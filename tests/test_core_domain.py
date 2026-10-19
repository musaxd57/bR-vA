"""End-to-end domain regressions; isolated Flask + in-memory SQLite only.

No original app import, dotenv, external requests, file removal or drop_all.
Run: python -m unittest discover -s tests -p 'test_core_*.py' -v
"""
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
import sys
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flask import Flask
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import User
from briva_core import create_core_schema, register_core
from briva_core import service as s
from briva_core.models import (AccountMovement, AuditEvent, Barcode, CatalogModel,
    Location, Package, Payment, Recipe, RequestRecord, ReturnItem, Sale,
    SaleItem, StockBalance, StockMovement, Variant)
from briva_core.labels import PATTERNS, code128_symbols, code128_svg


class CoreDomainTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask("briva-core-domain-" + uuid.uuid4().hex)
        self.app.config.update(TESTING=True, SECRET_KEY="synthetic-core-key",
                               SQLALCHEMY_DATABASE_URI="sqlite:///:memory:")
        db.init_app(self.app)
        register_core(self.app)
        self.context = self.app.app_context()
        self.context.push()
        self.tables = create_core_schema()
        User.__table__.create(db.engine, checkfirst=True)
        self.owner = User(username="core_owner", company="CORE_TEST", role="admin", password_hash="not-a-live-password")
        self.other = User(username="other_owner", company="OTHER_TEST", role="admin", password_hash="not-a-live-password")
        self.cashier = User(username="core_cashier", company="CORE_TEST", role="cashier", password_hash="not-a-live-password", can_make_sale=True)
        db.session.add_all([self.owner, self.other, self.cashier])
        db.session.commit()
        self.owner_id, self.other_id, self.cashier_id = self.owner.id, self.other.id, self.cashier.id
        self.client = self.app.test_client()
        self.login(self.owner_id)
        self.location = self.post("/locations", {"code": "GALATA", "name": "Galata kabul", "kind": "receiving"})["data"]
        self.product = self.post("/catalog/models", {"model_code": "BRV-125", "name": "Sentetik gömlek",
            "colors": [{"code": "SY", "name": "Siyah"}], "sizes": ["S", "M", "L", "XL"],
            "unit_prices": {"TRY": "100.00", "USD": "10.00", "EUR": "9.00"}})["data"]
        self.variants = {row["size"]: row for row in self.product["variants"]}
        self.recipe = self.post("/catalog/recipes", {"model_id": self.product["id"], "color_code": "SY",
            "name": "Standart", "components": [{"variant_id": self.variants[size]["id"], "quantity": qty}
                for size, qty in (("S", 1), ("M", 2), ("L", 2), ("XL", 1))]})["data"]

    def tearDown(self):
        db.session.rollback()
        db.session.remove()
        db.engine.dispose()
        self.context.pop()

    def login(self, user_id):
        with self.client.session_transaction() as sess:
            sess["user"] = user_id
            # Deliberately false cached tenant/role: v2 must ignore both.
            sess["company"] = "FORGED_SESSION_COMPANY"
            sess["role"] = "admin"

    def post(self, path, payload, status=201, key=None, method="post"):
        response = getattr(self.client, method)("/api/v2" + path, json=payload,
            headers={"Idempotency-Key": key or uuid.uuid4().hex})
        self.assertEqual(response.status_code, status, response.get_json())
        return response.get_json()

    def stock(self):
        response = self.client.get("/api/v2/stock")
        self.assertEqual(response.status_code, 200)
        return response.get_json()["data"]

    def receive(self, counts=None, reference=None):
        counts = counts or {"S": 10, "M": 20, "L": 20, "XL": 10}
        return self.post("/receipts", {"location_id": self.location["id"],
            "reference": reference or uuid.uuid4().hex,
            "items": [{"variant_id": self.variants[size]["id"], "quantity": count} for size, count in counts.items()]})["data"]

    def counts(self, location_id=None):
        location_id = location_id or self.location["id"]
        by_id = {row["variant_id"]: row["quantity"] for row in self.stock()["balances"] if row["location_id"] == location_id}
        return {size: by_id.get(row["id"], 0) for size, row in self.variants.items()}

    def sale_payload(self, items=None, paid="600.00", customer_id=None):
        return {"location_id": self.location["id"], "currency": "TRY", "customer_id": customer_id,
                "items": items or [{"barcode": self.recipe["barcode"], "quantity": 1}],
                "payment": {"amount": paid, "method": "cash"}}

    def test_additive_schema_does_not_touch_legacy_tables(self):
        self.assertTrue(all(name.startswith("briva_") for name in self.tables))
        self.assertEqual(create_core_schema(), self.tables)
        self.assertEqual(db.session.execute(select(func_count(User))).scalar(), 3)

    def test_core_schema_list_is_exactly_its_models_not_other_briva_modules(self):
        from sqlalchemy import Column, Integer, Table, inspect
        from briva_core import models as core_models
        expected = {value.__table__.name for value in vars(core_models).values()
            if isinstance(value, type) and value.__module__ == core_models.__name__ and hasattr(value, "__table__")}
        self.assertEqual(expected, set(core_models.CORE_TABLE_NAMES))
        if "briva_external_fixture" not in db.metadata.tables:
            Table("briva_external_fixture", db.metadata, Column("id", Integer, primary_key=True))
        tables = create_core_schema()
        self.assertNotIn("briva_external_fixture", tables)
        self.assertFalse(inspect(db.engine).has_table("briva_external_fixture"))

    def test_three_colors_four_sizes_create_twelve_distinct_unit_barcodes(self):
        result = self.post("/catalog/models", {"model_code": "BRV-126", "name": "Twelve",
            "colors": [{"code": code, "name": code} for code in ("SY", "BY", "MV")],
            "sizes": ["S", "M", "L", "XL"], "unit_prices": {"TRY": "1.00"}})["data"]
        self.assertEqual(len(result["variants"]), 12)
        self.assertEqual(len(set(row["barcode"] for row in result["variants"])), 12)
        self.assertEqual(self.stock()["totals"]["quantity"], 0)

    def test_barcodes_stay_same_after_ten_model_and_price_edits(self):
        codes = [row["barcode"] for row in self.product["variants"]]
        for number in range(10):
            result = self.post("/catalog/models/" + str(self.product["id"]),
                {"name": "Yeni ad " + str(number)}, method="patch")["data"]
            self.assertEqual([row["barcode"] for row in result["variants"]], codes)
            self.post(f'/catalog/variants/{self.variants["M"]["id"]}/prices',
                {"prices": {"TRY": str(number + 1)}, "reason": "Fiyat güncelleme"}, method="patch")
        self.assertEqual(self.client.get("/api/v2/barcodes/" + codes[1]).get_json()["data"]["size"], "M")

    def test_model_code_identity_cannot_be_rewritten(self):
        self.post(f'/catalog/models/{self.product["id"]}', {"model_code": "DIFFERENT"}, status=400, method="patch")

    def test_barcode_resolves_kind_and_exact_components(self):
        unit = self.client.get("/api/v2/barcodes/" + self.variants["M"]["barcode"]).get_json()["data"]
        recipe = self.client.get("/api/v2/barcodes/" + self.recipe["barcode"]).get_json()["data"]
        self.assertEqual((unit["kind"], unit["pieces_per_unit"], unit["size"]), ("unit", 1, "M"))
        self.assertEqual((recipe["kind"], recipe["pieces_per_unit"], recipe["prices"]["TRY"]), ("recipe", 6, "600.00"))

    def test_production_plan_and_label_render_create_no_stock(self):
        self.post("/production/plans", {"reference": "P-1", "workshop": "Atölye",
            "items": [{"barcode": self.recipe["barcode"], "quantity": 10}]})
        response = self.client.get("/api/v2/barcodes/" + self.recipe["barcode"] + ".svg")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"<svg", response.data)
        self.assertEqual(self.stock()["totals"]["quantity"], 0)

    def test_odd_receipt_records_size_differences_not_only_total(self):
        plan = self.post("/production/plans", {"reference": "P-odd", "items": [{"barcode": self.recipe["barcode"], "quantity": 10}]})["data"]
        receipt = self.post("/receipts", {"location_id": self.location["id"], "plan_id": plan["id"], "reference": "R-odd",
            "items": [{"variant_id": self.variants[size]["id"], "quantity": qty} for size, qty in (("S", 10), ("M", 21), ("L", 20), ("XL", 9))]})["data"]
        differences = {row["variant_id"]: row["difference"] for row in receipt["plan"]["differences"]}
        self.assertEqual(differences[self.variants["M"]["id"]], 1)
        self.assertEqual(differences[self.variants["XL"]["id"]], -1)
        capacity = next(row for row in self.stock()["capacities"] if row["recipe_id"] == self.recipe["id"])
        self.assertEqual(capacity["series_capacity"], 9)
        leftovers = {row["variant_id"]: row["quantity"] for row in capacity["leftovers"]}
        self.assertEqual([leftovers[self.variants[size]["id"]] for size in ("S", "M", "L", "XL")], [1, 3, 2, 0])

    def test_transfer_does_not_double_stock_and_ledger_reconciles(self):
        self.receive()
        target = self.post("/locations", {"code": "A-03", "name": "Raf A03", "kind": "shelf", "parent_id": self.location["id"]})["data"]
        self.post("/transfers", {"from_location_id": self.location["id"], "to_location_id": target["id"],
            "items": [{"variant_id": self.variants["M"]["id"], "quantity": 7}]})
        self.assertEqual(self.stock()["totals"]["quantity"], 60)
        self.assertEqual(self.counts()["M"], 13)
        self.assertEqual(self.counts(target["id"])["M"], 7)
        self.assert_reconciled()

    def test_cross_location_parts_cannot_be_sold_as_ready_series(self):
        self.receive({"S": 1, "M": 2, "L": 2})
        target = self.post("/locations", {"code": "MERTER", "name": "Merter", "kind": "warehouse"})["data"]
        self.post("/receipts", {"location_id": target["id"], "reference": "OTHER", "items": [{"variant_id": self.variants["XL"]["id"], "quantity": 1}]})
        self.assertTrue(all(row["series_capacity"] == 0 for row in self.stock()["capacities"]))
        self.post("/sales", self.sale_payload(), status=409)
        self.assertEqual(self.stock()["totals"]["quantity"], 6)

    def test_series_and_extra_pieces_reduce_same_authoritative_stock(self):
        self.receive()
        result = self.post("/sales", self.sale_payload([
            {"barcode": self.recipe["barcode"], "quantity": 2},
            {"barcode": self.variants["M"]["barcode"], "quantity": 3},
            {"barcode": self.variants["XL"]["barcode"], "quantity": 1}], paid="1600"))["data"]
        self.assertEqual(result["total"], "1600.00")
        self.assertEqual(self.counts(), {"S": 8, "M": 13, "L": 16, "XL": 7})
        self.assert_reconciled()

    def test_repeated_unit_rows_reduce_all_pieces(self):
        self.receive()
        row = {"barcode": self.variants["M"]["barcode"], "quantity": 2}
        result = self.post("/sales", self.sale_payload([row, row], paid="400"))["data"]
        self.assertEqual(result["total"], "400.00")
        self.assertEqual(self.counts()["M"], 16)

    def test_unknown_variant_and_bad_quantities_fail_without_effect(self):
        self.receive()
        for value in (-2, 0, 1.5, True, "2"):
            with self.subTest(quantity=value):
                self.post("/sales", self.sale_payload([{ "barcode": self.variants["M"]["barcode"], "quantity": value}], "100"), status=400)
        self.post("/sales", self.sale_payload([{"variant_id": 99999, "quantity": 1}], "100"), status=404)
        self.assertEqual(self.counts()["M"], 20)
        self.assertEqual(db.session.execute(select(func_count(Sale))).scalar(), 0)

    def test_money_is_exact_and_currency_does_not_use_series_price(self):
        self.receive()
        payload = self.sale_payload([{ "barcode": self.variants["M"]["barcode"], "quantity": 1}], "10")
        payload["currency"] = "USD"
        result = self.post("/sales", payload)["data"]
        self.assertEqual((result["total"], result["items"][0]["unit_price"]), ("10.00", "10.00"))
        self.post(f'/catalog/variants/{self.variants["M"]["id"]}/prices', {"prices": {"TRY": "0.10"}, "reason": "Kuruş denemesi"}, method="patch")
        result = self.post("/sales", self.sale_payload([{ "barcode": self.variants["M"]["barcode"], "quantity": 3}], "0.30"))["data"]
        self.assertEqual(result["total"], "0.30")

    def test_nonfinite_negative_and_extra_decimal_price_rejected(self):
        self.receive()
        for bad in ("NaN", "Infinity", "-1", "1.001", True):
            with self.subTest(price=bad):
                self.post("/sales", self.sale_payload([{ "barcode": self.variants["M"]["barcode"], "quantity": 1, "unit_price": bad, "price_reason": "Test"}], "0"), status=400)
        self.assertEqual(self.counts()["M"], 20)

    def test_last_line_failure_rolls_back_all_stock_and_documents(self):
        self.receive({"S": 1, "M": 1})
        self.post("/sales", self.sale_payload([
            {"barcode": self.variants["S"]["barcode"], "quantity": 1},
            {"barcode": self.variants["M"]["barcode"], "quantity": 2}], "300"), status=409)
        self.assertEqual(self.counts(), {"S": 1, "M": 1, "L": 0, "XL": 0})
        self.assertEqual(db.session.execute(select(func_count(Sale))).scalar(), 0)
        self.assert_reconciled()

    def test_failure_after_stock_write_rolls_back_cash_and_receivable(self):
        self.receive()
        customer = self.post("/customers", {"name": "Müşteri"})["data"]
        before = self.stock()
        with self.assertLogs("briva_core.api", level="ERROR") as captured, patch("briva_core.service._payment", side_effect=RuntimeError("synthetic-sensitive-customer@example.invalid")):
            self.post("/sales", self.sale_payload(paid="100", customer_id=customer["id"]), status=500)
        self.assertNotIn("synthetic-sensitive", " ".join(captured.output))
        self.assertIn("exception_class=RuntimeError", " ".join(captured.output))
        self.assertEqual(before, self.stock())
        self.assertEqual(db.session.execute(select(func_count(Sale))).scalar(), 0)
        self.assertEqual(db.session.execute(select(func_count(AccountMovement))).scalar(), 0)

    def test_idempotent_retry_exactly_once_and_conflicting_content_rejected(self):
        self.receive()
        payload = self.sale_payload()
        key = uuid.uuid4().hex
        first = self.post("/sales", payload, key=key)
        second = self.post("/sales", payload, key=key, status=200)
        self.assertEqual(first["data"], second["data"])
        self.assertTrue(second["replayed"])
        payload["payment"]["amount"] = "599"
        self.post("/sales", payload, key=key, status=409)
        self.assertEqual(self.counts()["M"], 18)
        self.assertEqual(db.session.execute(select(func_count(Sale))).scalar(), 1)

    def test_receipt_retry_and_duplicate_reference_cannot_double_stock(self):
        payload = {"location_id": self.location["id"], "reference": "R-unique", "items": [{"variant_id": self.variants["M"]["id"], "quantity": 5}]}
        key = uuid.uuid4().hex
        self.post("/receipts", payload, key=key)
        self.post("/receipts", payload, key=key, status=200)
        self.post("/receipts", payload, status=409)
        self.assertEqual(self.counts()["M"], 5)

    def test_recipe_versions_freeze_old_barcode_and_old_sale_snapshot(self):
        self.receive()
        self.post("/catalog/recipes", {"model_id": self.product["id"], "color_code": "SY", "name": "Standart",
            "components": [{"variant_id": self.variants["M"]["id"], "quantity": 3}]})
        old = self.client.get("/api/v2/barcodes/" + self.recipe["barcode"]).get_json()["data"]
        self.assertEqual(old["pieces_per_unit"], 6)
        sale = self.post("/sales", self.sale_payload())["data"]
        self.assertEqual(sum(row["quantity"] for row in sale["items"]), 6)
        self.assertEqual(self.counts()["M"], 18)
        recipe = db.session.get(Recipe, self.recipe["id"])
        recipe.components = [{"variant_id": self.variants["M"]["id"], "quantity": 99}]
        with self.assertRaises(ValueError):
            db.session.commit()
        db.session.rollback()

    def test_one_pick_does_not_close_ten_piece_line_and_unknown_size_rejected(self):
        self.receive()
        task = self.post("/picking/tasks", {"location_id": self.location["id"], "items": [{"barcode": self.variants["M"]["barcode"], "quantity": 10}]})["data"]
        result = self.post(f'/picking/{task["id"]}/scan', {"barcode": self.variants["M"]["barcode"]})["data"]
        self.assertEqual((result["status"], result["lines"][0]["picked"], result["lines"][0]["remaining"]), ("picking", 1, 9))
        self.post(f'/picking/{task["id"]}/scan', {"barcode": self.variants["L"]["barcode"]}, status=409)
        self.post(f'/picking/{task["id"]}/scan', {"barcode": self.variants["M"]["barcode"], "quantity": 10}, status=409)
        self.post(f'/picking/{task["id"]}/scan', {"barcode": self.recipe["barcode"]}, status=400)

    def test_two_tasks_cannot_reserve_same_physical_pieces(self):
        self.receive({"M": 10})
        payload = {"location_id": self.location["id"], "items": [{"barcode": self.variants["M"]["barcode"], "quantity": 8}]}
        self.post("/picking/tasks", payload)
        self.post("/picking/tasks", payload, status=409)
        self.assertEqual(self.stock()["totals"]["reserved"], 8)

    def test_reserved_stock_cannot_be_direct_sold_and_cancel_releases(self):
        self.receive({"M": 10})
        task = self.post("/picking/tasks", {"location_id": self.location["id"], "items": [{"barcode": self.variants["M"]["barcode"], "quantity": 8}]})["data"]
        self.post("/sales", self.sale_payload([{ "barcode": self.variants["M"]["barcode"], "quantity": 3}], "300"), status=409)
        self.post(f'/picking/{task["id"]}/cancel', {"reason": "Müşteri iptal etti"})
        self.assertEqual(self.stock()["totals"]["reserved"], 0)
        self.assertEqual(self.stock()["totals"]["quantity"], 10)

    def test_incomplete_task_cannot_sell_complete_task_consumes_reserved_stock(self):
        self.receive({"M": 10})
        item = {"barcode": self.variants["M"]["barcode"], "quantity": 3}
        task = self.post("/picking/tasks", {"location_id": self.location["id"], "items": [item]})["data"]
        payload = {**self.sale_payload([item], "300"), "picking_task_id": task["id"]}
        self.post("/sales", payload, status=409)
        self.post(f'/picking/{task["id"]}/scan', item)
        self.post("/sales", payload)
        self.assertEqual(self.stock()["totals"]["reserved"], 0)
        self.assertEqual(self.counts()["M"], 7)
        self.post("/sales", payload, status=409)
        self.assert_reconciled()

    def test_seal_and_open_package_preserve_piece_total(self):
        self.receive()
        package = self.post("/packages", {"location_id": self.location["id"], "recipe_id": self.recipe["id"]})["data"]
        self.assertEqual((self.stock()["totals"]["quantity"], self.stock()["totals"]["packaged"]), (60, 6))
        self.post(f'/packages/{package["id"]}/open', {"reason": "Tekli satış"})
        self.assertEqual((self.stock()["totals"]["quantity"], self.stock()["totals"]["packaged"]), (60, 0))
        self.post(f'/packages/{package["id"]}/open', {}, status=409)
        self.assert_reconciled()

    def test_sealed_package_cannot_be_sold_as_loose_and_package_sells_once(self):
        self.receive({"S": 1, "M": 2, "L": 2, "XL": 1})
        package = self.post("/packages", {"location_id": self.location["id"], "recipe_id": self.recipe["id"]})["data"]
        self.post("/sales", self.sale_payload([{ "barcode": self.variants["M"]["barcode"], "quantity": 1}], "100"), status=409)
        self.post("/sales", self.sale_payload([{ "barcode": package["barcode"], "quantity": 1}], "600"))
        self.assertEqual(self.stock()["totals"]["quantity"], 0)
        self.assertEqual(self.stock()["totals"]["packaged"], 0)
        self.post("/sales", self.sale_payload([{ "barcode": package["barcode"], "quantity": 1}], "600"), status=409)

    def test_full_return_after_partial_payment_preserves_customer_credit(self):
        self.receive({"M": 10})
        customer = self.post("/customers", {"name": "İade müşteri"})["data"]
        sale = self.post("/sales", self.sale_payload([{ "barcode": self.variants["M"]["barcode"], "quantity": 1}], "60", customer["id"]))["data"]
        result = self.post(f'/sales/{sale["id"]}/returns', {"location_id": self.location["id"], "reason": "İade",
            "items": [{"sale_item_id": sale["items"][0]["id"], "quantity": 1}], "refund_amount": "0"})["data"]
        self.assertEqual(result["customer_balances"]["TRY"], "-60.00")
        self.assertEqual(self.counts()["M"], 10)
        payment = self.post("/payments", {"customer_id": customer["id"], "currency": "TRY", "amount": "60", "direction": "out", "method": "cash"})["data"]
        self.assertEqual(payment["balances"]["TRY"], "0.00")
        self.assertEqual(db.session.execute(select(func_count(Sale))).scalar(), 1)
        self.assertEqual(db.session.execute(select(func_count(AccountMovement))).scalar(), 4)

    def test_partial_returns_limit_quantities_and_keep_original_sale_price(self):
        self.receive({"M": 10})
        sale = self.post("/sales", self.sale_payload([{ "barcode": self.variants["M"]["barcode"], "quantity": 2}], "200"))["data"]
        self.post(f'/catalog/variants/{self.variants["M"]["id"]}/prices', {"prices": {"TRY": "900"}, "reason": "Yeni fiyat"}, method="patch")
        payload = {"location_id": self.location["id"], "reason": "Bir parça", "items": [{"sale_item_id": sale["items"][0]["id"], "quantity": 1}], "refund_amount": "100"}
        self.assertEqual(self.post(f'/sales/{sale["id"]}/returns', payload)["data"]["total"], "100.00")
        self.post(f'/sales/{sale["id"]}/returns', payload)
        self.post(f'/sales/{sale["id"]}/returns', payload, status=409)
        self.assertEqual(self.counts()["M"], 10)

    def test_cashier_cannot_override_prices_or_forge_admin_session(self):
        self.receive()
        self.login(self.cashier_id)
        result = self.client.get("/api/v2/bootstrap").get_json()["data"]
        self.assertFalse(result["permissions"]["inventory"])
        self.post("/receipts", {"location_id": self.location["id"], "reference": "Bad", "items": [{"variant_id": self.variants["M"]["id"], "quantity": 1}]}, status=403)
        self.post("/sales", self.sale_payload([{ "barcode": self.variants["M"]["barcode"], "quantity": 1, "unit_price": "90", "price_reason": "Hile"}], "90"), status=403)
        self.post("/sales", self.sale_payload([{ "barcode": self.variants["M"]["barcode"], "quantity": 1}], "100"))

    def test_price_override_reason_and_audit_are_required(self):
        self.receive()
        item = {"barcode": self.variants["M"]["barcode"], "quantity": 1, "unit_price": "80"}
        self.post("/sales", self.sale_payload([item], "80"), status=400)
        item["price_reason"] = "Müşteri anlaşması"
        sale = self.post("/sales", self.sale_payload([item], "80"))["data"]
        self.assertEqual(sale["items"][0]["list_price"], "100.00")
        audit = db.session.execute(select(AuditEvent).where(AuditEvent.action == "sale.price_override")).scalar_one()
        self.assertEqual(audit.details["reason"], "Müşteri anlaşması")

    def test_tenant_isolation_on_every_barcode_and_object(self):
        self.receive()
        self.login(self.other_id)
        response = self.client.get("/api/v2/barcodes/" + self.variants["M"]["barcode"])
        self.assertEqual(response.status_code, 404)
        self.post("/receipts", {"location_id": self.location["id"], "reference": "Intrusion", "items": [{"variant_id": self.variants["M"]["id"], "quantity": 1}]}, status=404)
        bootstrap = self.client.get("/api/v2/bootstrap").get_json()["data"]
        self.assertEqual(bootstrap["models"], [])
        self.assertEqual(bootstrap["company"], "OTHER_TEST")
        self.post("/customers", {"company_id": "CORE_TEST", "name": "Injected"}, status=400)

    def test_unauthenticated_and_missing_idempotency_rejected(self):
        response = self.client.post("/api/v2/customers", json={"name": "No key"})
        self.assertEqual(response.status_code, 400)
        with self.client.session_transaction() as sess:
            sess.clear()
        self.assertEqual(self.client.get("/api/v2/bootstrap").status_code, 401)
        self.post("/customers", {"name": "No login"}, status=401)

    def test_quarantine_is_physically_counted_but_not_saleable(self):
        quarantine = self.post("/locations", {"code": "Q", "name": "Kusurlu", "kind": "quarantine"})["data"]
        self.post("/receipts", {"location_id": self.location["id"], "reference": "R-Q",
            "items": [{"variant_id": self.variants["M"]["id"], "quantity": 1}],
            "defective": [{"variant_id": self.variants["L"]["id"], "quantity": 2}], "quarantine_location_id": quarantine["id"]})
        payload = self.sale_payload([{ "barcode": self.variants["L"]["barcode"], "quantity": 1}], "100")
        payload["location_id"] = quarantine["id"]
        self.post("/sales", payload, status=400)
        self.assertEqual(self.stock()["totals"]["quantity"], 3)

    def test_stock_constraint_and_immutable_ledger_defend_programming_errors(self):
        self.receive()
        with self.assertRaises(IntegrityError):
            db.session.execute(update(StockBalance).values(quantity=-1))
            db.session.commit()
        db.session.rollback()
        ledger = db.session.execute(select(StockMovement).limit(1)).scalar_one()
        ledger.quantity_delta = 999
        with self.assertRaises(ValueError):
            db.session.commit()
        db.session.rollback()
        self.assert_reconciled()

    def test_numeric_barcode_rules_keep_old_labels_and_leading_zeroes(self):
        old = self.variants["M"]["barcode"]
        rule = self.post("/barcode-rules", {"mode": "numeric", "prefix": "007"}, method="put")["data"]
        self.assertEqual(rule["applies_to"], "new_barcodes_only")
        new = self.post("/catalog/models", {"model_code": "NUM-1", "name": "Numeric",
            "colors": [{"code": "BY", "name": "Beyaz"}], "sizes": ["M"], "unit_prices": {"TRY": "10"}})["data"]
        code = new["variants"][0]["barcode"]
        self.assertRegex(code, r"^0071[0-9]{9}$")
        self.assertEqual(sum(int(value) * (1 if index % 2 == 0 else 3) for index, value in enumerate(code[:12])) % 10, (-int(code[-1])) % 10)
        self.assertEqual(self.client.get("/api/v2/barcodes/" + old).status_code, 200)
        self.assertEqual(self.client.get("/api/v2/barcodes/" + code).get_json()["data"]["size"], "M")
        self.assertEqual(self.stock()["totals"]["quantity"], 0)

    def test_bad_barcode_rule_and_cashier_rule_write_are_rejected(self):
        for body in ({"mode": "numeric", "prefix": "12"}, {"mode": "numeric", "prefix": "１２３"},
                     {"mode": "alphanumeric", "prefix": "ŞİRKET"}, {"mode": "bad", "prefix": "BRV"}):
            self.post("/barcode-rules", body, method="put", status=400)
        self.login(self.cashier_id)
        self.post("/barcode-rules", {"mode": "numeric", "prefix": "200"}, method="put", status=403)

    def test_entirely_defective_receipt_has_no_sellable_stock(self):
        quarantine = self.post("/locations", {"code": "Q-ALL", "name": "Kusurlu", "kind": "quarantine"})["data"]
        self.post("/receipts", {"location_id": self.location["id"], "reference": "ALL-DEFECTIVE", "items": [],
            "defective": [{"variant_id": self.variants["M"]["id"], "quantity": 3}], "quarantine_location_id": quarantine["id"]})
        self.assertEqual(self.stock()["totals"]["quantity"], 3)
        self.assertEqual(self.stock()["totals"]["available"], 0)
        self.assertEqual(self.stock()["totals"]["quarantined"], 3)
        self.assert_reconciled()

    def test_package_transfer_keeps_code_and_preserves_piece_counts(self):
        self.receive({"S": 1, "M": 2, "L": 2, "XL": 1})
        target = self.post("/locations", {"code": "P-SHELF", "name": "Paket rafı", "kind": "shelf"})["data"]
        package = self.post("/packages", {"location_id": self.location["id"], "recipe_id": self.recipe["id"]})["data"]
        moved = self.post(f'/packages/{package["id"]}/transfer', {"to_location_id": target["id"]})["data"]
        self.assertEqual(moved["barcode"], package["barcode"])
        self.assertEqual(self.stock()["totals"]["quantity"], 6)
        self.assertEqual(self.stock()["totals"]["packaged"], 6)
        self.assertEqual(self.counts(), {"S": 0, "M": 0, "L": 0, "XL": 0})
        payload = self.sale_payload([{ "barcode": package["barcode"], "quantity": 1}], "600")
        payload["location_id"] = target["id"]
        self.post("/sales", payload)
        self.assertEqual(self.stock()["totals"]["quantity"], 0)
        self.assert_reconciled()

    def test_shelf_pick_requires_correct_shelf_scan_before_piece(self):
        shelf = self.post("/locations", {"code": "ACTUAL-SHELF", "name": "Gerçek raf", "kind": "shelf"})["data"]
        self.post("/receipts", {"location_id": shelf["id"], "reference": "SHELF-RECEIPT", "items": [{"variant_id": self.variants["M"]["id"], "quantity": 2}]})
        task = self.post("/picking/tasks", {"location_id": shelf["id"], "items": [{"barcode": self.variants["M"]["barcode"], "quantity": 2}]})["data"]
        self.post(f'/picking/{task["id"]}/scan', {"barcode": self.variants["M"]["barcode"]}, status=409)
        self.post(f'/picking/{task["id"]}/scan', {"barcode": self.location["barcode"]}, status=409)
        verified = self.post(f'/picking/{task["id"]}/scan', {"barcode": shelf["barcode"]})["data"]
        self.assertTrue(verified["location_verified"])
        self.assertEqual(verified["lines"][0]["picked"], 0)
        picked = self.post(f'/picking/{task["id"]}/scan', {"barcode": self.variants["M"]["barcode"]})["data"]
        self.assertEqual(picked["lines"][0]["remaining"], 1)

    def test_add_only_employee_cannot_receive_stock_or_edit_identity(self):
        user = db.session.get(User, self.cashier_id)
        user.can_add_product = True
        db.session.commit()
        self.login(self.cashier_id)
        self.post("/catalog/models", {"model_code": "ADD-ONLY", "name": "Can create",
            "colors": [{"code": "SY", "name": "Siyah"}], "sizes": ["M"], "unit_prices": {"TRY": "1"}})
        self.post("/receipts", {"location_id": self.location["id"], "reference": "CANNOT-ADD", "items": [{"variant_id": self.variants["M"]["id"], "quantity": 2}]}, status=403)
        self.post(f'/catalog/models/{self.product["id"]}', {"name": "Hile"}, status=403, method="patch")

    def test_cashier_cannot_read_other_sales_history_but_can_read_own_result(self):
        self.receive()
        owner_sale = self.post("/sales", self.sale_payload())["data"]
        self.login(self.cashier_id)
        bootstrap = self.client.get("/api/v2/bootstrap").get_json()["data"]
        self.assertEqual(bootstrap["sales"], [])
        self.assertEqual(self.client.get(f'/api/v2/sales/{owner_sale["id"]}').status_code, 403)
        own = self.post("/sales", self.sale_payload())["data"]
        self.assertEqual(self.client.get(f'/api/v2/sales/{own["id"]}').status_code, 200)

    def test_same_tenant_different_actor_cannot_replay_another_users_command(self):
        self.receive()
        payload = self.sale_payload()
        key = uuid.uuid4().hex
        self.post("/sales", payload, key=key)
        self.login(self.cashier_id)
        self.post("/sales", payload, key=key, status=409)

    def test_invalid_query_ids_return_controlled_errors(self):
        for value in ("0", "-1", "1.5", "²", "9999999999999999999999999999999", "2147483648"):
            self.assertEqual(self.client.get("/api/v2/stock", query_string={"location_id": value}).status_code, 400)
            self.assertEqual(self.client.get("/api/v2/ledger", query_string={"before_id": value}).status_code, 400)

    def test_custom_substitution_decreases_only_actual_bedens(self):
        self.receive()
        result = self.post("/sales", self.sale_payload([
            {"barcode": self.variants[size]["barcode"], "quantity": qty} for size, qty in (("S", 1), ("M", 3), ("L", 2))], "600"))["data"]
        self.assertEqual(result["total"], "600.00")
        self.assertEqual(self.counts(), {"S": 9, "M": 17, "L": 18, "XL": 10})
        self.assertEqual(self.client.get("/api/v2/barcodes/" + self.recipe["barcode"]).get_json()["data"]["pieces_per_unit"], 6)

    def test_api_timestamps_explicitly_identify_utc(self):
        self.receive()
        self.post("/sales", self.sale_payload())
        data = self.client.get("/api/v2/bootstrap").get_json()["data"]
        self.assertTrue(data["sales"][0]["created_at"].endswith("+00:00"))
        self.assertTrue(data["receipts"][0]["created_at"].endswith("+00:00"))

    def test_stock_count_is_an_idempotent_append_only_difference(self):
        self.receive({"M": 10})
        payload = {"location_id": self.location["id"], "reference": "COUNT-1", "reason": "Fiziksel sayım",
                   "items": [{"variant_id": self.variants["M"]["id"], "expected_quantity": 10, "counted_quantity": 8}]}
        key = uuid.uuid4().hex
        result = self.post("/stock-counts", payload, key=key)["data"]
        self.assertEqual(result["items"][0]["delta"], -2)
        self.post("/stock-counts", payload, key=key, status=200)
        self.assertEqual(self.counts()["M"], 8)
        receipt_movements = db.session.execute(select(StockMovement).where(StockMovement.kind == "receipt")).scalars().all()
        self.assertEqual(receipt_movements[0].quantity_delta, 10)
        self.assert_reconciled()
        self.assertEqual(self.client.get("/api/v2/health").get_json()["data"]["status"], "ok")

    def test_stale_stock_count_cannot_overwrite_new_receipt(self):
        self.receive({"M": 10})
        self.receive({"M": 1})
        response = self.post("/stock-counts", {"location_id": self.location["id"], "reference": "STALE-COUNT", "reason": "Sayım",
            "items": [{"variant_id": self.variants["M"]["id"], "expected_quantity": 10, "counted_quantity": 8}]}, status=409)
        self.assertEqual(response["error"]["code"], "stock_count_changed")
        self.assertEqual(self.counts()["M"], 11)

    def test_count_to_zero_and_positive_difference_are_both_recorded(self):
        self.receive({"M": 3})
        self.post("/stock-counts", {"location_id": self.location["id"], "reference": "ZERO", "reason": "Eksik ürün",
            "items": [{"variant_id": self.variants["M"]["id"], "expected_quantity": 3, "counted_quantity": 0}]})
        self.post("/stock-counts", {"location_id": self.location["id"], "reference": "FOUND", "reason": "Bulunan ürün",
            "items": [{"variant_id": self.variants["L"]["id"], "expected_quantity": 0, "counted_quantity": 4}]})
        self.assertEqual(self.counts(), {"S": 0, "M": 0, "L": 4, "XL": 0})
        self.assert_reconciled()

    def test_stock_count_cannot_go_below_reserved_or_packaged(self):
        self.receive()
        self.post("/picking/tasks", {"location_id": self.location["id"], "items": [{"barcode": self.variants["M"]["barcode"], "quantity": 19}]})
        self.post("/stock-counts", {"location_id": self.location["id"], "reference": "BAD-RESERVED", "reason": "Sayım",
            "items": [{"variant_id": self.variants["M"]["id"], "expected_quantity": 20, "counted_quantity": 18}]}, status=409)
        self.post("/packages", {"location_id": self.location["id"], "items": [{"variant_id": self.variants["S"]["id"], "quantity": 10}]})
        self.post("/stock-counts", {"location_id": self.location["id"], "reference": "BAD-PACKAGE", "reason": "Sayım",
            "items": [{"variant_id": self.variants["S"]["id"], "expected_quantity": 10, "counted_quantity": 9}]}, status=409)
        self.assertEqual(self.client.get("/api/v2/health").get_json()["data"]["status"], "ok")

    def test_health_reports_mismatch_without_repairing_it(self):
        self.receive({"M": 10})
        self.assertEqual(self.client.get("/api/v2/health").get_json()["data"]["status"], "ok")
        # Synthetic tampering in the isolated memory DB only.
        db.session.execute(update(StockBalance).where(StockBalance.variant_id == self.variants["M"]["id"]).values(quantity=11))
        db.session.commit()
        data = self.client.get("/api/v2/health").get_json()["data"]
        self.assertEqual(data["status"], "attention")
        self.assertEqual(data["issues"][0]["code"], "ledger_balance_mismatch")
        self.assertFalse(data["automatic_repair"])
        self.assertEqual(self.counts()["M"], 11)
        self.login(self.cashier_id)
        self.assertEqual(self.client.get("/api/v2/health").status_code, 403)

    def test_changed_price_preview_requires_explicit_new_confirmation(self):
        self.receive({"M": 2})
        payload = self.sale_payload([{ "barcode": self.variants["M"]["barcode"], "quantity": 1}], "100")
        payload["expected_total"] = "100.00"
        self.post(f'/catalog/variants/{self.variants["M"]["id"]}/prices', {"prices": {"TRY": "120"}, "reason": "Fiyat arttı"}, method="patch")
        response = self.post("/sales", payload, status=409)
        self.assertEqual(response["error"]["code"], "pricing_changed")
        self.assertEqual(self.counts()["M"], 2)
        payload["expected_total"], payload["payment"]["amount"] = "120", "120"
        self.post("/sales", payload)

    def test_nonfinance_cashier_result_and_detail_do_not_expose_customer_balance(self):
        self.receive()
        customer = self.post("/customers", {"name": "Özel cari müşteri"})["data"]
        self.post("/sales", self.sale_payload(paid="0", customer_id=customer["id"]))
        self.login(self.cashier_id)
        sale = self.post("/sales", self.sale_payload(paid="100", customer_id=customer["id"]))["data"]
        self.assertEqual(sale["customer_balances"], {})
        detail = self.client.get(f'/api/v2/sales/{sale["id"]}').get_json()["data"]
        self.assertEqual(detail["customer_balances"], {})
        self.login(self.owner_id)
        account = self.client.get(f'/api/v2/customers/{customer["id"]}/account').get_json()["data"]
        self.assertEqual(account["customer"]["balances"]["TRY"], "1100.00")

    def test_stock_keyset_page_keeps_totals_complete_and_capacities_correct(self):
        self.receive()
        first = self.client.get("/api/v2/stock?limit=2").get_json()["data"]
        self.assertEqual(len(first["balances"]), 2)
        self.assertTrue(first["pagination"]["has_more"])
        self.assertEqual(first["totals"]["quantity"], 60)
        cursor = first["pagination"]["next_after_balance_id"]
        second = self.client.get(f'/api/v2/stock?limit=2&after_balance_id={cursor}').get_json()["data"]
        self.assertFalse(second["pagination"]["has_more"])
        self.assertEqual(second["totals"]["quantity"], 60)
        self.assertFalse(set(row["id"] for row in first["balances"]) & set(row["id"] for row in second["balances"]))
        self.assertEqual(first["capacities"][0]["series_capacity"], 10)

    def test_completed_loose_pick_cannot_silently_become_a_sealed_package_sale(self):
        self.receive({"M": 4})
        package = self.post("/packages", {"location_id": self.location["id"], "items": [{"variant_id": self.variants["M"]["id"], "quantity": 2}]})["data"]
        task = self.post("/picking/tasks", {"location_id": self.location["id"], "items": [{"barcode": self.variants["M"]["barcode"], "quantity": 2}]})["data"]
        self.post(f'/picking/{task["id"]}/scan', {"barcode": self.variants["M"]["barcode"], "quantity": 2})
        payload = self.sale_payload([{ "barcode": package["barcode"], "quantity": 1}], "200")
        payload["picking_task_id"] = task["id"]
        self.post("/sales", payload, status=409)
        self.assertEqual((self.stock()["totals"]["quantity"], self.stock()["totals"]["reserved"], self.stock()["totals"]["packaged"]), (4, 2, 2))

    def test_malformed_wrong_media_and_excessively_nested_json_are_controlled(self):
        headers = {"Idempotency-Key": uuid.uuid4().hex}
        wrong_type = self.client.post("/api/v2/customers", data="name=hello", headers=headers)
        self.assertEqual((wrong_type.status_code, wrong_type.get_json()["error"]["code"]), (415, "json_required"))
        malformed = self.client.post("/api/v2/customers", data="{invalid", content_type="application/json", headers=headers)
        self.assertEqual((malformed.status_code, malformed.get_json()["error"]["code"]), (400, "invalid_json"))
        nested = '{"x":' + "[" * 2000 + "1" + "]" * 2000 + "}"
        response = self.client.post("/api/v2/customers", data=nested, content_type="application/json", headers=headers)
        self.assertEqual(response.status_code, 400)
        oversized = self.client.post("/api/v2/customers", data='{"name":"' + "X" * 512_001 + '"}', content_type="application/json", headers=headers)
        self.assertEqual(oversized.status_code, 413)

    def test_multi_tab_actor_context_cannot_rebind_old_payload_to_new_user(self):
        self.receive()
        payload = self.sale_payload()
        old_headers = {"Idempotency-Key": uuid.uuid4().hex, "X-Briva-Actor": str(self.owner_id), "X-Briva-Company": "CORE_TEST"}
        self.login(self.cashier_id)
        response = self.client.post("/api/v2/sales", json=payload, headers=old_headers)
        self.assertEqual((response.status_code, response.get_json()["error"]["code"]), (409, "auth_context_changed"))
        self.assertEqual(self.counts()["M"], 20)
        self.login(self.owner_id)
        response = self.client.post("/api/v2/sales", json=payload, headers=old_headers)
        self.assertEqual(response.status_code, 201)

    def test_actor_context_header_pair_is_complete_and_utf8_safe(self):
        incomplete = self.client.post("/api/v2/customers", json={"name": "Bad"}, headers={
            "Idempotency-Key": uuid.uuid4().hex, "X-Briva-Actor": str(self.owner_id)})
        self.assertEqual(incomplete.status_code, 400)
        user = db.session.get(User, self.owner_id)
        user.company = "Musa’nın Şirketi"
        db.session.commit()
        from urllib.parse import quote
        response = self.client.post("/api/v2/customers", json={"name": "Unicode safe"}, headers={
            "Idempotency-Key": uuid.uuid4().hex, "X-Briva-Actor": str(self.owner_id),
            "X-Briva-Company": quote(user.company, safe="")})
        self.assertEqual(response.status_code, 201, response.get_json())

    def assert_reconciled(self):
        totals = defaultdict(lambda: [0, 0, 0])
        for row in db.session.execute(select(StockMovement)).scalars():
            values = totals[(row.variant_id, row.location_id)]
            values[0] += row.quantity_delta
            values[1] += row.reserved_delta
            values[2] += row.packaged_delta
        for row in self.stock()["balances"]:
            self.assertEqual(totals[(row["variant_id"], row["location_id"])], [row["quantity"], row["reserved"], row["packaged"]])


def func_count(model):
    from sqlalchemy import func
    return func.count(model.id)


class CoreLabelTests(unittest.TestCase):
    def test_code128_table_width_and_checksum(self):
        self.assertEqual(len(PATTERNS), 107)
        self.assertTrue(all(sum(map(int, value)) == 11 for value in PATTERNS[:106]))
        self.assertEqual(sum(map(int, PATTERNS[106])), 13)
        self.assertEqual(code128_symbols("HI"), [104, 40, 41, (104 + 40 + 2 * 41) % 103, 106])
        self.assertIn("BRVU000000000001", code128_svg("BRVU000000000001"))

    def test_label_rejects_non_ascii_and_controls(self):
        for value in ("", "ş", "A\nB", "A" * 49):
            with self.subTest(code=value), self.assertRaises(Exception):
                code128_symbols(value)

    def test_production_codes_use_numeric_pairs_with_known_symbol_sequences(self):
        self.assertEqual(code128_symbols("BRVU000000000001"), [104, 34, 50, 54, 53, 99, 0, 0, 0, 0, 0, 1, 88, 106])
        self.assertEqual(code128_symbols("2001000000017"), [105, 20, 1, 0, 0, 0, 1, 100, 23, 90, 106])
        self.assertEqual(code128_symbols("0000"), [105, 0, 0, 2, 106])
        import xml.etree.ElementTree as ET
        alpha = ET.fromstring(code128_svg("BRVU000000000001"))
        numeric = ET.fromstring(code128_svg("2001000000017"))
        self.assertEqual(alpha.attrib["viewBox"], "0 0 180 68")
        self.assertEqual(numeric.attrib["viewBox"], "0 0 147 68")
        self.assertEqual(alpha.attrib["preserveAspectRatio"], "none")
        self.assertIn('shape-rendering="crispEdges"', code128_svg("0000"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
