"""Catalogue paging must bound nested variants without hiding known barcodes."""
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
from briva_core.models import Barcode, CatalogModel, Location, Recipe, StockBalance, StockMovement, Variant
from briva_core.service import format_barcode


class CoreCatalogPagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask("paging-" + uuid.uuid4().hex)
        cls.app.config.update(TESTING=True, SECRET_KEY="synthetic-paging-key", SQLALCHEMY_DATABASE_URI="sqlite:///:memory:")
        db.init_app(cls.app)
        register_core(cls.app)
        cls.context = cls.app.app_context()
        cls.context.push()
        create_core_schema()
        User.__table__.create(db.engine, checkfirst=True)
        user = User(username="paging_owner", company="PAGING", role="admin", password_hash="synthetic")
        db.session.add(user)
        db.session.flush()
        cls.user_id = user.id
        cls.models = []
        for index in range(8):
            model = CatalogModel(company_id="PAGING", model_code="PAGE-" + str(index),
                name="literal %_ model" if index == 7 else "Product " + str(index))
            db.session.add(model)
            db.session.flush()
            cls.models.append(model.id)
            count = 1 if index == 7 else 1000
            db.session.execute(Variant.__table__.insert(), [{"company_id": "PAGING", "model_id": model.id,
                "color_code": "C" + str(offset // 10), "color_name": "Color " + str(offset // 10),
                "size": str(offset % 10), "prices": {"TRY": "100.00"}} for offset in range(count)])
        ids = db.session.execute(select(Variant.id)).scalars().all()
        db.session.execute(Barcode.__table__.insert(), [{"company_id": "PAGING", "kind": "unit", "target_id": value,
            "code": format_barcode("unit", value, "alphanumeric", "BRV")} for value in ids])
        cls.last_variant = db.session.execute(select(Variant).where(Variant.model_id == cls.models[6]).order_by(Variant.id.desc())).scalars().first().id
        location = Location(company_id="PAGING", code="L", name="Outside catalogue page", kind="store")
        db.session.add(location)
        db.session.flush()
        db.session.add(Barcode(company_id="PAGING", kind="location", target_id=location.id, code=format_barcode("location", location.id, "alphanumeric", "BRV")))
        recipe = Recipe(company_id="PAGING", model_id=cls.models[6], color_code="C99", name="Outside page recipe", version=1,
            components=[{"variant_id": cls.last_variant, "quantity": 1}])
        db.session.add(recipe)
        db.session.flush()
        cls.recipe_id = recipe.id
        db.session.add(Barcode(company_id="PAGING", kind="recipe", target_id=recipe.id, code=format_barcode("recipe", recipe.id, "alphanumeric", "BRV")))
        db.session.add(StockBalance(company_id="PAGING", variant_id=cls.last_variant, location_id=location.id, quantity=3, reserved=0, packaged=0))
        db.session.add(StockMovement(company_id="PAGING", variant_id=cls.last_variant, location_id=location.id, quantity_delta=3,
            kind="synthetic_fixture", document_type="synthetic_fixture", document_id=1, actor_id=cls.user_id))
        db.session.commit()
        cls.client = cls.app.test_client()
        with cls.client.session_transaction() as sess:
            sess["user"] = cls.user_id

    @classmethod
    def tearDownClass(cls):
        db.session.remove()
        db.engine.dispose()
        cls.context.pop()

    def get(self, path):
        response = self.client.get("/api/v2" + path)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()["data"]

    def test_bootstrap_never_serializes_more_than_variant_budget(self):
        data = self.get("/bootstrap")
        pagination = data["catalog_pagination"]
        self.assertEqual(pagination["total_models"], 8)
        self.assertEqual(pagination["total_variants"], 7001)
        self.assertEqual(sum(len(row["variants"]) for row in data["models"]), 5000)
        self.assertEqual(pagination["next_after_model_id"], self.models[4])
        self.assertTrue(pagination["has_more"])
        self.assertEqual(data["barcode_count"], 7003)

    def test_next_page_has_no_duplicates_and_preserves_complete_models(self):
        first = self.get("/catalog")
        second = self.get("/catalog?after_model_id=" + str(first["pagination"]["next_after_model_id"]))
        first_ids = {row["id"] for row in first["models"]}
        second_ids = {row["id"] for row in second["models"]}
        self.assertFalse(first_ids & second_ids)
        self.assertEqual(first_ids | second_ids, set(self.models))
        self.assertFalse(second["pagination"]["has_more"])
        self.assertEqual(sum(len(row["variants"]) for row in second["models"]), 2001)

    def test_server_search_and_literal_wildcards_do_not_search_only_loaded_page(self):
        result = self.get("/catalog?q=PAGE-6")
        self.assertEqual([row["id"] for row in result["models"]], [self.models[6]])
        response = self.client.get("/api/v2/catalog", query_string={"q": "%_"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.get_json()["data"]["models"]], [self.models[7]])

    def test_direct_model_and_barcode_work_beyond_bootstrap_page(self):
        result = self.get("/catalog?model_id=" + str(self.models[6]))
        self.assertEqual(result["models"][0]["id"], self.models[6])
        self.assertEqual(len(result["models"][0]["variants"]), 1000)
        target = self.get("/barcodes/" + format_barcode("unit", self.last_variant, "alphanumeric", "BRV"))
        self.assertEqual(target["model_id"], self.models[6])

    def test_stock_carries_labels_and_recipes_even_outside_catalogue_page(self):
        data = self.get("/stock")
        self.assertEqual(data["variants"][0]["id"], self.last_variant)
        self.assertEqual(data["models"][0]["id"], self.models[6])
        self.assertEqual(data["recipes"][0]["id"], self.recipe_id)
        self.assertEqual(data["capacities"][0]["series_capacity"], 3)

    def test_page_limits_are_server_enforced(self):
        for path in ("/catalog?limit=101", "/catalog?variant_budget=999", "/catalog?variant_budget=5001", "/catalog?after_model_id=²"):
            self.assertEqual(self.client.get("/api/v2" + path).status_code, 400)
        result = self.get("/catalog?limit=2&variant_budget=1000")
        self.assertEqual(len(result["models"]), 1)
        self.assertEqual(result["pagination"]["loaded_variants"], 1000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
