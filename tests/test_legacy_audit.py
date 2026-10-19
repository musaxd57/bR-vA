"""Archive isolation regressions against the real app, using retained synthetic data."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]
PREFIX = "BRIVA_LEGACY_AUDIT_RESULT="


def probe(data_directory):
    from unittest.mock import patch

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / ".pure-python"))
    os.environ.update({
        "SECRET_KEY": "synthetic-audit-key-at-least-32-characters",
        "BRIVA_DATA_DIR": str(data_directory), "PYTHON_DOTENV_DISABLED": "1",
        "GROQ_API_KEY": "", "OPENAI_API_KEY": "", "BRIVA_ALLOW_LEGACY_WRITES": "0",
    })
    with patch("socket.socket.connect", side_effect=AssertionError("No network in archive audit")), \
            patch("socket.create_connection", side_effect=AssertionError("No network in archive audit")):
        original_stdout = sys.stdout
        import app as module
        sys.stdout = original_stdout
        from models import Product, Sale, User

        app = module.app
        app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        with app.app_context():
            if Path(module.db.engine.url.database).resolve() != (data_directory / "data.db").resolve():
                raise AssertionError("Refusing to write a non-synthetic database")
            module.db.create_all()
            for suffix in ("A", "B"):
                user = User(username="archive_owner_" + suffix, company="ARCHIVE_COMPANY_" + suffix,
                            role="admin", can_view_sales_history=True)
                user.set_password("Synthetic-archive-password-2026")
                module.db.session.add(user)
            module.db.session.commit()
            for suffix in ("A", "B"):
                user = User.query.filter_by(username="archive_owner_" + suffix).one()
                # Legacy codes are only unique within a company. Same code and
                # group identity deliberately test both independent boundaries.
                product = Product(code="SHARED_ARCHIVE_CODE", name="PRIVATE_FOREIGN_PRODUCT" if suffix == "B" else "Own product",
                                  company_id=user.company, stock=1, sizes=[], currency={"TL": 100})
                module.db.session.add(product)
                module.db.session.flush()
                module.db.session.add(Sale(
                    sale_group_id="SHARED_ARCHIVE_GROUP", product_code=product.code, product_id=product.id,
                    user_id=user.id, company_id=user.company, quantity=1, price_at_sale=100,
                    discount=0, total_price=100 if suffix == "A" else 987, currency="TL",
                    customer_name="PRIVATE_FOREIGN_CUSTOMER" if suffix == "B" else "Own customer",
                    sizes=[{"value": "FOREIGN_SIZE" if suffix == "B" else "M", "quantity": 1}],
                ))
            module.db.session.commit()

        def login(suffix):
            browser = app.test_client()
            csrf = browser.get("/api/security/csrf").json["csrf_token"]
            response = browser.post("/login", data={
                "username": "archive_owner_" + suffix, "password": "Synthetic-archive-password-2026", "csrf_token": csrf,
            })
            if response.status_code != 302:
                raise AssertionError("Synthetic login failed")
            return browser

        own, foreign = login("A"), login("B")
        marker = "PRIVATE_ARCHIVE_COMPANY_A_SCAN_MARKER"
        own.get("/query_product", query_string={"code": marker})
        log_response = foreign.get("/logs.json")
        history_response = own.get("/all_sales")
        search_response = own.get("/all_sales", query_string={"search": "PRIVATE_FOREIGN_PRODUCT"})
        report = {
            "raw_log_retired": {
                "status": log_response.status_code,
                "private_marker_exposed": marker in log_response.get_data(as_text=True),
            },
            "shared_group_scope": {"status": history_response.status_code, "body": history_response.json},
            "shared_code_search_scope": {"status": search_response.status_code, "body": search_response.json},
        }
        (data_directory / "regression-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(PREFIX + json.dumps(report))


class LegacyArchiveIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = ROOT / ".verification-data" / ("legacy-audit-regression-" + uuid.uuid4().hex)
        directory.mkdir(parents=True)
        process = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--probe", str(directory)],
                                 cwd=ROOT, text=True, encoding="utf-8", capture_output=True, timeout=120)
        if process.returncode:
            raise AssertionError("Archive probe failed:\n" + process.stdout + process.stderr)
        result = next((line[len(PREFIX):] for line in process.stdout.splitlines() if line.startswith(PREFIX)), None)
        if result is None:
            raise AssertionError("Archive probe did not return its report")
        cls.report = json.loads(result)
        cls.report_path = str(directory / "regression-report.json")
        print("Retained archive audit: " + cls.report_path)

    def test_raw_process_logs_are_retired_for_company_owners(self):
        result = self.report["raw_log_retired"]
        self.assertEqual(result["status"], 410)
        self.assertFalse(result["private_marker_exposed"])

    def test_shared_group_identity_cannot_mix_company_sale_details(self):
        result = self.report["shared_group_scope"]
        self.assertEqual(result["status"], 200)
        groups = result["body"]["sales_groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["customer_name"], "Own customer")
        self.assertEqual(groups[0]["sold_by"], "archive_owner_A")
        self.assertEqual(groups[0]["totals"], {"TL": 100.0})
        self.assertEqual(len(groups[0]["items"]), 1)
        self.assertNotIn("FOREIGN_SIZE", json.dumps(result))

    def test_foreign_product_name_cannot_match_own_shared_product_code(self):
        result = self.report["shared_code_search_scope"]
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["body"]["sales_groups"], [])


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--probe":
        probe(Path(sys.argv[2]).resolve())
    else:
        unittest.main(verbosity=2)
