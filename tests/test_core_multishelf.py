"""A branch may reserve its own shelves; other branches remain isolated."""
from pathlib import Path
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flask import Flask
from sqlalchemy import select
from extensions import db
from models import User
from briva_core import create_core_schema, register_core
from briva_core.models import Sale


class CoreMultiShelfTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask("shelves-" + uuid.uuid4().hex)
        self.app.config.update(TESTING=True, SECRET_KEY="synthetic-shelves", SQLALCHEMY_DATABASE_URI="sqlite:///:memory:")
        db.init_app(self.app)
        register_core(self.app)
        self.context = self.app.app_context()
        self.context.push()
        create_core_schema()
        User.__table__.create(db.engine, checkfirst=True)
        self.owner = User(username="shelf_owner", role="admin", company="SHELVES", password_hash="synthetic")
        self.worker = User(username="shelf_worker", role="warehouse", company="SHELVES", password_hash="synthetic", can_orders=True)
        db.session.add_all([self.owner, self.worker])
        db.session.commit()
        self.owner_id, self.worker_id = self.owner.id, self.worker.id
        self.client = self.app.test_client()
        self.login(self.owner_id)
        self.galata = self.location("G", "store")
        self.a = self.location("A", "shelf", self.galata["id"])
        self.b = self.location("B", "shelf", self.galata["id"])
        self.merter = self.location("MERTER", "store")
        self.c = self.location("C", "shelf", self.merter["id"])
        self.quarantine = self.location("Q", "quarantine", self.galata["id"])
        self.product = self.post("/catalog/models", {"model_code": "SHELF-MODEL", "name": "Raflı gömlek",
            "colors": [{"code": "SY", "name": "Siyah"}], "sizes": ["S", "M", "L", "XL"], "unit_prices": {"TRY": "100"}})
        self.variants = {row["size"]: row for row in self.product["variants"]}
        self.recipe = self.post("/catalog/recipes", {"model_id": self.product["id"], "color_code": "SY", "name": "Standart",
            "components": [{"variant_id": self.variants[size]["id"], "quantity": qty} for size, qty in (("S", 1), ("M", 2), ("L", 2), ("XL", 1))]})

    def tearDown(self):
        db.session.rollback()
        db.session.remove()
        db.engine.dispose()
        self.context.pop()

    def login(self, user_id):
        with self.client.session_transaction() as sess:
            sess["user"] = user_id

    def post(self, path, payload, status=201):
        response = self.client.post("/api/v2" + path, json=payload, headers={"Idempotency-Key": uuid.uuid4().hex})
        self.assertEqual(response.status_code, status, response.get_json())
        return response.get_json().get("data", response.get_json())

    def get(self, path):
        response = self.client.get("/api/v2" + path)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()["data"]

    def location(self, code, kind, parent_id=None):
        return self.post("/locations", {"code": code, "name": code, "kind": kind, "parent_id": parent_id})

    def receive(self, location, counts):
        return self.post("/receipts", {"location_id": location["id"], "reference": uuid.uuid4().hex,
            "items": [{"variant_id": self.variants[size]["id"], "quantity": qty} for size, qty in counts.items()]})

    def split_stock(self):
        self.receive(self.a, {"S": 1, "M": 1})
        self.receive(self.b, {"M": 1, "L": 2, "XL": 1})

    def task_payload(self, descendants=True):
        return {"location_id": self.galata["id"], "include_descendants": descendants,
                "items": [{"barcode": self.recipe["barcode"], "quantity": 1}]}

    def scan(self, task, code, quantity=1, status=201):
        return self.post(f'/picking/{task["id"]}/scan', {"barcode": code, "quantity": quantity}, status)

    def complete_split_task(self, task):
        self.scan(task, self.a["barcode"])
        self.scan(task, self.variants["S"]["barcode"])
        self.scan(task, self.variants["M"]["barcode"])
        self.scan(task, self.b["barcode"])
        self.scan(task, self.variants["M"]["barcode"])
        self.scan(task, self.variants["L"]["barcode"], 2)
        return self.scan(task, self.variants["XL"]["barcode"])

    def test_default_exact_scope_remains_and_descendants_is_explicit(self):
        self.split_stock()
        self.post("/picking/tasks", self.task_payload(False), 409)
        task = self.post("/picking/tasks", self.task_payload())
        self.assertTrue(task["include_descendants"])
        self.assertEqual(sum(row["quantity"] for row in task["allocations"]), 6)
        self.assertEqual({row["location_id"] for row in task["allocations"]}, {self.a["id"], self.b["id"]})
        self.assertTrue(all(row["barcode"] for row in task["lines"]))
        self.assertEqual(self.get("/health")["status"], "ok")

    def test_sale_consumes_actual_shelves_and_preserves_source_snapshot(self):
        self.split_stock()
        self.receive(self.c, {"M": 10})
        task = self.post("/picking/tasks", self.task_payload())
        ready = self.complete_split_task(task)
        self.assertEqual(ready["status"], "ready")
        sale = self.post("/sales", {**self.task_payload(), "currency": "TRY", "picking_task_id": task["id"],
            "payment": {"amount": "600", "method": "cash"}})
        stock = self.get("/stock")
        self.assertEqual(stock["totals"]["quantity"], 10)
        self.assertEqual(stock["totals"]["reserved"], 0)
        self.assertTrue(all(row["quantity"] == 0 for row in stock["balances"] if row["location_id"] in (self.a["id"], self.b["id"])))
        snapshot = db.session.get(Sale, sale["id"]).snapshot
        self.assertEqual({row["location_id"] for row in snapshot["allocations"]}, {self.a["id"], self.b["id"]})
        self.assertEqual(self.get("/health")["status"], "ok")

    def test_merter_inventory_does_not_complete_galata_series(self):
        self.receive(self.a, {"S": 1, "L": 2, "XL": 1})
        self.receive(self.c, {"M": 2})
        self.post("/picking/tasks", self.task_payload(), 409)
        self.assertEqual(self.get("/stock")["totals"]["reserved"], 0)

    def test_quarantine_and_nested_branch_roots_are_excluded(self):
        self.receive(self.a, {"S": 1, "L": 2, "XL": 1})
        self.receive(self.quarantine, {"M": 2})
        nested = self.location("NESTED_BRANCH", "store", self.galata["id"])
        self.receive(nested, {"M": 2})
        self.post("/picking/tasks", self.task_payload(), 409)
        self.assertEqual(self.get("/stock")["totals"]["reserved"], 0)

    def test_scan_is_bound_to_actual_source_shelf_and_exact_remaining_quantity(self):
        self.split_stock()
        task = self.post("/picking/tasks", self.task_payload())
        self.scan(task, self.galata["barcode"], status=409)
        self.scan(task, self.c["barcode"], status=409)
        self.scan(task, self.variants["M"]["barcode"], status=409)
        self.scan(task, self.a["barcode"])
        self.scan(task, self.variants["XL"]["barcode"], status=409)
        self.scan(task, self.variants["M"]["barcode"], quantity=2, status=409)
        result = self.scan(task, self.variants["M"]["barcode"])
        m_lines = [row for row in result["allocations"] if row["variant_id"] == self.variants["M"]["id"]]
        self.assertEqual([row["picked"] for row in m_lines], [1, 0])

    def test_partial_pick_cancel_releases_every_source_without_stock_change(self):
        self.split_stock()
        task = self.post("/picking/tasks", self.task_payload())
        self.scan(task, self.a["barcode"])
        self.scan(task, self.variants["S"]["barcode"])
        self.post(f'/picking/{task["id"]}/cancel', {"reason": "Vazgeçildi"})
        totals = self.get("/stock")["totals"]
        self.assertEqual((totals["quantity"], totals["reserved"]), (6, 0))
        self.assertEqual(self.get("/health")["status"], "ok")

    def test_each_actor_has_their_own_latest_verified_shelf(self):
        self.split_stock()
        task = self.post("/picking/tasks", self.task_payload())
        self.scan(task, self.a["barcode"])
        self.login(self.worker_id)
        self.scan(task, self.variants["S"]["barcode"], status=409)
        self.scan(task, self.b["barcode"])
        worker_task = self.get("/picking")["tasks"][0]
        self.assertEqual(worker_task["active_location_id"], self.b["id"])
        self.login(self.owner_id)
        owner_task = self.get("/picking")["tasks"][0]
        self.assertEqual(owner_task["active_location_id"], self.a["id"])

    def test_descendant_scope_requires_store_or_warehouse_and_boolean(self):
        self.split_stock()
        payload = self.task_payload()
        payload["include_descendants"] = "true"
        self.post("/picking/tasks", payload, 400)
        payload["include_descendants"], payload["location_id"] = True, self.a["id"]
        self.post("/picking/tasks", payload, 400)

    def test_new_recipe_version_does_not_change_reserved_shelf_contents(self):
        self.split_stock()
        task = self.post("/picking/tasks", self.task_payload())
        self.post("/catalog/recipes", {"model_id": self.product["id"], "color_code": "SY", "name": "Standart",
            "components": [{"variant_id": self.variants["M"]["id"], "quantity": 3}]})
        ready = self.complete_split_task(task)
        self.assertEqual(sum(row["quantity"] for row in ready["lines"]), 6)

    def test_stock_rollup_is_one_branch_only_with_explicit_collectable_capacity(self):
        self.split_stock()
        self.receive(self.quarantine, {"M": 5})
        self.receive(self.c, {"M": 10})
        exact = self.get("/stock?location_id=" + str(self.galata["id"]))
        self.assertEqual(exact["capacities"][0]["series_capacity"], 0)
        scoped = self.get("/stock?location_id=" + str(self.galata["id"]) + "&include_descendants=true")
        self.assertEqual(scoped["capacities"][0]["series_capacity"], 1)
        self.assertTrue(scoped["capacities"][0]["allocation_required"])
        self.assertEqual(scoped["capacities"][0]["capacity_type"], "collectable_from_shelves")
        self.assertEqual((scoped["totals"]["quantity"], scoped["totals"]["available"], scoped["totals"]["quarantined"]), (11, 6, 5))
        self.assertEqual({row["location_id"] for row in scoped["balances"]}, {self.a["id"], self.b["id"], self.quarantine["id"]})
        self.assertTrue(scoped["scope"]["quarantine_in_physical_total"])
        self.assertNotIn(self.quarantine["id"], scoped["scope"]["sellable_location_ids"])
        other = self.get("/stock?location_id=" + str(self.merter["id"]) + "&include_descendants=true")
        self.assertEqual(other["capacities"][0]["series_capacity"], 0)

    def test_global_or_shelf_rollup_is_rejected_and_nested_branch_is_not_counted(self):
        self.split_stock()
        nested = self.location("OTHER_BRANCH", "store", self.galata["id"])
        self.receive(nested, {"M": 99})
        for path in ("/stock?include_descendants=true", "/stock?location_id=" + str(self.a["id"]) + "&include_descendants=true"):
            self.assertEqual(self.client.get("/api/v2" + path).status_code, 400)
        scoped = self.get("/stock?location_id=" + str(self.galata["id"]) + "&include_descendants=true")
        self.assertEqual(scoped["totals"]["quantity"], 6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
