"""Real Flask requests against fresh in-memory SQL databases.

Run: python -m unittest discover -s tests -p 'test_security_*.py'
These tests never import the legacy app, read .env, use an existing database,
delete files, drop tables, or contact a network service.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import Mock, patch

from flask import Flask, g, jsonify, request, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from briva_security import (
    EMPLOYEE_ROLES, PERMISSIONS, LoginThrottle, SecurityValidationError,
    client_address, current_user, endpoint_security_report, establish_session,
    has_permission, init_security, require_company_resource, require_owner,
    require_permission, validate_employee_payload, validate_password,
    validate_permissions_payload,
)


class SecurityHooksTests(unittest.TestCase):
    def setUp(self):
        # A new application/database per test prevents order-dependent grants.
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="synthetic-session-key-for-security-tests-2026",
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            BRIVA_LOGIN_ATTEMPTS=3,
            BRIVA_LOGIN_IP_ATTEMPTS=7,
        )
        self.db = SQLAlchemy(self.app)
        db = self.db

        class User(db.Model):
            id = db.Column(db.Integer, primary_key=True)
            username = db.Column(db.String(100), nullable=False, unique=True)
            password_hash = db.Column(db.String(256), nullable=False)
            company = db.Column(db.String(100), nullable=False)
            role = db.Column(db.String(50), nullable=False)
            phone = db.Column(db.String(30))
            is_active = db.Column(db.Boolean, default=True)
            for permission in PERMISSIONS:
                locals()[permission] = db.Column(db.Boolean, default=False)

        class Resource(db.Model):
            id = db.Column(db.Integer, primary_key=True)
            code = db.Column(db.String(30), nullable=False)
            company_id = db.Column(db.String(100), nullable=False)
            image_path = db.Column(db.String(255))

        self.User = User
        self.Resource = Resource
        self.writes = []

        @self.app.route("/login", methods=["GET", "POST"])
        def login():
            if request.method == "GET":
                return "Login"
            username = request.form.get("username")
            user = User.query.filter_by(username=username).first()
            # Controlled synthetic auth boundary; no test accepts passwords as
            # identities. Real app verifies its hash before this shared method.
            if user is not None and request.form.get("password") == "Synthetic-password-2026":
                establish_session(user)
                return "Logged in", 302
            return "Invalid credentials", 401

        @self.app.route("/")
        def main():
            return "Home"

        @self.app.route("/profile")
        def profile():
            return "Profile"

        @self.app.route("/identity")
        def identity():
            user = current_user()
            return jsonify(id=user.id, role=session["role"], company=session["company"])

        @self.app.route("/sale", methods=["POST"], endpoint="create_sale")
        def sale():
            self.writes.append("sale")
            return jsonify(ok=True)

        @self.app.route("/delete_customer/<int:customer_id>", methods=["DELETE"])
        def delete_customer(customer_id):
            self.writes.append(("customer", customer_id))
            return jsonify(ok=True)

        @self.app.route("/manage_order", methods=["POST"], endpoint="manage_order_route")
        def manage_order():
            self.writes.append(("order", request.json["order_id"]))
            return jsonify(ok=True)

        @self.app.route("/getallusers", endpoint="get_all_users")
        def all_users():
            return jsonify(users=[user.id for user in User.query.all()])

        @self.app.route("/add_user_py", methods=["POST"])
        def add_user_py():
            self.writes.append(("employee", request.json))
            return jsonify(ok=True)

        @self.app.route("/get_user_permissions/<int:user_id>")
        def get_user_permissions(user_id):
            return jsonify(id=g.briva_target_user.id)

        @self.app.route("/update_user_permissions/<int:user_id>", methods=["POST"])
        def update_user_permissions(user_id):
            self.writes.append(("permissions", user_id))
            return jsonify(ok=True)

        @self.app.route("/deleteuser/<int:user_id>", methods=["DELETE"], endpoint="delete_user_route")
        def delete_user(user_id):
            self.writes.append(("delete_user", user_id))
            return jsonify(ok=True)

        @self.app.route("/send_employee_message", methods=["POST"])
        def send_employee_message():
            self.writes.append(("message", request.json["user_id"]))
            return jsonify(ok=True)

        @self.app.route("/api/upload_excel_step1", methods=["POST"], endpoint="upload_excel_step1")
        def import_excel():
            raise AssertionError("Retired route must not execute")

        @self.app.route("/api/regenerate_all_barcodes", methods=["POST"], endpoint="api_regenerate_all_barcodes")
        def regenerate():
            raise AssertionError("Retired route must not execute")

        @self.app.route("/chat_ai", methods=["POST"])
        def chat_ai():
            raise AssertionError("Disabled AI must not call a provider")

        @self.app.route("/secured-by-decorator", methods=["POST"])
        @require_permission("can_edit_product")
        def decorated():
            self.writes.append("decorated")
            return jsonify(ok=True)

        @self.app.route("/owner-by-decorator")
        @require_owner
        def owner_decorated():
            return jsonify(ok=True)

        @self.app.route("/peer")
        def peer():
            return jsonify(peer=client_address())

        @self.app.route("/strict-svg-policy")
        def strict_svg():
            response = self.app.response_class("<svg></svg>", mimetype="image/svg+xml")
            response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
            return response

        init_security(self.app, db, User, resource_models={
            "Customer": Resource, "Category": Resource, "Color": Resource,
            "Shelf": Resource, "Product": Resource, "Order": Resource,
        })

        with self.app.app_context():
            db.create_all()
            password_hash = generate_password_hash("Synthetic-password-2026")
            for username, role, company, grants in (
                ("owner", "admin", "A", ()),
                ("manager", "manager", "A", ("can_manage_employees", "can_manage_customers", "can_orders")),
                ("cashier", "cashier", "A", ("can_make_sale",)),
                ("delegated", "cashier", "A", ("can_manage_employees",)),
                ("foreign", "cashier", "B", ("can_make_sale",)),
            ):
                user = User(username=username, role=role, company=company,
                            password_hash=password_hash)
                for grant in grants:
                    setattr(user, grant, True)
                db.session.add(user)
            db.session.add_all([
                Resource(id=1, code="OWN", company_id="A", image_path="uploads/own.png"),
                Resource(id=2, code="FOREIGN", company_id="B", image_path="uploads/foreign.png"),
            ])
            db.session.commit()
            self.ids = {u.username: u.id for u in User.query.all()}
        self.client = self.app.test_client()

    def tearDown(self):
        # Releasing an in-memory connection deletes no filesystem objects.
        with self.app.app_context():
            self.db.session.remove()
            self.db.engine.dispose()

    def token(self, client=None):
        response = (client or self.client).get("/api/security/csrf")
        self.assertEqual(response.status_code, 200)
        return response.json["csrf_token"]

    def login(self, username="owner", *, client=None):
        client = client or self.client
        previous = self.token(client)
        response = client.post("/login", data={
            "csrf_token": previous, "username": username,
            "password": "Synthetic-password-2026",
        })
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        token = self.token(client)
        self.assertNotEqual(previous, token, "Login must rotate the CSRF token")
        return {"X-CSRF-Token": token}

    def test_every_unknown_application_route_requires_live_login(self):
        self.assertEqual(self.client.get("/identity").status_code, 401)
        response = self.client.get("/profile")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/login")
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_login_is_csrf_protected(self):
        response = self.client.post("/login", data={
            "username": "owner", "password": "Synthetic-password-2026",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["code"], "csrf_failed")
        with self.client.session_transaction() as cookie:
            self.assertNotIn("user", cookie)

    def test_oversized_or_control_character_login_never_reaches_password_lookup(self):
        boundary = Mock(side_effect=AssertionError("Invalid credentials reached the login handler"))
        self.app.view_functions["login"] = boundary
        csrf = self.token()
        for username, password in (("x" * 101, "Synthetic-password-2026"),
                                   ("owner", "x" * 257), ("owner\nadmin", "Synthetic-password-2026")):
            response = self.client.post("/login", data={"username": username, "password": password, "csrf_token": csrf})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json["code"], "invalid_login_payload")
        response = self.client.post("/login", data={"username": "owner", "password": "x" * 9000})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json["code"], "login_payload_too_large")
        boundary.assert_not_called()

    def test_missing_wrong_foreign_and_stale_tokens_do_not_mutate(self):
        old = self.token()
        headers = self.login("cashier")
        scenarios = (
            {}, {"X-CSRF-Token": "wrong"}, {"X-CSRF-Token": old},
            {**headers, "Origin": "https://foreign.example"},
            {**headers, "Sec-Fetch-Site": "cross-site"},
        )
        for supplied in scenarios:
            response = self.client.post("/sale", json={}, headers=supplied)
            self.assertEqual(response.status_code, 400, supplied)
        self.assertEqual(self.writes, [])
        self.assertEqual(self.client.post("/sale", json={}, headers=headers).status_code, 200)
        self.assertEqual(self.writes, ["sale"])

    def test_another_browser_token_cannot_be_reused(self):
        headers = self.login("cashier")
        other = self.app.test_client()
        self.login("cashier", client=other)
        response = other.post("/sale", json={}, headers=headers)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.writes, [])

    def test_non_ascii_csrf_token_is_rejected_without_a_server_error(self):
        self.login("cashier")
        response = self.client.post("/sale", data={"csrf_token": "ş" * 43})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["code"], "csrf_failed")

    def test_uploaded_images_are_private_and_company_scoped(self):
        # Replace only the file sender: assertions exercise the installed hook
        # before it runs, without constructing or deleting image fixtures.
        self.app.view_functions["static"] = lambda filename: jsonify(filename=filename)
        self.assertEqual(self.client.get("/static/app.css").status_code, 200)
        self.assertEqual(self.client.get("/static/uploads/own.png").status_code, 401)
        self.assertEqual(self.client.get("/static/js/../uploads/own.png").status_code, 401)
        self.assertEqual(self.client.get("/static/js/%2e%2e/uploads/own.png").status_code, 401)
        self.login()
        self.assertEqual(self.client.get("/static/uploads/own.png").status_code, 200)
        self.assertEqual(self.client.get("/static/uploads/foreign.png").status_code, 404)
        self.assertEqual(self.client.get("/static/uploads/missing.png").status_code, 404)
        self.assertEqual(self.client.get("/static/js/../uploads/foreign.png").status_code, 404)
        self.app.extensions["briva_security"]["resource_models"].pop("Product")
        self.assertEqual(self.client.get("/static/uploads/own.png").status_code, 503)
        self.assertEqual(self.client.get("/static/app.css").status_code, 200)

    def test_origin_default_port_is_normalized_but_not_hostname_or_scheme(self):
        headers = self.login("cashier")
        good = self.client.post("/sale", json={}, headers={**headers, "Origin": "http://localhost:80"})
        self.assertEqual(good.status_code, 200)
        for origin in ("null", "https://localhost", "http://localhost.evil.example", "http://owner@localhost"):
            response = self.client.post("/sale", json={}, headers={**headers, "Origin": origin})
            self.assertEqual(response.status_code, 400, origin)

    def test_permissions_are_read_live_not_from_cookie_role(self):
        headers = self.login("cashier")
        self.assertEqual(self.client.post("/sale", json={}, headers=headers).status_code, 200)
        with self.app.app_context():
            user = self.db.session.get(self.User, self.ids["cashier"])
            user.can_make_sale = False
            self.db.session.commit()
        self.assertEqual(self.client.post("/sale", json={}, headers=headers).status_code, 403)
        self.assertEqual(len(self.writes), 1)

    def test_owner_demotion_immediately_changes_legacy_session_role(self):
        self.login()
        self.assertEqual(self.client.get("/getallusers").status_code, 200)
        with self.app.app_context():
            user = self.db.session.get(self.User, self.ids["owner"])
            user.role = "cashier"
            self.db.session.commit()
        self.assertEqual(self.client.get("/getallusers").status_code, 403)
        self.assertEqual(self.client.get("/identity").json["role"], "cashier")

    def test_password_change_invalidates_other_cookies(self):
        self.login()
        with self.app.app_context():
            user = self.db.session.get(self.User, self.ids["owner"])
            user.password_hash = generate_password_hash("A-new-synthetic-password")
            self.db.session.commit()
        self.assertEqual(self.client.get("/identity").status_code, 401)
        with self.client.session_transaction() as cookie:
            self.assertNotIn("user", cookie)

    def test_company_change_and_disabled_account_invalidate_session(self):
        for field, value in (("company", "B"), ("is_active", False)):
            with self.subTest(field=field):
                self.login("cashier")
                with self.app.app_context():
                    user = self.db.session.get(self.User, self.ids["cashier"])
                    previous = getattr(user, field)
                    setattr(user, field, value)
                    self.db.session.commit()
                self.assertEqual(self.client.get("/identity").status_code, 401)
                with self.app.app_context():
                    user = self.db.session.get(self.User, self.ids["cashier"])
                    setattr(user, field, previous)
                    self.db.session.commit()

    def test_missing_credential_binding_and_idle_expiry_fail_closed(self):
        self.login()
        with self.client.session_transaction() as cookie:
            del cookie["_credential_fingerprint"]
        self.assertEqual(self.client.get("/identity").status_code, 401)
        self.login()
        with self.client.session_transaction() as cookie:
            cookie["_last_seen"] = int(time.time()) - 9 * 3600
        self.assertEqual(self.client.get("/identity").status_code, 401)

    def test_company_scope_matches_missing_and_foreign_resources(self):
        headers = self.login("manager")
        for identity in (2, 999999):
            response = self.client.delete(f"/delete_customer/{identity}", headers=headers)
            self.assertEqual(response.status_code, 404)
        self.assertEqual(self.writes, [])
        response = self.client.delete("/delete_customer/1", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.writes, [("customer", 1)])

    def test_foreign_order_id_is_blocked_before_legacy_handler(self):
        headers = self.login("manager")
        self.assertEqual(self.client.post("/manage_order", json={"order_id": 2}, headers=headers).status_code, 404)
        for identity in (True, -1, "1.0", "1 OR 1=1", None, 10 ** 100):
            response = self.client.post("/manage_order", json={"order_id": identity}, headers=headers)
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.writes, [])

    def test_delegated_cashier_cannot_touch_foreign_or_owner_accounts(self):
        headers = self.login("delegated")
        response = self.client.get(f"/get_user_permissions/{self.ids['foreign']}")
        self.assertEqual(response.status_code, 404)
        response = self.client.delete(f"/deleteuser/{self.ids['owner']}", headers=headers)
        self.assertEqual(response.status_code, 403)
        response = self.client.get(f"/get_user_permissions/{self.ids['manager']}")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.writes, [])

    def test_employee_creation_never_creates_global_owner_or_privilege_grants(self):
        headers = self.login("delegated")
        base = {"name": "employee_test", "password": "Safe-employee-password-2026"}
        for extra in ({"role": "admin"}, {"role": "owner"}, {"role": "manager"},
                      {"company": "B"}, {"can_manage_debts": True}, {"role": ["cashier"]}):
            response = self.client.post("/add_user_py", json={**base, **extra}, headers=headers)
            self.assertEqual(response.status_code, 400, extra)
        self.assertEqual(self.writes, [])
        response = self.client.post("/add_user_py", json={**base, "role": "cashier"}, headers=headers)
        self.assertEqual(response.status_code, 200)

    def test_only_owner_can_grant_permissions_and_values_are_strict_booleans(self):
        headers = self.login("manager")
        endpoint = f"/update_user_permissions/{self.ids['cashier']}"
        self.assertEqual(self.client.post(endpoint, json={"can_manage_debts": True}, headers=headers).status_code, 403)
        headers = self.login()
        for payload in ({"can_manage_debts": "false"}, {"can_make_sale": 1}, {"role": "admin"}, []):
            self.assertEqual(self.client.post(endpoint, json=payload, headers=headers).status_code, 400)
        self.assertEqual(self.writes, [])
        self.assertEqual(self.client.post(endpoint, json={"can_manage_debts": True}, headers=headers).status_code, 200)

    def test_cross_company_employee_message_is_rejected(self):
        headers = self.login("manager")
        response = self.client.post("/send_employee_message", json={"user_id": self.ids["foreign"]}, headers=headers)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.writes, [])

    def test_retired_excel_and_barcode_rewrite_never_execute(self):
        headers = self.login()
        for path in ("/api/upload_excel_step1", "/api/regenerate_all_barcodes"):
            response = self.client.post(path, json={"filename": "../../protected"}, headers=headers)
            self.assertEqual(response.status_code, 410)
        response = self.client.post("/chat_ai", json={"message": "synthetic"}, headers=headers)
        self.assertEqual(response.status_code, 503)
        self.app.config["BRIVA_ENABLE_LEGACY_AI"] = True
        response = self.client.post("/chat_ai", json={"message": "synthetic"}, headers=headers)
        self.assertEqual(response.status_code, 503)

    def test_decorators_apply_on_new_routes(self):
        headers = self.login("cashier")
        self.assertEqual(self.client.post("/secured-by-decorator", json={}, headers=headers).status_code, 403)
        self.assertEqual(self.client.get("/owner-by-decorator").status_code, 403)
        headers = self.login()
        self.assertEqual(self.client.post("/secured-by-decorator", json={}, headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/owner-by-decorator").status_code, 200)

    def test_login_throttle_ignores_untrusted_forwarded_headers(self):
        token = self.token()
        for index in range(3):
            response = self.client.post("/login", data={
                "csrf_token": token, "username": "missing", "password": "wrong",
            }, headers={"X-Forwarded-For": f"203.0.113.{index}"})
            self.assertEqual(response.status_code, 401)
        response = self.client.post("/login", data={
            "csrf_token": token, "username": "missing", "password": "wrong",
        }, headers={"X-Forwarded-For": "198.51.100.50"})
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response.headers["Retry-After"]), 0)

    def test_client_ip_only_trusts_explicit_single_hop_proxy(self):
        self.login()
        self.assertEqual(self.client.get("/peer", headers={"X-Forwarded-For": "203.0.113.5"}).json["peer"], "127.0.0.1")
        self.app.config["BRIVA_TRUSTED_PROXY_IPS"] = {"127.0.0.1"}
        self.assertEqual(self.client.get("/peer", headers={"X-Forwarded-For": "203.0.113.5"}).json["peer"], "203.0.113.5")
        self.assertEqual(self.client.get("/peer", headers={"X-Forwarded-For": "203.0.113.5, 198.51.100.1"}).json["peer"], "127.0.0.1")

    def test_headers_private_cache_and_cookie_attributes(self):
        response = self.client.get("/api/security/csrf")
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("object-src 'none'", response.headers["Content-Security-Policy"])
        self.assertIn("camera=(self)", response.headers["Permissions-Policy"])
        cookie = response.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertNotIn("Strict-Transport-Security", response.headers)
        https_response = self.client.get("/api/security/csrf", base_url="https://localhost")
        self.assertIn("max-age=", https_response.headers["Strict-Transport-Security"])

    def test_stricter_svg_policy_is_preserved_and_missing_scope_fails_closed(self):
        headers = self.login("manager")
        response = self.client.get("/strict-svg-policy")
        self.assertEqual(response.headers["Content-Security-Policy"], "default-src 'none'; sandbox")
        self.app.extensions["briva_security"]["resource_models"].pop("Customer")
        response = self.client.delete("/delete_customer/1", headers=headers)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["code"], "resource_scope_not_configured")
        self.assertEqual(self.writes, [])

    def test_audit_never_records_login_credentials_or_usernames(self):
        with self.assertLogs("briva.security", level="INFO") as captured:
            self.login()
        output = " ".join(captured.output)
        self.assertNotIn("Synthetic-password-2026", output)
        self.assertNotIn("'username'", output)
        self.assertNotIn("password_hash", output)
        self.assertIn("login_succeeded", output)

    def test_permission_updates_preserve_omitted_values(self):
        with self.app.app_context():
            owner = self.db.session.get(self.User, self.ids["owner"])
            cashier = self.db.session.get(self.User, self.ids["cashier"])
            result = validate_permissions_payload({"can_manage_debts": True}, actor=owner, target=cashier)
            self.assertTrue(result["can_make_sale"])
            self.assertTrue(result["can_manage_debts"])
            self.assertFalse(result["can_manage_employees"])

    def test_invalid_passwords_reject_without_complexity_theatre(self):
        for value in (None, "short", " " * 15, "a" * 257):
            with self.assertRaises(SecurityValidationError):
                validate_password(value)
        self.assertEqual(validate_password("A usable long passphrase"), "A usable long passphrase")

    def test_report_lists_default_auth_and_explicit_policies(self):
        report = {item["endpoint"]: item for item in endpoint_security_report(self.app)}
        self.assertFalse(report["login"]["authenticated"])
        self.assertTrue(report["identity"]["authenticated"])
        self.assertEqual(report["create_sale"]["permissions_all"], ["can_make_sale"])
        self.assertTrue(report["upload_excel_step1"]["retired"])


class LoginThrottleTests(unittest.TestCase):
    def test_expiry_and_capacity_do_not_evict_an_active_budget(self):
        now = [100.0]
        limiter = LoginThrottle(attempts=2, ip_attempts=5, window_seconds=10,
                                max_keys=2, clock=lambda: now[0])
        self.assertEqual(limiter.consume("127.0.0.1", "user"), (True, 0))
        self.assertEqual(limiter.consume("127.0.0.1", "user"), (True, 0))
        self.assertEqual(limiter.consume("127.0.0.1", "user"), (False, 10))
        self.assertEqual(limiter.consume("127.0.0.2", "user"), (False, 10))
        now[0] = 110.0
        self.assertEqual(limiter.consume("127.0.0.2", "user"), (True, 0))

    def test_success_resets_pair_but_preserves_ip_budget(self):
        limiter = LoginThrottle(attempts=1, ip_attempts=2)
        self.assertTrue(limiter.consume("peer", "User")[0])
        self.assertFalse(limiter.consume("peer", " user ")[0])
        limiter.successful_login("peer", "USER")
        self.assertTrue(limiter.consume("peer", "user")[0])
        limiter.successful_login("peer", "user")
        self.assertFalse(limiter.consume("peer", "another")[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
