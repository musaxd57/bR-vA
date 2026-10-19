"""Local setup regressions against a disposable SQLite database.

Run from the project directory: python tests/test_local_setup.py
Install the project's dependencies first. The tests never load .env, use the
original database, start a web server, or contact an AI provider.
"""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


class LocalSetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = PROJECT_ROOT / '.verification-data' / ('local-setup-' + uuid.uuid4().hex)
        cls.data_dir.mkdir(parents=True)
        cls.expected_database = cls.data_dir / "data.db"
        cls.environment = patch.dict(os.environ, {
            "SECRET_KEY": "synthetic-test-session-key-with-at-least-32-characters",
            "BRIVA_DATA_DIR": str(cls.data_dir),
            "PYTHON_DOTENV_DISABLED": "1",
            "GROQ_API_KEY": "",
        })
        cls.environment.start()
        cls.addClassCleanup(cls.environment.stop)

        # A regression must not accidentally send a request while importing or
        # exercising a route. Flask's test client does not use network sockets.
        for target in ("socket.socket.connect", "socket.create_connection"):
            network_guard = patch(target, side_effect=AssertionError(
                "Network access is disabled in local setup tests"
            ))
            network_guard.start()
            cls.addClassCleanup(network_guard.stop)

        saved_stdout = sys.stdout
        try:
            cls.app_module = importlib.import_module("app")
        finally:
            # The legacy app redirects stdout for its log screen. Keep the
            # unittest output on the original stream.
            sys.stdout = saved_stdout

        if Path(cls.app_module.__file__).resolve() != PROJECT_ROOT / "app.py":
            raise AssertionError("A different project's app module was imported")
        cls.app = cls.app_module.app
        cls.db = cls.app_module.db
        cls.models = importlib.import_module("models")
        cls.app.config.update(TESTING=True, BRIVA_ALLOW_LEGACY_WRITES=True)

        if cls.expected_database.exists():
            raise AssertionError("Importing app must not create the database")

        with cls.app.app_context():
            actual_database = Path(cls.db.engine.url.database).resolve()
            if actual_database != cls.expected_database.resolve():
                raise AssertionError("Refusing to touch a non-test database")
            cls.db.create_all()
            cls.addClassCleanup(cls.close_database)
            from db_utils import add_user

            cls.username = "synthetic_setup_admin"
            cls.password = "Synthetic-test-password-43"
            cls.company = "SYNTHETIC_SETUP_COMPANY"
            add_user(cls.username, cls.password, company=cls.company, role="admin")
            cls.db.session.add(cls.models.Category(
                name="Synthetic Shirts", code="SH", company_id=cls.company,
                sizes=[{"value": "M", "number": 1}],
            ))
            cls.db.session.add(cls.models.Color(
                code="W", name="Synthetic White", company_id=cls.company,
            ))
            cls.db.session.commit()

    @classmethod
    def close_database(cls):
        with cls.app.app_context():
            cls.db.session.remove()
            cls.db.engine.dispose()

    def setUp(self):
        self.client = self.app.test_client()

    def login_admin(self):
        token = self.client.get('/api/security/csrf').get_json()['csrf_token']
        response = self.client.post("/login", data={
            "username": self.username,
            "password": self.password,
            "csrf_token": token,
        })
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertEqual(session["role"], "admin")
            self.assertEqual(session["company"], self.company)
            self.assertIsInstance(session["user"], int)
        self.csrf = self.client.get('/api/security/csrf').get_json()['csrf_token']

    def test_login_page_renders_without_ai_credentials(self):
        self.assertEqual(os.environ["GROQ_API_KEY"], "")
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('name="username"', html)
        self.assertIn('name="password"', html)

    def test_logged_in_admin_can_create_a_product(self):
        self.login_admin()
        response = self.client.post("/product", data={
            "csrf_token": self.csrf,
            "name": "Synthetic newly created shirt",
            "category": "Synthetic Shirts",
            "sizes": json.dumps([{"value": "M", "number": 1, "stock": 10}]),
            "currency": json.dumps({"TL": 1000, "USD": 25, "EURO": 30}),
            "colors": json.dumps(["W"]),
            "custom_attributes": json.dumps({}),
        })
        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertEqual(response.get_json()["status"], "ok")
        with self.app.app_context():
            products = self.models.Product.query.filter_by(
                name="Synthetic newly created shirt", company_id=self.company,
            ).all()
            self.assertEqual(len(products), 1)
            product = products[0]
            self.assertTrue(product.code)
            self.assertEqual(product.color_id, "W")
            self.assertEqual(product.stock, 10)
            self.assertEqual(product.currency["TL"], 1000)

    def test_new_customer_starts_with_an_empty_debt_object(self):
        self.login_admin()
        response = self.client.post("/add_customer", headers={'X-CSRF-Token': self.csrf}, json={
            "name": "Synthetic New Customer",
            "phone": "0000000043",
            "description": "Disposable local test customer",
        })
        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertEqual(response.get_json()["status"], "ok")
        with self.app.app_context():
            customer = self.models.Customer.query.filter_by(
                name="Synthetic New Customer", company_id=self.company,
            ).one()
            self.assertIsInstance(customer.debt, dict)
            self.assertEqual(customer.debt, {})

    def test_startup_rejects_missing_or_short_secret_key(self):
        for secret in (None, "short-test-key"):
            with self.subTest(secret="missing" if secret is None else "too short"):
                data_dir = PROJECT_ROOT / '.verification-data' / ('secret-check-' + uuid.uuid4().hex)
                data_dir.mkdir(parents=True)
                environment = os.environ.copy()
                environment.update({
                    "BRIVA_DATA_DIR": str(data_dir),
                    "PYTHON_DOTENV_DISABLED": "1",
                    "GROQ_API_KEY": "",
                })
                if secret is None:
                    environment.pop("SECRET_KEY", None)
                else:
                    environment["SECRET_KEY"] = secret
                result = subprocess.run(
                    [sys.executable, "-c", "import app"],
                    cwd=PROJECT_ROOT,
                    env=environment,
                    text=True,
                    capture_output=True,
                    timeout=20,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("SECRET_KEY", result.stdout + result.stderr)
                self.assertFalse((data_dir / "data.db").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
