"""Two real SQLite connections contend for the same inventory.

Evidence databases are deliberately retained in .verification-data; no cleanup
or original database access occurs. PostgreSQL verification is still a separate
pre-deployment requirement and is not implied by these SQLite checks.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import threading
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flask import Flask
from sqlalchemy import select, func
from extensions import db
from models import User
from briva_core import create_core_schema, register_core
from briva_core.models import Sale, StockBalance, AccountMovement


class CoreConcurrencyTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".verification-data" / ("core-concurrency-" + uuid.uuid4().hex)
        directory.mkdir(parents=True, exist_ok=False)
        self.app = Flask("concurrent-core-" + uuid.uuid4().hex)
        self.app.config.update(TESTING=True, SECRET_KEY="synthetic-concurrency-key",
            SQLALCHEMY_DATABASE_URI="sqlite:///" + (directory / "synthetic.db").as_posix(),
            SQLALCHEMY_ENGINE_OPTIONS={"connect_args": {"timeout": 15}})
        db.init_app(self.app)
        register_core(self.app)
        with self.app.app_context():
            create_core_schema()
            User.__table__.create(db.engine, checkfirst=True)
            owner = User(username="concurrent_owner", company="CONCURRENT", role="admin", password_hash="synthetic")
            db.session.add(owner)
            db.session.commit()
            self.owner_id = owner.id
        self.location = self.send("/locations", {"code": "L", "name": "Location", "kind": "store"})[1]["data"]
        product = self.send("/catalog/models", {"model_code": "C-1", "name": "Concurrent",
            "colors": [{"code": "SY", "name": "Siyah"}], "sizes": ["M"], "unit_prices": {"TRY": "100"}})[1]["data"]
        self.variant = product["variants"][0]

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()

    def send(self, path, payload, key=None, barrier=None):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["user"] = self.owner_id
        if barrier:
            barrier.wait(timeout=15)
        response = client.post("/api/v2" + path, json=payload,
                               headers={"Idempotency-Key": key or uuid.uuid4().hex})
        return response.status_code, response.get_json()

    def receive(self, count=1):
        status, body = self.send("/receipts", {"location_id": self.location["id"], "reference": uuid.uuid4().hex,
            "items": [{"variant_id": self.variant["id"], "quantity": count}]})
        self.assertEqual(status, 201, body)

    def sale_payload(self, customer_id=None, paid="100"):
        return {"location_id": self.location["id"], "currency": "TRY", "customer_id": customer_id,
                "items": [{"barcode": self.variant["barcode"], "quantity": 1}],
                "payment": {"amount": paid, "method": "cash"}}

    def race(self, first, second):
        barrier = threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(self.send, path, payload, key, barrier)
                       for path, payload, key in (first, second)]
            return [future.result(timeout=30) for future in futures]

    def balance(self):
        with self.app.app_context():
            row = db.session.execute(select(StockBalance).where(StockBalance.variant_id == self.variant["id"])).scalar_one()
            return row.quantity, row.reserved

    def test_last_piece_sold_once_under_two_real_connections(self):
        self.receive(1)
        results = self.race(("/sales", self.sale_payload(), uuid.uuid4().hex),
                            ("/sales", self.sale_payload(), uuid.uuid4().hex))
        self.assertEqual(sorted(status for status, _ in results), [201, 409], results)
        self.assertEqual(self.balance(), (0, 0))
        with self.app.app_context():
            self.assertEqual(db.session.execute(select(func.count(Sale.id))).scalar_one(), 1)

    def test_simultaneous_same_key_replays_one_sale(self):
        self.receive(2)
        key = uuid.uuid4().hex
        results = self.race(("/sales", self.sale_payload(), key), ("/sales", self.sale_payload(), key))
        self.assertEqual(sorted(status for status, _ in results), [200, 201], results)
        self.assertEqual(results[0][1]["data"]["id"], results[1][1]["data"]["id"])
        self.assertEqual(self.balance(), (1, 0))

    def test_simultaneous_same_key_different_payload_conflicts(self):
        self.receive(3)
        key = uuid.uuid4().hex
        different = self.sale_payload()
        different["items"][0]["quantity"] = 2
        different["payment"]["amount"] = "200"
        results = self.race(("/sales", self.sale_payload(), key), ("/sales", different, key))
        self.assertEqual(sorted(status for status, _ in results), [201, 409], results)
        self.assertIn(self.balance()[0], (1, 2))

    def test_concurrent_first_receipts_do_not_lose_stock(self):
        base = {"location_id": self.location["id"], "items": [{"variant_id": self.variant["id"], "quantity": 3}]}
        results = self.race(("/receipts", {**base, "reference": "FIRST"}, uuid.uuid4().hex),
                            ("/receipts", {**base, "reference": "SECOND"}, uuid.uuid4().hex))
        self.assertEqual([status for status, _ in results], [201, 201], results)
        self.assertEqual(self.balance(), (6, 0))

    def test_concurrent_reservations_cannot_overallocate(self):
        self.receive(10)
        payload = {"location_id": self.location["id"], "items": [{"barcode": self.variant["barcode"], "quantity": 8}]}
        results = self.race(("/picking/tasks", payload, uuid.uuid4().hex),
                            ("/picking/tasks", payload, uuid.uuid4().hex))
        self.assertEqual(sorted(status for status, _ in results), [201, 409], results)
        self.assertEqual(self.balance(), (10, 8))

    def test_concurrent_returns_cannot_return_same_piece_twice(self):
        self.receive(1)
        status, body = self.send("/sales", self.sale_payload())
        self.assertEqual(status, 201, body)
        sale = body["data"]
        payload = {"location_id": self.location["id"], "reason": "Synthetic return", "refund_amount": "100",
                   "items": [{"sale_item_id": sale["items"][0]["id"], "quantity": 1}]}
        path = f'/sales/{sale["id"]}/returns'
        results = self.race((path, payload, uuid.uuid4().hex), (path, payload, uuid.uuid4().hex))
        self.assertEqual(sorted(status for status, _ in results), [201, 409], results)
        self.assertEqual(self.balance(), (1, 0))

    def test_concurrent_refunds_cannot_spend_customer_credit_twice(self):
        self.receive(1)
        status, customer = self.send("/customers", {"name": "Concurrent customer"})
        self.assertEqual(status, 201, customer)
        customer_id = customer["data"]["id"]
        status, body = self.send("/sales", self.sale_payload(customer_id, "60"))
        self.assertEqual(status, 201, body)
        sale = body["data"]
        status, body = self.send(f'/sales/{sale["id"]}/returns', {"location_id": self.location["id"],
            "reason": "Synthetic", "items": [{"sale_item_id": sale["items"][0]["id"], "quantity": 1}]})
        self.assertEqual(status, 201, body)
        payload = {"customer_id": customer_id, "currency": "TRY", "amount": "60", "direction": "out", "method": "cash"}
        results = self.race(("/payments", payload, uuid.uuid4().hex), ("/payments", payload, uuid.uuid4().hex))
        self.assertEqual(sorted(status for status, _ in results), [201, 409], results)
        with self.app.app_context():
            balance = db.session.execute(select(func.sum(AccountMovement.amount)).where(AccountMovement.customer_id == customer_id)).scalar_one()
            self.assertEqual(str(balance), "0.00")

    def test_two_stock_counts_cannot_both_overwrite_same_snapshot(self):
        self.receive(10)
        base = {"location_id": self.location["id"], "reason": "İki sayımın çakışması"}
        first = {**base, "reference": "COUNT-A", "items": [{"variant_id": self.variant["id"], "expected_quantity": 10, "counted_quantity": 8}]}
        second = {**base, "reference": "COUNT-B", "items": [{"variant_id": self.variant["id"], "expected_quantity": 10, "counted_quantity": 7}]}
        results = self.race(("/stock-counts", first, uuid.uuid4().hex), ("/stock-counts", second, uuid.uuid4().hex))
        self.assertEqual(sorted(status for status, _ in results), [201, 409], results)
        self.assertIn(self.balance()[0], (7, 8))

    def test_two_descendant_tasks_cannot_overallocate_same_shelves(self):
        shelves = []
        for name in ("A", "B"):
            status, body = self.send("/locations", {"code": name, "name": name, "kind": "shelf", "parent_id": self.location["id"]})
            self.assertEqual(status, 201, body)
            shelves.append(body["data"])
            status, body = self.send("/receipts", {"location_id": shelves[-1]["id"], "reference": "SHELF-" + name,
                "items": [{"variant_id": self.variant["id"], "quantity": 5}]})
            self.assertEqual(status, 201, body)
        payload = {"location_id": self.location["id"], "include_descendants": True,
                   "items": [{"barcode": self.variant["barcode"], "quantity": 8}]}
        results = self.race(("/picking/tasks", payload, uuid.uuid4().hex), ("/picking/tasks", payload, uuid.uuid4().hex))
        self.assertEqual(sorted(status for status, _ in results), [201, 409], results)
        with self.app.app_context():
            rows = db.session.execute(select(StockBalance).where(StockBalance.variant_id == self.variant["id"])).scalars().all()
            self.assertEqual(sum(row.quantity for row in rows), 10)
            self.assertEqual(sum(row.reserved for row in rows), 8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
