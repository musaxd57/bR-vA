"""Bounded API read budgets with 1000 variants and 50,000 stock rows.

This is a deterministic synthetic query-count and payload-size regression,
not a production throughput claim. Timings are retained as observations only.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from time import perf_counter
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flask import Flask
from sqlalchemy import event
from extensions import db
from models import User
from briva_core import register_core, create_core_schema
from briva_core.api import ALL_PERMISSIONS
from briva_core import service as s
from briva_core.models import StockBalance, StockMovement


class CoreReadBudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask("core-scale-" + uuid.uuid4().hex)
        cls.app.config.update(TESTING=True, SECRET_KEY="synthetic-scale-key",
                               SQLALCHEMY_DATABASE_URI="sqlite:///:memory:")
        db.init_app(cls.app)
        register_core(cls.app)
        cls.context = cls.app.app_context()
        cls.context.push()
        create_core_schema()
        User.__table__.create(db.engine, checkfirst=True)
        user = User(username="scale_owner", company="SCALE", role="admin", password_hash="synthetic")
        db.session.add(user)
        db.session.commit()
        cls.user_id = user.id
        actor = s.Actor("SCALE", user.id, ALL_PERMISSIONS)
        model = s.create_model(actor, {"model_code": "SCALE-1000", "name": "Scale fixture",
            "colors": [{"code": "C" + str(index), "name": "Color " + str(index)} for index in range(100)],
            "sizes": [str(index) for index in range(10)], "unit_prices": {"TRY": "100"}})
        cls.variants = model["variants"]
        cls.locations = [s.create_location(actor, {"code": "L" + str(index), "name": "Location " + str(index), "kind": "store"}) for index in range(50)]
        for index in range(20):
            rows = [row for row in cls.variants if row["color_code"] == "C" + str(index)]
            s.create_recipe(actor, {"model_id": model["id"], "color_code": "C" + str(index), "name": "Standart",
                "components": [{"variant_id": row["id"], "quantity": 1} for row in rows]})
        balances = [{"company_id": "SCALE", "variant_id": variant["id"], "location_id": location["id"], "quantity": 1, "reserved": 0, "packaged": 0}
                    for location in cls.locations for variant in cls.variants]
        db.session.execute(StockBalance.__table__.insert(), balances)
        db.session.execute(StockMovement.__table__.insert(), [
            {"company_id": "SCALE", "variant_id": row["variant_id"], "location_id": row["location_id"],
             "quantity_delta": 1, "reserved_delta": 0, "packaged_delta": 0, "kind": "synthetic_fixture",
             "document_type": "synthetic_fixture", "document_id": 1, "actor_id": cls.user_id} for row in balances])
        db.session.commit()
        cls.client = cls.app.test_client()
        with cls.client.session_transaction() as sess:
            sess["user"] = cls.user_id
        cls.metrics = []

    @classmethod
    def tearDownClass(cls):
        directory = ROOT / ".verification-data" / ("core-performance-" + uuid.uuid4().hex)
        directory.mkdir(parents=True, exist_ok=False)
        with (directory / "metrics.json").open("x", encoding="utf-8") as output:
            json.dump({"timestamp_utc": datetime.now(timezone.utc).isoformat(),
                       "fixture": {"variants": 1000, "locations": 50, "stock_rows": 50000, "recipes": 20},
                       "measurements": cls.metrics,
                       "limitations": "SQLite memory fixture; not a production throughput or PostgreSQL benchmark"}, output, indent=2)
        print("Read budget evidence:", directory / "metrics.json")
        db.session.remove()
        db.engine.dispose()
        cls.context.pop()

    def measured(self, path):
        count = 0
        def before_cursor(*args):
            nonlocal count
            count += 1
        engine = db.engine
        event.listen(engine, "before_cursor_execute", before_cursor)
        start = perf_counter()
        try:
            response = self.client.get("/api/v2" + path)
        finally:
            elapsed = perf_counter() - start
            event.remove(engine, "before_cursor_execute", before_cursor)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.metrics.append({"path": path, "queries": count, "seconds": round(elapsed, 6), "response_bytes": len(response.data)})
        return response.get_json()["data"], count, len(response.data)

    def test_bootstrap_has_no_per_variant_or_per_location_queries(self):
        data, queries, size = self.measured("/bootstrap")
        self.assertEqual(len(data["models"][0]["variants"]), 1000)
        self.assertEqual(len(data["locations"]), 50)
        self.assertLessEqual(queries, 26)
        self.assertLess(size, 500_000)

    def test_stock_is_paginated_but_totals_cover_all_fifty_thousand_rows(self):
        data, queries, size = self.measured("/stock")
        self.assertEqual(len(data["balances"]), 1000)
        self.assertEqual(data["pagination"]["total_balances"], 50000)
        self.assertEqual(data["totals"]["quantity"], 50000)
        self.assertTrue(data["pagination"]["has_more"])
        self.assertTrue(data["capacities_truncated"])
        self.assertEqual(len(data["capacities"]), 500)
        self.assertTrue(all(row["series_capacity"] == 1 for row in data["capacities"]))
        self.assertLessEqual(queries, 10)
        self.assertLess(size, 1_000_000)

    def test_filtered_stock_capacity_and_totals_are_complete(self):
        data, queries, size = self.measured("/stock?location_id=" + str(self.locations[-1]["id"]))
        self.assertEqual(data["totals"]["quantity"], 1000)
        self.assertEqual(len(data["balances"]), 1000)
        self.assertFalse(data["pagination"]["has_more"])
        self.assertFalse(data["capacities_truncated"])
        self.assertEqual(len(data["capacities"]), 20)
        self.assertLessEqual(queries, 11)

    def test_reconciliation_remains_bounded_in_query_count(self):
        data, queries, size = self.measured("/health")
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["checked_balance_count"], 50000)
        self.assertLessEqual(queries, 8)
        self.assertLess(size, 1000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
