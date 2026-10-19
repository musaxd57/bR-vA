"""Security regressions against the actual app, isolated in a subprocess.

Only a new retained synthetic data directory is used. No source/environment
file or existing database is modified. No teardown removes files.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULT_PREFIX = "BRIVA_SECURITY_PROBE_RESULT="


def run_probe(data_directory):
    """Run in a fresh process to avoid another test's Flask import/database."""
    from unittest.mock import patch
    sys.path.insert(0, str(PROJECT_ROOT))
    # The workstation's application-control policy blocks the optional native
    # FontTools accelerator. The retained local pure-Python install is the same
    # package/API and avoids weakening that host policy during PDF imports.
    pure_python = PROJECT_ROOT / ".pure-python"
    if pure_python.is_dir():
        sys.path.insert(0, str(pure_python))
    os.environ.update({
        "SECRET_KEY": "synthetic-real-app-security-key-at-least-32-characters",
        "BRIVA_DATA_DIR": str(data_directory),
        "PYTHON_DOTENV_DISABLED": "1",
        "GROQ_API_KEY": "",
        "OPENAI_API_KEY": "",
        "BRIVA_HTTPS": "0",
        "BRIVA_ALLOW_LEGACY_WRITES": "0",
    })
    guards = []
    for target in ("socket.socket.connect", "socket.create_connection"):
        guard = patch(target, side_effect=AssertionError("Security tests cannot contact a network service"))
        guard.start()
        guards.append(guard)
    original_stdout = sys.stdout
    try:
        import app as app_module
    finally:
        sys.stdout = original_stdout
    from models import (
        User, Customer, Category, Color, Shelf, Product, Sale, Order,
        Notification, OrderStatus,
    )
    from briva_security import PERMISSIONS, endpoint_security_report
    from flask import abort

    app = app_module.app
    db = app_module.db
    app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False,
                      BRIVA_LOGIN_ATTEMPTS=100, BRIVA_LOGIN_IP_ATTEMPTS=1000)
    # Existing throttle was initialized during import; synthetic cases create
    # several clients deliberately, so raise only this fixture's IP threshold.
    app.extensions["briva_security"]["throttle"].ip_attempts = 1000
    app.extensions["briva_security"]["throttle"].attempts = 100

    @app.route("/synthetic-security-exception")
    def synthetic_security_exception():
        raise RuntimeError("SYNTHETIC_SECRET_MUST_NOT_BE_SENT_TO_BROWSER")

    expected_database = Path(data_directory).resolve() / "data.db"
    with app.app_context():
        if Path(db.engine.url.database).resolve() != expected_database:
            raise AssertionError("Refusing to touch a non-synthetic database")
        db.create_all()
        for username, role, company, grants in (
            ("security_owner_a", "admin", "SYNTHETIC_A", PERMISSIONS),
            ("security_owner_b", "admin", "SYNTHETIC_B", PERMISSIONS),
            ("security_cashier", "cashier", "SYNTHETIC_A", ("can_make_sale",)),
            ("security_delegated", "cashier", "SYNTHETIC_A", ("can_manage_employees",)),
            ("security_viewer", "cashier", "SYNTHETIC_A", ("can_view_sales_history", "can_manage_customers")),
            ("security_reset", "cashier", "SYNTHETIC_A", ()),
            ("security_catalog", "cashier", "SYNTHETIC_A", ("can_add_product",)),
            ("security_stock", "warehouse", "SYNTHETIC_A", ("can_edit_product",)),
        ):
            user = User(username=username, company=company, role=role)
            user.set_password("Synthetic-security-password-2026")
            for permission in grants:
                setattr(user, permission, True)
            db.session.add(user)
        db.session.commit()
        users = {user.username: user.id for user in User.query.all()}
        db.session.add_all([
            Customer(id=2001, name="Own synthetic customer", company_id="SYNTHETIC_A", debt={"TL": 321.0}),
            Customer(id=2002, name="Foreign synthetic customer", company_id="SYNTHETIC_B", debt={"TL": 987.0}),
            Category(id=2002, name="Foreign category", code="FC", sizes=[], company_id="SYNTHETIC_B"),
            Color(id=2002, name="Foreign color", code="FOREIGN_COLOR", company_id="SYNTHETIC_B"),
            Shelf(id=2002, code="FOREIGN_SHELF", company_id="SYNTHETIC_B"),
            Product(id=2001, code="A_SECURITY_PRODUCT", name="Own security product", company_id="SYNTHETIC_A", stock=1,
                    sizes=[{"value": "M", "number": 1, "stock": 1}], currency={"TL": 100}),
            Product(id=2002, code="B_SECURITY_PRODUCT", name="Foreign security product", company_id="SYNTHETIC_B", stock=1,
                    sizes=[{"value": "M", "number": 1, "stock": 1}], currency={"TL": 100}),
            Sale(sale_group_id="synthetic-sale", product_code="A_SECURITY_PRODUCT", product_id=2001,
                 user_id=users["security_owner_a"], company_id="SYNTHETIC_A", quantity=1,
                 price_at_sale=100, discount=0, total_price=100, currency="TL",
                 customer_name="PRIVATE_CUSTOMER_HISTORY_MARKER"),
            Order(id=2002, company_id="SYNTHETIC_B", user_id=users["security_owner_b"],
                  customer_name="Foreign order customer", items=[], total_amount=0, currency="TL", status=OrderStatus.PENDING),
            Notification(id=2002, user_id=users["security_owner_b"], company_id="SYNTHETIC_B",
                         title="Foreign notification", message="Private", is_read=False),
        ])
        db.session.commit()

    results = {}
    core_state = {}
    client = app.test_client()

    def assert_equal(actual, expected, note=""):
        if actual != expected:
            raise AssertionError(f"{note}: expected {expected!r}, got {actual!r}")

    def token(browser):
        response = browser.get("/api/security/csrf")
        assert_equal(response.status_code, 200, "CSRF bootstrap")
        return response.json["csrf_token"]

    def login(username="security_owner_a", browser=None):
        browser = browser or app.test_client()
        response = browser.post("/login", data={
            "username": username, "password": "Synthetic-security-password-2026",
            "csrf_token": token(browser),
        })
        assert_equal(response.status_code, 302, "Actual password authentication")
        return browser, {"X-CSRF-Token": token(browser)}

    def case(name, function):
        try:
            detail = function()
            results[name] = {"ok": True, "detail": detail}
        except Exception as error:
            results[name] = {"ok": False, "error": f"{type(error).__name__}: {error}"}

    def anonymous_route_inventory():
        import re
        checked = []
        allowed = {"static", "main", "login", "security_csrf"}
        for entry in endpoint_security_report(app):
            if entry["endpoint"] in allowed:
                continue
            route = re.sub(r"<(?:(?:[^:>]+):)?([^>]+)>", "999999", entry["route"])
            for method in entry["methods"]:
                response = client.open(route, method=method, json={} if method not in {"GET", "HEAD"} else None)
                if response.status_code not in {302, 400, 401, 403, 404, 405, 410, 503}:
                    raise AssertionError(f"Anonymous {method} {route} returned {response.status_code}")
                if response.status_code == 400 and response.is_json and response.json.get("code") != "csrf_failed":
                    raise AssertionError(f"Anonymous {route} reached a payload-validation handler")
                checked.append(f"{method} {entry['route']}")
        return {"checked_method_routes": len(checked), "inventory": endpoint_security_report(app)}

    def actual_login_form_and_csrf():
        response = client.get("/login")
        html = response.get_data(as_text=True)
        if 'name="csrf_token"' not in html:
            raise AssertionError("Login form does not expose its required CSRF token")
        response = client.post("/login", data={"username": "security_owner_a", "password": "Synthetic-security-password-2026"})
        assert_equal(response.status_code, 400, "Login CSRF")
        browser, headers = login()
        response = browser.post("/update_profile", json={"username": "security_owner_a", "phone": ""})
        assert_equal(response.status_code, 400, "Profile CSRF")
        if "csrf_failed" not in response.get_data(as_text=True):
            raise AssertionError("Missing token was not rejected by security layer")
        return "Actual hashed-password login and profile CSRF verified"

    def foreign_resources():
        browser, headers = login()
        probes = [
            ("DELETE", "/delete_customer/2002", None),
            ("DELETE", "/delete_color/FOREIGN_COLOR", None),
            ("DELETE", "/delete_category/2002", None),
            ("DELETE", "/delete_shelf/2002", None),
            ("DELETE", "/product/delete/2002", None),
            ("POST", "/manage_order", {"order_id": 2002, "action": "approve"}),
            ("POST", "/collect_payment", {"customer_id": 2002, "amount": 1, "currency": "TL"}),
            ("GET", "/get_customer_debt_history/2002", None),
            ("GET", "/get_picking_list/2002", None),
            ("GET", "/generate_order_pdf/2002", None),
            ("GET", "/product_detail/2002", None),
            ("DELETE", f"/deleteuser/{users['security_owner_b']}", None),
            ("GET", f"/get_user_permissions/{users['security_owner_b']}", None),
            ("POST", f"/update_user_permissions/{users['security_owner_b']}", {"can_make_sale": False}),
            ("POST", "/send_employee_message", {"user_id": users["security_owner_b"], "title": "Synthetic", "message": "Synthetic"}),
        ]
        for method, path, payload in probes:
            response = browser.open(path, method=method, json=payload, headers=headers)
            if response.status_code not in {403, 404, 410}:
                raise AssertionError(f"Foreign {method} {path} returned {response.status_code}")
        with app.app_context():
            assert_equal(db.session.get(Customer, 2002).debt, {"TL": 987.0}, "Foreign debt unchanged")
            assert_equal(db.session.get(Product, 2002).stock, 1, "Foreign stock unchanged")
            assert_equal(db.session.get(Order, 2002).status, OrderStatus.PENDING, "Foreign order unchanged")
        return f"{len(probes)} actual foreign-object routes denied"

    def employee_escalation():
        browser, headers = login("security_delegated")
        response = browser.post("/add_user_py", json={
            "name": "synthetic_escalated_owner", "password": "Synthetic-new-employee-password",
            "role": "admin",
        }, headers=headers)
        assert_equal(response.status_code, 400, "Delegated employee cannot create admin")
        response = browser.post(f"/update_user_permissions/{users['security_cashier']}",
                                json={"can_manage_debts": True}, headers=headers)
        if response.status_code not in {400, 403}:
            raise AssertionError("Delegated employee can grant financial permission")
        with app.app_context():
            assert_equal(User.query.filter_by(username="synthetic_escalated_owner").count(), 0)
            assert_equal(db.session.get(User, users["security_cashier"]).can_manage_debts, False)
        return "No global admin creation or delegated financial grants"

    def owner_list_isolation():
        browser, _ = login()
        response = browser.get("/getallusers")
        assert_equal(response.status_code, 200)
        if "security_owner_b" in response.get_data(as_text=True):
            raise AssertionError("Owner user list exposes another company")
        response = browser.get("/get_all_companys")
        assert_equal(response.status_code, 200)
        if "SYNTHETIC_B" in response.get_data(as_text=True):
            raise AssertionError("Owner company list exposes another company")
        delegated, _ = login("security_delegated")
        assert_equal(delegated.get("/getallusers").status_code, 403)
        return "Owner lists are company-scoped; employee list route is restricted"

    def sensitive_history_and_debt():
        browser, _ = login("security_cashier")
        response = browser.get("/query_product?code=A_SECURITY_PRODUCT")
        assert_equal(response.status_code, 200)
        if "PRIVATE_CUSTOMER_HISTORY_MARKER" in response.get_data(as_text=True):
            raise AssertionError("Barcode lookup exposes customer history without history permission")
        response = browser.get("/product_detail/2001")
        assert_equal(response.status_code, 200)
        if "PRIVATE_CUSTOMER_HISTORY_MARKER" in response.get_data(as_text=True):
            raise AssertionError("Product detail exposes customer history without permission")
        viewer, _ = login("security_viewer")
        response = viewer.get("/get_all_customers")
        assert_equal(response.status_code, 200)
        if "321.0" in response.get_data(as_text=True):
            raise AssertionError("Customer list exposes debt without finance permission")
        return "Barcode/detail history and customer-debt data are permission-scoped"

    def actual_password_invalidation():
        first, first_headers = login("security_reset")
        second, _ = login("security_reset")
        response = first.post("/change_password", json={
            "current_password": "Synthetic-security-password-2026",
            "new_password": "Synthetic-updated-password-2026",
        }, headers=first_headers)
        assert_equal(response.status_code, 200, "Actual password update")
        assert_equal(first.get("/get_current_user_data").status_code, 200, "Changing browser remains signed in")
        assert_equal(second.get("/get_current_user_data").status_code, 401, "Other session is invalidated")
        response = first.post("/update_profile", json={"username": "security_reset", "phone": ""}, headers=first_headers)
        assert_equal(response.status_code, 400, "Password update rotates old CSRF token")
        return "Credential binding and CSRF rotation verified on actual password route"

    def profile_input_validation():
        browser, headers = login("security_cashier")
        for payload in ([], "invalid", None, {"role": "admin", "username": "security_cashier"},
                        {"username": "bad\nname", "phone": ""}, {"username": "x", "phone": ""},
                        {"username": "security_cashier", "phone": "1" * 16}):
            response = browser.post("/update_profile", json=payload, headers=headers)
            assert_equal(response.status_code, 400, "Invalid profile is rejected")
        for payload in ([], "invalid", {"current_password": [], "new_password": "Synthetic-new-password-2026"},
                        {"current_password": "Synthetic-security-password-2026", "new_password": "short"},
                        {"current_password": "Synthetic-security-password-2026", "new_password": "Synthetic-new-password-2026", "role": "admin"}):
            response = browser.post("/change_password", json=payload, headers=headers)
            assert_equal(response.status_code, 400, "Invalid password payload is rejected")
        with app.app_context():
            user = db.session.get(User, users["security_cashier"])
            assert_equal(user.username, "security_cashier")
            assert_equal(user.role, "cashier")
            if not user.check_password("Synthetic-security-password-2026"):
                raise AssertionError("Rejected payload changed the password")
        return "Profile/password whitelist, types, control characters and length limits enforced"

    def logout_and_exception():
        browser, headers = login()
        response = browser.get("/logout")
        if response.status_code not in {200, 405}:
            raise AssertionError(f"Unexpected logout confirmation {response.status_code}")
        assert_equal(browser.get("/get_current_user_data").status_code, 200, "GET logout must be read-only")
        response = browser.get("/synthetic-security-exception")
        assert_equal(response.status_code, 500)
        body = response.get_data(as_text=True)
        if "SYNTHETIC_SECRET_MUST_NOT_BE_SENT_TO_BROWSER" in body or "Traceback" in body:
            raise AssertionError("Unhandled exceptions disclose internal diagnostics")
        for header in ("Content-Security-Policy", "X-Frame-Options", "X-Content-Type-Options", "X-Request-ID"):
            if header not in response.headers:
                raise AssertionError(f"500 sanitization dropped security header {header}")
        response = browser.post("/logout", headers=headers)
        assert_equal(response.status_code, 302)
        assert_equal(browser.get("/get_current_user_data").status_code, 401)
        return "GET logout is safe; POST logout clears session; errors are generic"

    def headers_and_host():
        response = client.get("/login")
        for name in ("Content-Security-Policy", "X-Content-Type-Options", "X-Frame-Options", "Permissions-Policy"):
            if name not in response.headers:
                raise AssertionError(f"Missing actual response header {name}")
        if "no-store" not in response.headers["Cache-Control"]:
            raise AssertionError("Login/token responses are cacheable")
        response = client.get("/login", headers={"Host": "untrusted.invalid"})
        assert_equal(response.status_code, 400, "Untrusted Host is rejected")
        return "CSP/privacy headers and Host allowlist verified"

    def core_tenant_and_idempotency():
        owner, headers = login()
        foreign, foreign_headers = login("security_owner_b")
        base = {"model_code": "SECURITY-CORE", "name": "Own core model",
                "colors": [{"code": "SY", "name": "Siyah"}], "sizes": ["M"],
                "unit_prices": {"TRY": "100.00"}}
        path = "/api/v2/catalog/models"
        response = owner.post(path, json=base, headers=headers)
        assert_equal(response.status_code, 400, "Command requires an idempotency key")
        assert_equal(response.json["error"]["code"], "idempotency_required")
        command_headers = {**headers, "Idempotency-Key": "security-core-model-create"}
        response = owner.post(path, json={**base, "company_id": "SYNTHETIC_B"}, headers=command_headers)
        assert_equal(response.status_code, 400, "Caller cannot select a tenant")
        assert_equal(response.json["error"]["code"], "tenant_field")
        response = owner.post(path, json=base, headers=command_headers)
        assert_equal(response.status_code, 201, "First real core model command")
        own_model = response.json["data"]
        replay = owner.post(path, json=base, headers=command_headers)
        assert_equal(replay.status_code, 200, "Same command safely replays")
        assert_equal(replay.json["replayed"], True)
        assert_equal(replay.json["data"]["id"], own_model["id"])
        response = owner.post(path, json={**base, "name": "Changed retry payload"}, headers=command_headers)
        assert_equal(response.status_code, 409, "Same key with changed payload is a conflict")
        assert_equal(response.json["error"]["code"], "idempotency_conflict")
        catalog_user, catalog_headers = login("security_catalog")
        response = catalog_user.post(path, json=base, headers={**catalog_headers, "Idempotency-Key": "security-core-model-create"})
        assert_equal(response.status_code, 409, "Another actor cannot replay a command result")
        assert_equal(response.json["error"]["code"], "idempotency_owner")
        response = foreign.post(path, json={**base, "name": "FOREIGN_CORE_MODEL_MARKER"},
                                headers={**foreign_headers, "Idempotency-Key": "security-core-model-create"})
        assert_equal(response.status_code, 201, "Tenant-scoped keys are independent")
        foreign_model = response.json["data"]
        for browser, credential, key in ((owner, headers, "own"), (foreign, foreign_headers, "foreign")):
            response = browser.post("/api/v2/locations", json={"code": "SECURITY-STORE", "name": "Security store", "kind": "store"},
                                    headers={**credential, "Idempotency-Key": "security-core-location-create"})
            assert_equal(response.status_code, 201)
            core_state[key + "_location"] = response.json["data"]
        core_state.update(owner=owner, headers=headers, own_model=own_model, foreign_model=foreign_model)
        foreign_id = foreign_model["id"]
        response = owner.patch(f"/api/v2/catalog/models/{foreign_id}", json={"name": "Forbidden rename"},
                               headers={**headers, "Idempotency-Key": "security-core-foreign-edit"})
        assert_equal(response.status_code, 404, "Foreign core model mutation")
        foreign_barcode = foreign_model["variants"][0]["barcode"]
        assert_equal(owner.get(f"/api/v2/barcodes/{foreign_barcode}").status_code, 404)
        response = owner.get("/api/v2/catalog")
        if "FOREIGN_CORE_MODEL_MARKER" in response.get_data(as_text=True):
            raise AssertionError("Core catalogue exposes foreign rows")
        response = owner.get(f"/api/v2/stock?location_id={core_state['foreign_location']['id']}")
        assert_equal(response.status_code, 404)
        return "Core commands bind tenant, actor, payload and operation; foreign rows never resolve"

    def core_permissions_and_finance():
        owner, headers = core_state["owner"], core_state["headers"]
        model = core_state["own_model"]
        variant = model["variants"][0]
        location = core_state["own_location"]["id"]
        catalog_user, catalog_headers = login("security_catalog")
        for method, path, payload in (
            ("POST", "/api/v2/locations", {"code": "DENIED", "name": "Denied", "kind": "store"}),
            ("POST", "/api/v2/receipts", {"location_id": location, "reference": "denied", "items": []}),
            ("PATCH", f"/api/v2/catalog/models/{model['id']}", {"name": "Denied edit"}),
            ("PATCH", f"/api/v2/catalog/variants/{variant['id']}/prices", {"prices": {"TRY": "1.00"}, "reason": "Denied price"}),
        ):
            response = catalog_user.open(path, method=method, json=payload,
                                         headers={**catalog_headers, "Idempotency-Key": "security-denied-" + path.rsplit('/', 1)[-1]})
            assert_equal(response.status_code, 403, "Product-create permission cannot edit, adjust stock or price")
        response = owner.post("/api/v2/receipts", json={"location_id": location, "reference": "security-receipt",
                              "items": [{"variant_id": variant["id"], "quantity": 5}]},
                              headers={**headers, "Idempotency-Key": "security-core-receipt"})
        assert_equal(response.status_code, 201)
        response = owner.post("/api/v2/customers", json={"name": "Security core customer", "phone": "0000000000"},
                              headers={**headers, "Idempotency-Key": "security-core-customer"})
        assert_equal(response.status_code, 201)
        customer_id = response.json["data"]["id"]
        sale_payload = {"location_id": location, "customer_id": customer_id, "currency": "TRY",
                        "items": [{"barcode": variant["barcode"], "quantity": 1}],
                        "payment": {"amount": "40.00", "method": "cash"}}
        response = owner.post("/api/v2/sales", json=sale_payload,
                              headers={**headers, "Idempotency-Key": "security-owner-sale"})
        assert_equal(response.status_code, 201)
        owner_sale = response.json["data"]["id"]
        cashier, cashier_headers = login("security_cashier")
        bootstrap = cashier.get("/api/v2/bootstrap")
        assert_equal(bootstrap.status_code, 200)
        assert_equal(bootstrap.json["data"]["sales"], [], "Selling is not permission to browse company history")
        assert_equal(cashier.get(f"/api/v2/sales/{owner_sale}").status_code, 403)
        for path in ("/api/v2/audit", "/api/v2/ledger", f"/api/v2/customers/{customer_id}/account"):
            assert_equal(cashier.get(path).status_code, 403, "Finance/audit read requires explicit grant")
        overridden = {**sale_payload, "items": [{"barcode": variant["barcode"], "quantity": 1,
                                                   "unit_price": "1.00", "price_reason": "Unauthorized override"}],
                      "payment": {"amount": "1.00", "method": "cash"}}
        response = cashier.post("/api/v2/sales", json=overridden,
                                headers={**cashier_headers, "Idempotency-Key": "security-cashier-price-override"})
        assert_equal(response.status_code, 403)
        response = cashier.post("/api/v2/sales", json=sale_payload,
                                headers={**cashier_headers, "Idempotency-Key": "security-cashier-sale"})
        assert_equal(response.status_code, 201)
        own_sale = response.json["data"]
        assert_equal(own_sale["customer_balances"], {}, "Sale result must not disclose the customer's full account")
        response = cashier.get(f"/api/v2/sales/{own_sale['id']}")
        assert_equal(response.status_code, 200, "Cashier can verify their own receipt")
        assert_equal(response.json["data"]["customer_balances"], {})
        response = owner.get(f"/api/v2/customers/{customer_id}/account")
        assert_equal(response.status_code, 200)
        assert_equal(response.json["data"]["customer"]["balances"]["TRY"], "120.00")
        return "Create/edit/inventory/history/price/finance permissions are separate; cashier only sees their own receipt"

    def core_query_and_svg():
        owner = core_state["owner"]
        headers = {"X-CSRF-Token": token(owner), "Idempotency-Key": "security-core-deep-json"}
        deep = '{"name":' + '[' * 3500 + '0' + ']' * 3500 + '}'
        response = owner.post("/api/v2/locations", data=deep, content_type="application/json", headers=headers)
        assert_equal(response.status_code, 400, "Deep JSON must reach the core parser's safe boundary")
        assert_equal(response.json["error"]["code"], "invalid_json")
        oversized = '{"name":"' + 'x' * 513000 + '"}'
        response = owner.post("/api/v2/locations", data=oversized, content_type="application/json", headers=headers)
        assert_equal(response.status_code, 413, "Core size limit must run before JSON parsing")
        for value in ("0", "-1", "1.2", "²", "９", "9" * 50):
            response = owner.get("/api/v2/stock", query_string={"location_id": value})
            assert_equal(response.status_code, 400, "Invalid query ID must not reach SQLite")
        barcode = core_state["own_model"]["variants"][0]["barcode"]
        response = owner.get(f"/api/v2/barcodes/{barcode}.svg")
        assert_equal(response.status_code, 200)
        assert_equal(response.mimetype, "image/svg+xml")
        if "sandbox" not in response.headers.get("Content-Security-Policy", ""):
            raise AssertionError("Shared headers weakened SVG's sandbox policy")
        return "Query IDs reject Unicode/overflow; core JSON limits precede decoding; SVG retains strict policy"

    def employee_access_preserves_history():
        from briva_auth_models import AuthAccess
        owner, headers = login()
        employee, employee_headers = login("security_delegated")
        stolen = employee.get_cookie("session").value
        target_id = users["security_delegated"]
        # Actual compatibility endpoint must deactivate rather than delete a
        # User referenced by server sessions and historical transactions.
        response = owner.delete(f"/deleteuser/{target_id}", headers=headers)
        assert_equal(response.status_code, 204)
        assert_equal(employee.get("/get_current_user_data").status_code, 401)
        with app.app_context():
            target = db.session.get(User, target_id)
            if target is None:
                raise AssertionError("Access closure deleted the historical user identity")
            assert_equal(target.role, "cashier")
            assert_equal(target.company, "SYNTHETIC_A")
            if db.session.get(AuthAccess, target_id).disabled_at is None:
                raise AssertionError("User was not marked disabled")
        listed = owner.get("/get_employees").json
        row = next(item for item in listed if item["id"] == target_id)
        assert_equal(row["active"], False)
        response = owner.post(f"/api/account-security/employees/{target_id}/access",
                              json={"enabled": True, "reason": "Sentetik yeniden erişim"}, headers=headers)
        assert_equal(response.status_code, 200)
        replay = app.test_client()
        replay.set_cookie("session", stolen)
        assert_equal(replay.get("/get_current_user_data").status_code, 401, "Reactivation never revives old cookies")
        employee, _ = login("security_delegated")
        assert_equal(employee.get("/get_current_user_data").status_code, 200)
        return "Actual DELETE preserves User; owner reactivation requires fresh authentication; active flag is exposed"

    for name, function in (
        ("anonymous_inventory", anonymous_route_inventory),
        ("login_form_csrf", actual_login_form_and_csrf),
        ("foreign_resources", foreign_resources),
        ("employee_escalation", employee_escalation),
        ("owner_list_isolation", owner_list_isolation),
        ("sensitive_history", sensitive_history_and_debt),
        ("password_invalidation", actual_password_invalidation),
        ("profile_validation", profile_input_validation),
        ("logout_and_errors", logout_and_exception),
        ("headers_and_host", headers_and_host),
        ("core_tenant_and_idempotency", core_tenant_and_idempotency),
        ("core_permissions_and_finance", core_permissions_and_finance),
        ("core_query_and_svg", core_query_and_svg),
        ("employee_access", employee_access_preserves_history),
    ):
        case(name, function)
    with app.app_context():
        db.session.remove()
        db.engine.dispose()
    for guard in guards:
        guard.stop()
    print(RESULT_PREFIX + json.dumps(results, ensure_ascii=True))


class SecurityActualAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Retained on purpose: the user's no-file-deletion rule applies to test
        # artifacts too. The path is printed only on failure for inspection.
        cls.data_directory = tempfile.mkdtemp(prefix="briva-security-real-app-")
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--probe", cls.data_directory],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=90,
        )
        markers = [line for line in result.stdout.splitlines() if line.startswith(RESULT_PREFIX)]
        if result.returncode != 0 or not markers:
            raise AssertionError(
                f"Real-app security probe failed; retained {cls.data_directory}\n"
                f"{result.stdout[-4000:]}\n{result.stderr[-4000:]}"
            )
        cls.results = json.loads(markers[-1][len(RESULT_PREFIX):])

    def check_case(self, name):
        result = self.results[name]
        self.assertTrue(result["ok"], f"{result.get('error')}; retained {self.data_directory}")

    def test_01_anonymous_inventory(self):
        self.check_case("anonymous_inventory")

    def test_02_actual_login_form_and_csrf(self):
        self.check_case("login_form_csrf")

    def test_03_foreign_resources(self):
        self.check_case("foreign_resources")

    def test_04_employee_escalation(self):
        self.check_case("employee_escalation")

    def test_05_owner_list_isolation(self):
        self.check_case("owner_list_isolation")

    def test_06_sensitive_history_and_debt(self):
        self.check_case("sensitive_history")

    def test_07_password_invalidation(self):
        self.check_case("password_invalidation")

    def test_08_logout_and_exception(self):
        self.check_case("logout_and_errors")

    def test_09_headers_and_host(self):
        self.check_case("headers_and_host")

    def test_10_profile_input_validation(self):
        self.check_case("profile_validation")

    def test_11_core_tenant_and_idempotency(self):
        self.check_case("core_tenant_and_idempotency")

    def test_12_core_permissions_and_finance(self):
        self.check_case("core_permissions_and_finance")

    def test_13_core_query_and_svg(self):
        self.check_case("core_query_and_svg")

    def test_14_employee_access_preserves_history(self):
        self.check_case("employee_access")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--probe":
        run_probe(sys.argv[2])
    else:
        unittest.main(verbosity=2)
