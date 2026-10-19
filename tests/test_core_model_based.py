"""Deterministic state-machine checks against an independent integer oracle.

Default: 1000 mixed commands. Optional BRIVA_CORE_FUZZ_ITERATIONS and
BRIVA_CORE_FUZZ_SEED extend a local run. No production app, files, network,
drop_all or cleanup are used. Every run retains its seed and command trace.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import random
import re
import sys
from time import perf_counter
import traceback
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flask import Flask
from sqlalchemy import func, select
from extensions import db
from models import User
from briva_core import register_core, create_core_schema
from briva_core.models import (AccountMovement, AuditEvent, Package, Payment,
    PickAllocation, PickingEvent, PickingLine, PickingTask, RequestRecord,
    ReturnDocument, ReturnItem, Sale, SaleItem, StockBalance, StockMovement)


def cash(cents):
    """Integer-only oracle formatting; no application money helpers reused."""
    return str(cents // 100) + "." + str(cents % 100).zfill(2)


class CoreModelBasedTests(unittest.TestCase):
    def setUp(self):
        self.seed = int(os.environ.get("BRIVA_CORE_FUZZ_SEED", "8453187"), 0)
        self.iterations = int(os.environ.get("BRIVA_CORE_FUZZ_ITERATIONS", "1000"))
        if not 1 <= self.iterations <= 100000:
            raise ValueError("BRIVA_CORE_FUZZ_ITERATIONS must be between 1 and 100000")
        self.random = random.Random(self.seed)
        self.directory = ROOT / ".verification-data" / ("core-fuzz-" + uuid.uuid4().hex)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.app = Flask("core-fuzz-" + uuid.uuid4().hex)
        self.app.config.update(TESTING=True, SECRET_KEY="synthetic-fuzz-key", SQLALCHEMY_DATABASE_URI="sqlite:///:memory:")
        db.init_app(self.app)
        register_core(self.app)
        self.context = self.app.app_context()
        self.context.push()
        create_core_schema()
        User.__table__.create(db.engine, checkfirst=True)
        owner = User(username="oracle_owner", company="ORACLE", role="admin", password_hash="synthetic")
        db.session.add(owner)
        db.session.commit()
        self.owner_id = owner.id
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["user"] = self.owner_id
        self.calls = []
        self.request_counts = Counter()
        self.trace = []
        self.kinds = Counter()
        self.locations = [self.send("/locations", {"code": name, "name": name, "kind": "store"})["data"]["id"] for name in ("A", "B")]
        product = self.send("/catalog/models", {"model_code": "ORACLE", "name": "Independent integer oracle",
            "colors": [{"code": "SY", "name": "Siyah"}], "sizes": ["S", "M", "L", "XL"], "unit_prices": {"TRY": "1.99"}})["data"]
        self.variants = {row["id"]: row for row in product["variants"]}
        self.by_size = {row["size"]: row["id"] for row in product["variants"]}
        self.variant_ids = sorted(self.variants)
        self.recipe_parts = [(self.by_size[size], qty) for size, qty in (("S", 1), ("M", 2), ("L", 2), ("XL", 1))]
        self.recipe = self.send("/catalog/recipes", {"model_id": product["id"], "color_code": "SY", "name": "S1M2L2XL1",
            "components": [{"variant_id": variant, "quantity": qty} for variant, qty in self.recipe_parts]})["data"]
        self.customer_id = self.send("/customers", {"name": "Synthetic oracle customer"})["data"]["id"]
        self.stock = defaultdict(lambda: [0, 0, 0])  # physical, reserved, packaged
        self.account = []  # signed integer cents; debt positive, customer credit negative
        self.expected_payment_count = 0
        self.sales = {}  # sale_item_id -> independently expected data
        self.packages = {}  # package id -> status, location, variant, quantity
        self.tasks = {}  # exact task id -> state, location, variant, quantity, picked
        self.started = perf_counter()

    def tearDown(self):
        db.session.rollback()
        db.session.remove()
        db.engine.dispose()
        self.context.pop()

    def send(self, path, payload, expected=201, key=None, remember=True):
        key = key or uuid.uuid4().hex
        response = self.client.post("/api/v2" + path, json=payload, headers={
            "Idempotency-Key": key, "X-Briva-Actor": str(self.owner_id), "X-Briva-Company": "ORACLE"})
        body = response.get_json()
        canonical_path = re.sub(r"/\d+(?=/|$)", "/<id>", path)
        self.request_counts[str(response.status_code) + " " + canonical_path] += 1
        self.assertEqual(response.status_code, expected, {"path": path, "payload": payload, "body": body, "seed": self.seed})
        if expected == 201 and remember:
            self.calls.append((path, deepcopy(payload), key, deepcopy(body)))
        return body

    def available(self, location, variant):
        physical, reserved, packaged = self.stock[(location, variant)]
        return physical - reserved - packaged

    def candidates(self, minimum=1):
        return [(location, variant) for location in self.locations for variant in self.variant_ids if self.available(location, variant) >= minimum]

    def receive(self):
        location = self.random.choice(self.locations)
        variant = self.random.choice(self.variant_ids)
        count = self.random.randint(1, 7)
        self.send("/receipts", {"location_id": location, "reference": uuid.uuid4().hex,
            "items": [{"variant_id": variant, "quantity": count}]})
        self.stock[(location, variant)][0] += count

    def transfer(self):
        choices = self.candidates()
        if not choices:
            return self.receive()
        source, variant = self.random.choice(choices)
        target = next(value for value in self.locations if value != source)
        count = self.random.randint(1, min(5, self.available(source, variant)))
        self.send("/transfers", {"from_location_id": source, "to_location_id": target,
            "items": [{"variant_id": variant, "quantity": count}]})
        self.stock[(source, variant)][0] -= count
        self.stock[(target, variant)][0] += count

    def book_sale(self, result, location, expected_lines, paid):
        """Compare the result to precomputed oracle lines before retaining IDs."""
        self.assertEqual(len(result["items"]), len(expected_lines))
        total = sum(qty * unit for variant, qty, unit in expected_lines)
        self.assertEqual(result["total"], cash(total))
        self.assertEqual(result["paid"], cash(paid))
        for actual, expected in zip(result["items"], expected_lines):
            variant, count, unit = expected
            self.assertEqual((actual["variant_id"], actual["quantity"], actual["unit_price"]), (variant, count, cash(unit)))
            self.sales[actual["id"]] = {"sale_id": result["id"], "variant": variant, "quantity": count,
                "returned": 0, "unit": unit, "location": location}
        self.account.append(total)
        if paid:
            self.account.append(-paid)
            self.expected_payment_count += 1

    def sell(self, mode="unit"):
        location = self.random.choice(self.locations)
        if mode == "series":
            if not all(self.available(location, variant) >= qty for variant, qty in self.recipe_parts):
                return self.receive()
            expected = [(variant, qty, 199) for variant, qty in self.recipe_parts]
            items = [{"barcode": self.recipe["barcode"], "quantity": 1}]
        elif mode == "custom":
            parts = [(self.by_size[size], qty) for size, qty in (("S", 1), ("M", 3), ("L", 2))]
            if not all(self.available(location, variant) >= qty for variant, qty in parts):
                return self.receive()
            expected = [(variant, qty, 199) for variant, qty in parts]
            items = [{"barcode": self.variants[variant]["barcode"], "quantity": qty} for variant, qty in parts]
        else:
            choices = self.candidates(2 if mode == "repeat" else 1)
            if not choices:
                return self.receive()
            location, variant = self.random.choice(choices)
            if mode == "repeat":
                expected = [(variant, 1, 199), (variant, 1, 199)]
                items = [{"barcode": self.variants[variant]["barcode"], "quantity": 1} for _ in range(2)]
            else:
                count = self.random.randint(1, min(3, self.available(location, variant)))
                unit = self.random.choice((199, 199, 199, 123, 0))
                expected = [(variant, count, unit)]
                item = {"barcode": self.variants[variant]["barcode"], "quantity": count}
                if unit != 199:
                    item.update(unit_price=cash(unit), price_reason="Synthetic authorized override")
                items = [item]
        total = sum(qty * unit for _, qty, unit in expected)
        paid = self.random.choice((0, total, total // 2))
        result = self.send("/sales", {"location_id": location, "customer_id": self.customer_id, "currency": "TRY",
            "items": items, "expected_total": cash(total), "payment": {"amount": cash(paid), "method": "cash"}})["data"]
        for variant, qty, _ in expected:
            self.stock[(location, variant)][0] -= qty
        self.book_sale(result, location, expected, paid)

    def return_piece(self):
        choices = [(item_id, item) for item_id, item in self.sales.items() if item["returned"] < item["quantity"]]
        if not choices:
            return self.sell()
        item_id, item = self.random.choice(choices)
        count = self.random.randint(1, item["quantity"] - item["returned"])
        location = self.random.choice(self.locations)
        total = count * item["unit"]
        maximum_refund = min(total, max(0, total - sum(self.account)))
        refund = self.random.choice((0, maximum_refund))
        result = self.send(f'/sales/{item["sale_id"]}/returns', {"location_id": location,
            "reason": "Synthetic return", "items": [{"sale_item_id": item_id, "quantity": count}], "refund_amount": cash(refund)})["data"]
        self.assertEqual(result["total"], cash(total))
        item["returned"] += count
        self.stock[(location, item["variant"])][0] += count
        self.account.append(-total)
        if refund:
            self.account.append(refund)
            self.expected_payment_count += 1

    def payment(self):
        amount = self.random.randint(1, 20) * 37
        self.send("/payments", {"customer_id": self.customer_id, "currency": "TRY", "amount": cash(amount), "direction": "in", "method": "transfer"})
        self.account.append(-amount)
        self.expected_payment_count += 1

    def refund(self):
        credit = -sum(self.account)
        if credit <= 0:
            return self.reject("refund")
        amount = self.random.randint(1, credit)
        self.send("/payments", {"customer_id": self.customer_id, "currency": "TRY", "amount": cash(amount), "direction": "out", "method": "cash"})
        self.account.append(amount)
        self.expected_payment_count += 1

    def seal(self):
        choices = self.candidates()
        if not choices:
            return self.receive()
        location, variant = self.random.choice(choices)
        count = self.random.randint(1, min(3, self.available(location, variant)))
        result = self.send("/packages", {"location_id": location, "items": [{"variant_id": variant, "quantity": count}]})["data"]
        self.stock[(location, variant)][2] += count
        self.packages[result["id"]] = {"status": "sealed", "location": location, "variant": variant, "quantity": count, "barcode": result["barcode"]}

    def package_action(self, action):
        choices = [(package_id, package) for package_id, package in self.packages.items() if package["status"] == "sealed"]
        if not choices:
            return self.seal()
        package_id, package = self.random.choice(choices)
        location, variant, count = package["location"], package["variant"], package["quantity"]
        if action == "open":
            self.send(f'/packages/{package_id}/open', {"reason": "Synthetic open"})
            self.stock[(location, variant)][2] -= count
            package["status"] = "opened"
        elif action == "move":
            target = next(value for value in self.locations if value != location)
            self.send(f'/packages/{package_id}/transfer', {"to_location_id": target})
            self.stock[(location, variant)][0] -= count
            self.stock[(location, variant)][2] -= count
            self.stock[(target, variant)][0] += count
            self.stock[(target, variant)][2] += count
            package["location"] = target
        else:
            total, paid = count * 199, 0
            result = self.send("/sales", {"location_id": location, "customer_id": self.customer_id, "currency": "TRY",
                "items": [{"barcode": package["barcode"], "quantity": 1}], "expected_total": cash(total), "payment": {"amount": "0", "method": "cash"}})["data"]
            self.stock[(location, variant)][0] -= count
            self.stock[(location, variant)][2] -= count
            package["status"] = "sold"
            self.book_sale(result, location, [(variant, count, 199)], paid)

    def reserve(self):
        choices = self.candidates()
        if not choices:
            return self.receive()
        location, variant = self.random.choice(choices)
        count = self.random.randint(1, min(4, self.available(location, variant)))
        result = self.send("/picking/tasks", {"location_id": location, "items": [{"barcode": self.variants[variant]["barcode"], "quantity": count}]})["data"]
        self.stock[(location, variant)][1] += count
        self.tasks[result["id"]] = {"status": "picking", "location": location, "variant": variant, "quantity": count, "picked": 0}

    def task_action(self, action):
        choices = [(task_id, task) for task_id, task in self.tasks.items() if task["status"] in ("picking", "ready")]
        if not choices:
            return self.reserve()
        task_id, task = self.random.choice(choices)
        location, variant, count = task["location"], task["variant"], task["quantity"]
        if action == "cancel":
            self.send(f'/picking/{task_id}/cancel', {"reason": "Synthetic cancel"})
            self.stock[(location, variant)][1] -= count
            task["status"] = "cancelled"
        elif action == "scan" or task["status"] != "ready":
            if task["picked"] == count:
                return self.reject("pick_over", (task_id, task))
            amount = self.random.randint(1, count - task["picked"])
            result = self.send(f'/picking/{task_id}/scan', {"barcode": self.variants[variant]["barcode"], "quantity": amount})["data"]
            task["picked"] += amount
            task["status"] = "ready" if task["picked"] == count else "picking"
            self.assertEqual(result["status"], task["status"])
        else:
            total, paid = count * 199, self.random.choice((0, count * 199))
            result = self.send("/sales", {"location_id": location, "customer_id": self.customer_id, "currency": "TRY", "picking_task_id": task_id,
                "items": [{"barcode": self.variants[variant]["barcode"], "quantity": count}],
                "expected_total": cash(total), "payment": {"amount": cash(paid), "method": "cash"}})["data"]
            self.stock[(location, variant)][0] -= count
            self.stock[(location, variant)][1] -= count
            task["status"] = "completed"
            self.book_sale(result, location, [(variant, count, 199)], paid)

    def snapshot(self):
        """Full command-owned state, including no-op audit/idempotency changes."""
        stock = tuple(db.session.execute(select(StockBalance.location_id, StockBalance.variant_id,
            StockBalance.quantity, StockBalance.reserved, StockBalance.packaged).order_by(StockBalance.location_id, StockBalance.variant_id)).all())
        counts = tuple(db.session.execute(select(func.count(model.id))).scalar_one() for model in (
            Sale, SaleItem, ReturnDocument, ReturnItem, Payment, StockMovement, AccountMovement, AuditEvent, RequestRecord, PickingEvent))
        packages = tuple(db.session.execute(select(Package.id, Package.status, Package.location_id).order_by(Package.id)).all())
        tasks = tuple(db.session.execute(select(PickingTask.id, PickingTask.status).order_by(PickingTask.id)).all())
        lines = tuple(db.session.execute(select(PickingLine.id, PickingLine.picked).order_by(PickingLine.id)).all())
        allocations = tuple(db.session.execute(select(PickAllocation.id, PickAllocation.picked).order_by(PickAllocation.id)).all())
        balance = db.session.execute(select(func.coalesce(func.sum(AccountMovement.amount), 0))).scalar_one()
        return stock, counts, packages, tasks, lines, allocations, int(Decimal(balance) * 100)

    def replay(self, conflicting=False):
        path, payload, key, expected_body = self.random.choice(self.calls)
        payload = deepcopy(payload)
        if conflicting:
            payload["oracle_conflicting_body"] = True
        before = self.snapshot()
        result = self.send(path, payload, 409 if conflicting else 200, key, remember=False)
        if conflicting:
            self.assertEqual(result["error"]["code"], "idempotency_conflict")
        else:
            self.assertEqual(result["data"], expected_body["data"])
            self.assertTrue(result["replayed"])
        self.assertEqual(before, self.snapshot(), "Replay/reject mutated state")

    def reject(self, kind=None, extra=None):
        kind = kind or self.random.choice(("negative", "last_line", "overreserve"))
        location, variant = self.random.choice(self.locations), self.random.choice(self.variant_ids)
        expected = 400
        if kind == "refund":
            path, payload = "/payments", {"customer_id": self.customer_id, "currency": "TRY", "amount": cash(max(1, -sum(self.account) + 1)), "direction": "out", "method": "cash"}
            expected = 409
        elif kind == "pick_over":
            task_id, task = extra
            path, payload = f'/picking/{task_id}/scan', {"barcode": self.variants[task["variant"]]["barcode"], "quantity": task["quantity"] + 1}
            expected = 409
        elif kind == "last_line":
            last_variant = self.variant_ids[-1]
            first_variant = self.variant_ids[0]
            path, payload = "/sales", {"location_id": location, "customer_id": self.customer_id, "currency": "TRY",
                "items": [{"barcode": self.variants[first_variant]["barcode"], "quantity": 1},
                          {"barcode": self.variants[last_variant]["barcode"], "quantity": self.available(location, last_variant) + 1}],
                "payment": {"amount": "0", "method": "cash"}}
            expected = 409
        elif kind == "overreserve":
            path, payload = "/picking/tasks", {"location_id": location, "items": [{"barcode": self.variants[variant]["barcode"], "quantity": self.available(location, variant) + 1}]}
            expected = 409
        else:
            path, payload = "/receipts", {"location_id": location, "reference": uuid.uuid4().hex, "items": [{"variant_id": variant, "quantity": -1}]}
        before = self.snapshot()
        self.send(path, payload, expected, remember=False)
        self.assertEqual(before, self.snapshot(), "Rejected command mutated state")

    def assert_oracle(self):
        actual = {(row.location_id, row.variant_id): [row.quantity, row.reserved, row.packaged]
            for row in db.session.execute(select(StockBalance)).scalars()}
        for key in set(actual) | set(self.stock):
            expected = self.stock[key]
            self.assertEqual(actual.get(key, [0, 0, 0]), expected, {"key": key, "seed": self.seed})
            self.assertGreaterEqual(expected[0], expected[1] + expected[2])
            self.assertGreaterEqual(min(expected), 0)
        account_sum, account_count = db.session.execute(select(func.coalesce(func.sum(AccountMovement.amount), 0), func.count(AccountMovement.id))).one()
        self.assertEqual(int(Decimal(account_sum) * 100), sum(self.account))
        self.assertEqual(account_count, len(self.account))
        self.assertEqual(db.session.execute(select(func.count(Payment.id))).scalar_one(), self.expected_payment_count)
        actual_packages = {row.id: (row.status, row.location_id) for row in db.session.execute(select(Package)).scalars()}
        self.assertEqual(actual_packages, {key: (value["status"], value["location"]) for key, value in self.packages.items()})
        actual_tasks = {row.id: row.status for row in db.session.execute(select(PickingTask)).scalars()}
        self.assertEqual(actual_tasks, {key: value["status"] for key, value in self.tasks.items()})
        for row in db.session.execute(select(PickingLine)).scalars():
            oracle = self.tasks[row.task_id]
            self.assertEqual((row.variant_id, row.quantity, row.picked), (oracle["variant"], oracle["quantity"], oracle["picked"]))

    def test_mixed_commands_against_independent_integer_oracle(self):
        commands = {
            "receipt": self.receive, "transfer": self.transfer,
            "unit_sale": self.sell, "series_sale": lambda: self.sell("series"),
            "custom_sale": lambda: self.sell("custom"), "repeated_sale": lambda: self.sell("repeat"),
            "return": self.return_piece, "payment": self.payment, "refund": self.refund,
            "seal": self.seal, "open": lambda: self.package_action("open"),
            "package_transfer": lambda: self.package_action("move"), "package_sale": lambda: self.package_action("sale"),
            "reserve": self.reserve, "pick": lambda: self.task_action("scan"),
            "cancel": lambda: self.task_action("cancel"), "picked_sale": lambda: self.task_action("sale"),
            "replay": self.replay, "key_conflict": lambda: self.replay(True), "reject": self.reject,
        }
        success, failure = False, None
        try:
            # Seed physical inventory through the same acceptance API.
            for _ in range(24):
                self.receive()
            for step in range(self.iterations):
                name = self.random.choice(tuple(commands))
                self.kinds[name] += 1
                commands[name]()
                self.assert_oracle()
                if step % 20 == 0 or step == self.iterations - 1:
                    response = self.client.get("/api/v2/health")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.get_json()["data"]["status"], "ok", response.get_json())
                self.trace.append({"step": step, "command": name, "physical": sum(row[0] for row in self.stock.values()),
                    "reserved": sum(row[1] for row in self.stock.values()), "packaged": sum(row[2] for row in self.stock.values()),
                    "customer_balance_cents": sum(self.account)})
            success = True
        except Exception:
            failure = traceback.format_exc()
            raise
        finally:
            elapsed = perf_counter() - self.started
            with (self.directory / "oracle-report.json").open("x", encoding="utf-8") as output:
                json.dump({"timestamp_utc": datetime.now(timezone.utc).isoformat(), "seed": self.seed,
                    "requested_steps": self.iterations, "completed_steps": len(self.trace), "successful": success,
                    "seconds": round(elapsed, 3), "requested_command_counts": dict(self.kinds),
                    "actual_http_outcomes": dict(self.request_counts), "failure": failure,
                    "trace": self.trace, "limitations": "Synthetic SQLite only; not PostgreSQL, printer or scanner verification"}, output, indent=2)
            print("Model-based evidence:", self.directory / "oracle-report.json")


if __name__ == "__main__":
    unittest.main(verbosity=2)
