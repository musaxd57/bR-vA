"""Revocation/MFA integration tests without importing the PDF/legacy app.

All databases are fresh in-memory SQLite; no files, tables or existing records
are deleted. cryptography is the real installed implementation, not a stub.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import threading
import time
import unittest
import uuid
from unittest.mock import patch
from urllib.parse import quote

from cryptography.fernet import Fernet
from flask import Flask, jsonify, redirect, request, session, url_for
from sqlalchemy import inspect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from extensions import db
from models import User
from briva_auth_models import AuthAccess, AuthChallenge, AuthFactor, AuthLoginBucket, AuthRecoveryCode, AuthSession
import briva_totp as auth
from briva_security import current_user, establish_session, init_security


PASSWORD = "Synthetic-owner-password-for-auth-2026"


class AuthSessionsAndTotpTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / "templates"))
        self.app.config.update(TESTING=True, SECRET_KEY="synthetic-auth-session-secret-at-least-32-characters",
            SQLALCHEMY_DATABASE_URI=getattr(self, "database_uri", "sqlite:///:memory:"), SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SQLALCHEMY_ENGINE_OPTIONS={"connect_args": {"timeout": 15}},
            BRIVA_AUTH_ENCRYPTION_KEY=Fernet.generate_key().decode("ascii"))
        db.init_app(self.app)
        self.now = int(time.time())
        self.clock = patch("briva_totp._now", side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)

        @self.app.route("/login", methods=["GET", "POST"])
        def login():
            if request.method == "GET":
                return "Login"
            user = User.query.filter_by(username=request.form.get("username")).first()
            if user is None or not user.check_password(request.form.get("password", "")):
                return jsonify(message="Invalid"), 401
            if auth.begin_login_challenge(user):
                db.session.commit()
                return redirect(url_for("briva_auth.login_challenge"))
            establish_session(user)
            db.session.commit()
            return redirect(url_for("workbench"))

        @self.app.get("/")
        def main():
            return "Main"

        @self.app.get("/workbench")
        def workbench():
            return jsonify(user=current_user().username)

        @self.app.get("/profile")
        def profile():
            return "Profile"

        @self.app.get("/api/auth-probe")
        def auth_probe():
            return jsonify(user=current_user().username)

        @self.app.post("/logout")
        def logout():
            auth.revoke_current_session()
            db.session.commit()
            session.clear()
            return redirect(url_for("login"))

        @self.app.post("/synthetic-password-change")
        def synthetic_password_change():
            user = current_user()
            proof = auth.has_authenticated_factor(user)
            user.set_password("Synthetic-updated-password-for-auth-2026")
            db.session.commit()
            establish_session(user, mfa_verified=proof)
            db.session.commit()
            return jsonify(ok=True)

        auth.init_auth(self.app, db, User)
        init_security(self.app, db, User)
        with self.app.app_context():
            db.create_all()
            for username, role, company in (("owner", "admin", "A"), ("employee", "cashier", "A"), ("foreign", "admin", "B")):
                user = User(username=username, role=role, company=company)
                user.set_password(PASSWORD)
                db.session.add(user)
            db.session.commit()
            self.ids = {u.username: u.id for u in User.query.all()}
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()

    def token(self, client=None):
        response = (client or self.client).get("/api/security/csrf")
        self.assertEqual(response.status_code, 200)
        return response.json["csrf_token"]

    def headers(self, client=None):
        return {"X-CSRF-Token": self.token(client)}

    def login(self, username="owner", client=None, *, challenge=False, password=PASSWORD):
        client = client or self.client
        response = client.post("/login", data={"username": username, "password": password, "csrf_token": self.token(client)})
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        self.assertEqual(response.headers["Location"], "/login/verify" if challenge else "/workbench")
        return self.headers(client)

    def post(self, path, payload, client=None):
        client = client or self.client
        return client.post(path, json=payload, headers=self.headers(client))

    def begin_setup(self, client=None):
        response = self.post("/api/account-security/mfa/setup", {"current_password": PASSWORD}, client)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.json["data"]["secret"]

    def code(self, secret, when=None):
        return auth._totp(base64.b32decode(secret)).generate(self.now if when is None else when).decode("ascii")

    def enable(self):
        self.login()
        secret = self.begin_setup()
        response = self.post("/api/account-security/mfa/confirm", {"current_password": PASSWORD, "code": self.code(secret)})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return secret, response.json["data"]["recovery_codes"]

    def test_logout_revokes_stolen_cookie_replay_on_the_server(self):
        self.login()
        stolen_cookie = self.client.get_cookie("session").value
        self.assertEqual(self.client.post("/logout", headers=self.headers()).status_code, 302)
        replay = self.app.test_client()
        replay.set_cookie("session", stolen_cookie)
        self.assertEqual(replay.get("/api/auth-probe").status_code, 401)
        with self.app.app_context():
            self.assertIsNotNone(AuthSession.query.one().revoked_at)

    def test_raw_session_token_is_not_stored_and_old_rotation_is_revoked(self):
        self.login()
        with self.client.session_transaction() as cookie:
            raw = cookie["_session_token"]
        with self.app.app_context():
            grant = AuthSession.query.one()
            self.assertNotEqual(grant.token_hash, raw)
            self.assertEqual(grant.token_hash, auth._hash(raw))
        self.login()
        with self.app.app_context():
            self.assertEqual(AuthSession.query.filter(AuthSession.revoked_at.is_(None)).count(), 1)
            self.assertEqual(AuthSession.query.count(), 2)

    def test_user_can_revoke_other_own_sessions_without_touching_current_or_foreign(self):
        self.login()
        other = self.app.test_client()
        self.login(client=other)
        foreign = self.app.test_client()
        self.login("foreign", foreign)
        response = self.post("/api/account-security/sessions/revoke", {"current_password": PASSWORD, "all_others": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["data"]["revoked"], 1)
        self.assertEqual(self.client.get("/workbench").status_code, 200)
        self.assertEqual(other.get("/api/auth-probe").status_code, 401)
        self.assertEqual(foreign.get("/workbench").status_code, 200)

    def test_foreign_session_ids_and_payload_fields_are_rejected(self):
        self.login()
        foreign = self.app.test_client()
        self.login("foreign", foreign)
        with self.app.app_context():
            foreign_id = AuthSession.query.filter_by(user_id=self.ids["foreign"]).one().id
        response = self.post("/api/account-security/sessions/revoke", {"current_password": PASSWORD, "session_id": foreign_id})
        self.assertEqual(response.status_code, 404)
        for extra in ({"user_id": self.ids["foreign"]}, {"company": "B"}, {"role": "admin"}):
            response = self.post("/api/account-security/sessions/revoke", {"current_password": PASSWORD, "all_others": True, **extra})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(foreign.get("/workbench").status_code, 200)

    def test_server_expiry_is_enforced_independently_of_the_browser_cookie(self):
        self.login()
        self.now += self.app.config["BRIVA_SESSION_MAX_SECONDS"] + 1
        self.assertEqual(self.client.get("/api/auth-probe").status_code, 401)

    def test_missing_encryption_key_does_not_enable_mfa_or_break_normal_login(self):
        self.app.config["BRIVA_AUTH_ENCRYPTION_KEY"] = ""
        with patch.dict("os.environ", {"BRIVA_AUTH_ENCRYPTION_KEY": ""}):
            self.login()
            status = self.client.get("/api/account-security").json["data"]
            self.assertFalse(status["mfa_enabled"])
            self.assertFalse(status["mfa_configured"])
            response = self.post("/api/account-security/mfa/setup", {"current_password": PASSWORD})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(self.client.get("/workbench").status_code, 200)

    def test_employee_cannot_enroll_or_disable_owner_factor(self):
        self.login("employee")
        for operation in ("setup", "confirm", "disable", "recovery-codes"):
            response = self.post("/api/account-security/mfa/" + operation, {"current_password": PASSWORD})
            self.assertEqual(response.status_code, 403)
        with self.app.app_context():
            self.assertEqual(AuthFactor.query.count(), 0)

    def test_setup_is_pending_encrypted_and_bound_to_the_initiating_session(self):
        self.login()
        second = self.app.test_client()
        self.login(client=second)
        secret = self.begin_setup()
        with self.app.app_context():
            factor = db.session.get(AuthFactor, self.ids["owner"])
            self.assertFalse(factor.enabled)
            self.assertNotIn(secret, factor.pending_ciphertext)
            self.assertIsNone(factor.secret_ciphertext)
        response = self.post("/api/account-security/mfa/confirm", {"current_password": PASSWORD, "code": self.code(secret)}, second)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(second.get("/workbench").status_code, 200)
        response = self.post("/api/account-security/mfa/confirm", {"current_password": PASSWORD, "code": self.code(secret)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json["data"]["recovery_codes"]), auth.RECOVERY_COUNT)
        self.assertEqual(second.get("/api/auth-probe").status_code, 401)

    def test_setup_expiry_does_not_enable_factor(self):
        self.login()
        secret = self.begin_setup()
        self.now += auth.SETUP_SECONDS + 1
        response = self.post("/api/account-security/mfa/confirm", {"current_password": PASSWORD, "code": self.code(secret)})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.get("/api/account-security").json["data"]["mfa_enabled"], False)

    def test_enabled_mfa_requires_a_second_step_and_does_not_leave_full_auth_in_pending_cookie(self):
        secret, _ = self.enable()
        other = self.app.test_client()
        self.login(client=other, challenge=True)
        with other.session_transaction() as cookie:
            self.assertNotIn("user", cookie)
            self.assertNotIn("_session_token", cookie)
            self.assertIn("_pending_auth", cookie)
        self.assertEqual(other.get("/api/auth-probe").status_code, 401)
        self.now += OTP_SECONDS
        response = self.post("/login/verify", {"code": self.code(secret)}, other)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(other.get("/workbench").status_code, 200)
        with other.session_transaction() as cookie:
            self.assertNotIn("_pending_auth", cookie)
            self.assertIn("user", cookie)

    def test_totp_replay_is_rejected_even_with_a_new_password_challenge(self):
        secret, _ = self.enable()
        other = self.app.test_client()
        self.login(client=other, challenge=True)
        # Enrollment consumed this step's code; it cannot authenticate again.
        response = self.post("/login/verify", {"code": self.code(secret)}, other)
        self.assertEqual(response.status_code, 401)
        self.now += OTP_SECONDS
        accepted_code = self.code(secret)
        self.assertEqual(self.post("/login/verify", {"code": accepted_code}, other).status_code, 200)
        third = self.app.test_client()
        self.login(client=third, challenge=True)
        self.assertEqual(self.post("/login/verify", {"code": accepted_code}, third).status_code, 401)

    def test_used_challenge_cookie_is_not_a_reusable_login_capability(self):
        secret, _ = self.enable()
        other = self.app.test_client()
        self.login(client=other, challenge=True)
        pending_cookie = other.get_cookie("session").value
        self.now += OTP_SECONDS
        self.assertEqual(self.post("/login/verify", {"code": self.code(secret)}, other).status_code, 200)
        replay = self.app.test_client()
        replay.set_cookie("session", pending_cookie)
        self.now += OTP_SECONDS
        response = self.post("/login/verify", {"code": self.code(secret)}, replay)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json["error"]["code"], "challenge_expired")

    def test_recovery_codes_are_hashed_single_use_and_not_returned_by_status(self):
        _secret, codes = self.enable()
        with self.app.app_context():
            hashes = [row.code_hash for row in AuthRecoveryCode.query.all()]
            for code in codes:
                self.assertNotIn(code.replace("-", ""), hashes)
        serialized = self.client.get("/api/account-security").get_data(as_text=True)
        for code in codes:
            self.assertNotIn(code, serialized)
        other = self.app.test_client()
        self.login(client=other, challenge=True)
        self.assertEqual(self.post("/login/verify", {"code": codes[0]}, other).status_code, 200)
        third = self.app.test_client()
        self.login(client=third, challenge=True)
        self.assertEqual(self.post("/login/verify", {"code": codes[0]}, third).status_code, 401)
        with self.app.app_context():
            self.assertEqual(AuthRecoveryCode.query.filter(AuthRecoveryCode.used_at.isnot(None)).count(), 1)

    def test_recovery_can_disable_mfa_if_the_separate_encryption_key_is_unavailable(self):
        _secret, codes = self.enable()
        self.app.config["BRIVA_AUTH_ENCRYPTION_KEY"] = Fernet.generate_key().decode("ascii")
        other = self.app.test_client()
        self.login(client=other, challenge=True)
        self.assertEqual(self.post("/login/verify", {"code": codes[0]}, other).status_code, 200)
        response = self.post("/api/account-security/mfa/disable", {"current_password": PASSWORD, "code": codes[1]}, other)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json["data"]["mfa_enabled"])
        self.assertEqual(other.get("/workbench").status_code, 200)
        self.assertEqual(self.client.get("/api/auth-probe").status_code, 401)

    def test_recovery_rotation_retires_old_codes_without_deleting_history(self):
        _secret, old_codes = self.enable()
        response = self.post("/api/account-security/mfa/recovery-codes", {"current_password": PASSWORD, "code": old_codes[0]})
        self.assertEqual(response.status_code, 200)
        fresh = response.json["data"]["recovery_codes"]
        self.assertNotEqual(old_codes, fresh)
        with self.app.app_context():
            self.assertEqual(AuthRecoveryCode.query.count(), auth.RECOVERY_COUNT * 2)
            self.assertEqual(AuthRecoveryCode.query.filter(AuthRecoveryCode.retired_at.isnot(None)).count(), auth.RECOVERY_COUNT)
        other = self.app.test_client()
        self.login(client=other, challenge=True)
        self.assertEqual(self.post("/login/verify", {"code": old_codes[1]}, other).status_code, 401)
        self.assertEqual(self.post("/login/verify", {"code": fresh[0]}, other).status_code, 200)

    def test_password_change_retains_current_mfa_proof_and_invalidates_other_sessions(self):
        secret, _codes = self.enable()
        other = self.app.test_client()
        self.login(client=other, challenge=True)
        self.now += OTP_SECONDS
        self.assertEqual(self.post("/login/verify", {"code": self.code(secret)}, other).status_code, 200)
        response = self.client.post("/synthetic-password-change", headers=self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/workbench").status_code, 200)
        self.assertEqual(other.get("/api/auth-probe").status_code, 401)

    def test_persistent_account_throttle_blocks_repeated_reauthentication_failures(self):
        self.login()
        for index in range(auth.FAILURE_LIMIT):
            response = self.post("/api/account-security/mfa/setup", {"current_password": "incorrect-password"})
            self.assertEqual(response.status_code, 401, index)
        response = self.post("/api/account-security/mfa/setup", {"current_password": PASSWORD})
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response.headers["Retry-After"]), 0)
        self.now += auth.FAILURE_WINDOW + 1
        self.assertEqual(self.post("/api/account-security/mfa/setup", {"current_password": PASSWORD}).status_code, 200)

    def test_pending_challenge_expiry_and_csrf_are_enforced(self):
        secret, _ = self.enable()
        other = self.app.test_client()
        self.login(client=other, challenge=True)
        response = other.post("/login/verify", json={"code": self.code(secret)})
        self.assertEqual(response.status_code, 400)
        self.now += auth.CHALLENGE_SECONDS + 1
        response = self.post("/login/verify", {"code": self.code(secret)}, other)
        self.assertEqual(response.status_code, 401)

    def test_auth_status_never_exposes_ciphertexts_tokens_or_fingerprints(self):
        self.enable()
        response = self.client.get("/api/account-security")
        body = response.get_data(as_text=True)
        for forbidden in ("secret_ciphertext", "pending_ciphertext", "token_hash", "credential_fingerprint", "_session_token"):
            self.assertNotIn(forbidden, body)
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_owner_can_disable_and_reactivate_employee_without_deleting_identity(self):
        self.login()
        employee = self.app.test_client()
        self.login("employee", employee)
        stolen = employee.get_cookie("session").value
        with self.app.app_context():
            before = db.session.get(User, self.ids["employee"])
            original = (before.role, before.password_hash, before.company)
        path = f"/api/account-security/employees/{self.ids['employee']}/access"
        response = self.post(path, {"enabled": False, "reason": "Görev değişikliği"})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertFalse(response.json["data"]["active"])
        self.assertEqual(employee.get("/api/auth-probe").status_code, 401)
        attempted = employee.post("/login", data={"username": "employee", "password": PASSWORD,
                                                  "csrf_token": self.token(employee)})
        self.assertEqual(attempted.status_code, 403)
        self.assertEqual(attempted.json["error"]["code"], "account_disabled")
        response = self.post(path, {"enabled": True, "reason": "Göreve geri döndü"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["data"]["active"])
        replay = self.app.test_client()
        replay.set_cookie("session", stolen)
        self.assertEqual(replay.get("/api/auth-probe").status_code, 401)
        self.login("employee", employee)
        with self.app.app_context():
            retained = db.session.get(User, self.ids["employee"])
            self.assertEqual((retained.role, retained.password_hash, retained.company), original)
            self.assertIsNone(db.session.get(AuthAccess, retained.id).disabled_at)
            self.assertEqual(User.query.count(), 3)

    def test_employee_access_is_owner_only_company_scoped_and_owner_protected(self):
        self.login()
        for target in ("owner", "foreign"):
            response = self.post(f"/api/account-security/employees/{self.ids[target]}/access",
                                 {"enabled": False, "reason": "Sentetik test"})
            self.assertEqual(response.status_code, 403 if target == "owner" else 404)
        employee = self.app.test_client()
        self.login("employee", employee)
        response = self.post(f"/api/account-security/employees/{self.ids['employee']}/access",
                             {"enabled": False, "reason": "Sentetik test"}, employee)
        self.assertEqual(response.status_code, 403)
        with self.app.app_context():
            self.assertEqual(AuthAccess.query.count(), 0)

    def test_employee_access_rejects_truthy_strings_control_characters_and_tenant_injection(self):
        self.login()
        path = f"/api/account-security/employees/{self.ids['employee']}/access"
        for body in ({"enabled": "false", "reason": "Açıklama"},
                     {"enabled": 0, "reason": "Açıklama"},
                     {"enabled": False, "reason": "ab"},
                     {"enabled": False, "reason": "x" * 241},
                     {"enabled": False, "reason": "Test\nlog"},
                     {"enabled": False, "reason": "Açıklama", "company": "B"}):
            with self.subTest(body=body):
                self.assertEqual(self.post(path, body).status_code, 400)
        with self.app.app_context():
            self.assertEqual(AuthAccess.query.count(), 0)

    def test_disabled_user_pending_mfa_challenge_is_cancelled_on_deactivation(self):
        # A former owner can have an enrolled factor and later become an employee.
        secret, _ = self.enable()
        pending = self.app.test_client()
        self.login(client=pending, challenge=True)
        with self.app.app_context():
            owner = db.session.get(User, self.ids["owner"])
            owner.role = "cashier"
            new_owner = User(username="replacement-owner", role="admin", company="A")
            new_owner.set_password(PASSWORD)
            db.session.add(new_owner)
            db.session.commit()
            replacement_id = new_owner.id
        replacement = self.app.test_client()
        self.login("replacement-owner", replacement)
        response = self.post(f"/api/account-security/employees/{self.ids['owner']}/access",
                             {"enabled": False, "reason": "Yetki devri tamamlandı"}, replacement)
        self.assertEqual(response.status_code, 200)
        self.now += OTP_SECONDS
        attempted = self.post("/login/verify", {"code": self.code(secret)}, pending)
        self.assertEqual(attempted.status_code, 401)
        with self.app.app_context():
            self.assertEqual(AuthChallenge.query.filter(AuthChallenge.used_at.is_(None)).count(), 0)
            self.assertEqual(db.session.get(AuthAccess, self.ids["owner"]).changed_by, replacement_id)

    def test_account_and_challenge_pages_render_with_csrf_without_revealing_secrets(self):
        self.login()
        page = self.client.get("/account-security")
        self.assertEqual(page.status_code, 200)
        self.assertIn('name="csrf-token"', page.get_data(as_text=True))
        secret = self.begin_setup()
        self.assertNotIn(secret, self.client.get("/account-security").get_data(as_text=True))
        response = self.post("/api/account-security/mfa/confirm", {
            "current_password": PASSWORD, "code": self.code(secret)})
        self.assertEqual(response.status_code, 200)
        other = self.app.test_client()
        self.login(client=other, challenge=True)
        challenge = other.get("/login/verify")
        self.assertEqual(challenge.status_code, 200)
        self.assertIn('data-security-mode="challenge"', challenge.get_data(as_text=True))
        self.assertNotIn(secret, challenge.get_data(as_text=True))

    def test_oversized_session_id_returns_validation_error_without_database_overflow(self):
        self.login()
        response = self.post("/api/account-security/sessions/revoke", {
            "current_password": PASSWORD, "session_id": 10 ** 100})
        self.assertEqual(response.status_code, 400)

    def test_account_command_context_rejects_stale_actor_and_incomplete_or_corrupt_pairs(self):
        self.login()
        path = "/api/account-security/mfa/setup"
        base = self.headers()
        cases = [({"X-Briva-Actor": str(self.ids["foreign"]), "X-Briva-Company": "B"}, 409),
                 ({"X-Briva-Actor": str(self.ids["owner"])}, 400),
                 ({"X-Briva-Company": "A"}, 400),
                 ({"X-Briva-Actor": "²", "X-Briva-Company": "A"}, 400),
                 ({"X-Briva-Actor": str(self.ids["owner"]), "X-Briva-Company": "%FF"}, 400)]
        for extra, status in cases:
            with self.subTest(headers=extra):
                response = self.client.post(path, json={"current_password": PASSWORD}, headers={**base, **extra})
                self.assertEqual(response.status_code, status)
        with self.app.app_context():
            self.assertEqual(AuthFactor.query.count(), 0)

    def test_account_command_context_accepts_encoded_turkish_company_for_original_actor(self):
        company = "BRIVA İstanbul % A"
        with self.app.app_context():
            db.session.get(User, self.ids["owner"]).company = company
            db.session.commit()
        headers = self.login()
        response = self.client.post("/api/account-security/mfa/setup", json={"current_password": PASSWORD},
            headers={**headers, "X-Briva-Actor": str(self.ids["owner"]), "X-Briva-Company": quote(company, safe="")})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.client.get("/api/account-security").json["data"]["actor"]["company"], company)

    def test_auth_payload_limit_and_deep_json_fail_before_creating_factor_or_ciphertext(self):
        self.login()
        path = "/api/account-security/mfa/setup"
        response = self.client.post(path, json={"current_password": "x" * 9000}, headers=self.headers())
        self.assertEqual(response.status_code, 413)
        deep = '{"current_password":' + '[' * 3500 + '0' + ']' * 3500 + '}'
        response = self.client.post(path, data=deep, content_type="application/json", headers=self.headers())
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["error"]["code"], "invalid_auth_payload")
        with self.app.app_context():
            self.assertEqual(AuthFactor.query.count(), 0)

    def test_shared_password_budget_denies_before_the_user_password_checker_runs_again(self):
        throttle = self.app.extensions["briva_security"]["throttle"]
        self.assertIsInstance(throttle, auth.SharedLoginThrottle)
        throttle.attempts = 1
        throttle.ip_attempts = 100
        with patch.object(User, "check_password", return_value=False) as password_checker:
            for expected in (401, 429):
                response = self.client.post("/login", data={"username": "owner", "password": "wrong-password",
                    "csrf_token": self.token()})
                self.assertEqual(response.status_code, expected)
            self.assertEqual(password_checker.call_count, 1)

    def test_mfa_password_step_keeps_pair_budget_until_factor_commit_and_preserves_ip_total(self):
        secret, _codes = self.enable()
        throttle = self.app.extensions["briva_security"]["throttle"]
        pending = self.app.test_client()
        self.login(client=pending, challenge=True)
        with self.app.app_context():
            ip_key, pair_key = [value for value, _kind in throttle._keys("127.0.0.1", "owner")]
            pair = AuthLoginBucket.query.filter_by(key_hash=pair_key).one()
            self.assertEqual(pair.attempt_count, 1)
            self.assertEqual(AuthLoginBucket.query.filter_by(key_hash=ip_key).one().attempt_count, 2)
        self.now += OTP_SECONDS
        response = self.post("/login/verify", {"code": self.code(secret)}, pending)
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertEqual(AuthLoginBucket.query.filter_by(key_hash=pair_key).one().attempt_count, 0)
            self.assertEqual(AuthLoginBucket.query.filter_by(key_hash=ip_key).one().attempt_count, 2)


OTP_SECONDS = auth.OTP_STEP


class TotpPrimitiveTests(unittest.TestCase):
    def test_explicit_auth_upgrade_creates_only_six_auth_tables_and_is_repeatable(self):
        app = Flask("auth-schema-" + uuid.uuid4().hex)
        app.config.update(SQLALCHEMY_DATABASE_URI="sqlite:///:memory:")
        db.init_app(app)
        auth.init_auth(app, db, User)
        with app.app_context():
            self.assertEqual(inspect(db.engine).get_table_names(), [])
            User.__table__.create(bind=db.engine, checkfirst=True)
            auth.create_auth_schema()
            auth.create_auth_schema()
            self.assertEqual(set(inspect(db.engine).get_table_names()), {"user"} | auth.AUTH_TABLE_NAMES)
            self.assertEqual(len(auth.AUTH_TABLE_NAMES), 6)
            db.session.remove()
            db.engine.dispose()

    def test_rfc6238_sha1_vectors_in_six_digit_mode_including_after_2038(self):
        secret = b"12345678901234567890"
        vectors = [(59, "287082"), (1111111109, "081804"), (1111111111, "050471"),
                   (1234567890, "005924"), (2000000000, "279037"), (20000000000, "353130")]
        for moment, expected in vectors:
            with self.subTest(moment=moment), patch("briva_totp._now", return_value=moment):
                self.assertEqual(auth._totp(secret).generate(moment).decode("ascii"), expected)
                self.assertEqual(auth._matching_counter(secret, expected), moment // auth.OTP_STEP)
                self.assertIsNone(auth._matching_counter(secret, expected, last_counter=moment // auth.OTP_STEP))


class AuthConcurrencyTests(unittest.TestCase):
    """Two SQLite connections; evidence files intentionally remain on disk."""

    def setUp(self):
        directory = Path(__file__).resolve().parents[1] / ".verification-data" / ("auth-concurrency-" + uuid.uuid4().hex)
        directory.mkdir(parents=True, exist_ok=False)
        self.fixture = AuthSessionsAndTotpTests("runTest")
        self.fixture.database_uri = "sqlite:///" + (directory / "synthetic.db").as_posix()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)

    def race_code(self, value):
        fixture = self.fixture
        clients = [fixture.app.test_client(), fixture.app.test_client()]
        headers = [fixture.login(client=client, challenge=True) for client in clients]
        barrier = threading.Barrier(2)

        def send(index):
            barrier.wait(timeout=15)
            response = clients[index].post("/login/verify", json={"code": value}, headers=headers[index])
            return response.status_code, response.get_json()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(send, (0, 1)))
        self.assertEqual(sum(status == 200 for status, _ in results), 1, results)
        self.assertTrue(all(status in (200, 401, 409, 503) for status, _ in results), results)
        with fixture.app.app_context():
            # Current enrollment session plus exactly one newly verified login.
            self.assertEqual(AuthSession.query.filter(AuthSession.revoked_at.is_(None)).count(), 2)
        return results

    def test_one_recovery_code_cannot_complete_two_concurrent_logins(self):
        _secret, codes = self.fixture.enable()
        self.race_code(codes[0])
        with self.fixture.app.app_context():
            self.assertEqual(AuthRecoveryCode.query.filter(AuthRecoveryCode.used_at.isnot(None)).count(), 1)

    def test_one_totp_counter_cannot_complete_two_concurrent_logins(self):
        secret, _codes = self.fixture.enable()
        self.fixture.now += OTP_SECONDS
        self.race_code(self.fixture.code(secret))


if __name__ == "__main__":
    unittest.main(verbosity=2)
