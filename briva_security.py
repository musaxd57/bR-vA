"""Server-side security shared by the legacy Flask views and the new core.

Install once with ``init_security(app, db, User, resource_models={...})``.
After a successful password check call ``establish_session(user)``.  Templates
receive ``csrf_token()``; browser mutations send the token as a form field or
``X-CSRF-Token``.  No test-only bypass is required or provided for CSRF.

This module never commits the request ORM session, deletes files, calls an AI
provider, or writes request bodies to logs. With init_auth installed, password
attempts use the shared database throttle; isolated hook fixtures use memory.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import timedelta
from functools import wraps
import hashlib
import hmac
import ipaddress
import logging
import math
import posixpath
import re
import secrets
import threading
import time
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from flask import (
    abort, current_app, g, has_request_context, jsonify, redirect, request,
    session, url_for,
)


PERMISSIONS = (
    "can_make_sale", "can_view_sales_history", "can_view_statistics",
    "can_generate_reports", "can_add_product", "can_edit_product",
    "can_delete_product", "can_manage_employees", "can_manage_attributes",
    "can_manage_customers", "can_manage_debts", "can_orders",
)
EMPLOYEE_ROLES = frozenset({"employee", "cashier", "warehouse"})
OWNER_ROLES = frozenset({"admin", "owner"})
KNOWN_ROLES = EMPLOYEE_ROLES | OWNER_ROLES | {"manager"}
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
PUBLIC_ENDPOINTS = frozenset({"main", "login", "security_csrf",
                              "briva_auth.login_challenge", "briva_auth.cancel_challenge"})
PAGE_ENDPOINTS = frozenset({
    "storage", "profile", "customer_debts", "home", "admin", "manager",
    "addcolor_route", "addshelf_route", "addcategory_route", "definitions",
    "add_customer_route", "feedback", "notifications", "product_detail",
    "show_logs",
    "workbench", "logout", "product_photos",
    "briva_auth.account_security",
})
EXCEL_IMPORT_ENDPOINTS = frozenset({
    "upload_excel_step1", "upload_excel_step2", "upload_excel_step3",
})
# No feature flag reopens these unsafe imports. A future import service must
# validate private file IDs and use the shared stock service before new routes.
RETIRED_MUTATION_ENDPOINTS = EXCEL_IMPORT_ENDPOINTS | {
    "api_regenerate_all_barcodes",
}


@dataclass(frozen=True)
class EndpointPolicy:
    permissions: tuple[str, ...] = ()
    any_permissions: tuple[str, ...] = ()
    owner: bool = False


ENDPOINT_POLICIES: dict[str, EndpointPolicy] = {}


def _policies(endpoints: str, **policy: Any) -> None:
    for endpoint in endpoints.split():
        ENDPOINT_POLICIES[endpoint] = EndpointPolicy(**policy)


_policies(
    "show_logs logs_json admin update_user_route add_manager add_company "
    "delete_company get_all_companys get_all_users send_bulk_notification_route",
    owner=True,
)
_policies("manager get_employees add_user_py get_user_permissions "
          "update_user_permissions delete_user_route send_employee_message",
          permissions=("can_manage_employees",))
_policies("update_user_permissions", owner=True)
_policies("briva_auth.setup_mfa briva_auth.confirm_mfa briva_auth.disable_mfa "
          "briva_auth.regenerate_recovery briva_auth.employee_access", owner=True)
_policies("add_expenditure get_expenditures collect_payment "
          "get_customers_with_debt get_customer_debt_history customer_debts",
          permissions=("can_manage_debts",))
_policies("create_sale", permissions=("can_make_sale",))
_policies("create_order_route", permissions=("can_make_sale", "can_orders"))
_policies("get_orders_route manage_order_route get_orders_unified_route "
          "get_order_history_route revert_order_route get_picking_list_route",
          permissions=("can_orders",))
_policies("get_all_sales_paginated", permissions=("can_view_sales_history",))
_policies("get_sales_statistics get_daily_sales_trend_route "
          "get_product_sales_trend_route", permissions=("can_view_statistics",))
_policies("generate_transaction_pdf generate_lod_pdf generate_order_pdf "
          "export_products download_sample_excel",
          permissions=("can_generate_reports",))
_policies("add_product_route", permissions=("can_add_product",))
_policies("add_stock_route add_stock_to_shelf_route update_product_route",
          permissions=("can_edit_product",))
_policies("delete_product_route", permissions=("can_delete_product",))
_policies("add_customer delete_customer add_customer_route",
          permissions=("can_manage_customers",))
_policies("get_all_customers", any_permissions=(
    "can_manage_customers", "can_view_sales_history", "can_orders",
))
_policies("add_shelf add_color add_category delete_color delete_category "
          "delete_shelf add_attribute_def_route update_attribute_def_route "
          "delete_attribute_def_route manage_barcode_rules definitions "
          "addcolor_route addshelf_route addcategory_route",
          permissions=("can_manage_attributes",))


class SecurityValidationError(ValueError):
    """An explicitly invalid identity, role, or permissions request."""


class LoginThrottle:
    """Bounded, thread-safe sliding-window throttle; keys never store usernames.

    Both the peer address and peer+username are limited. Unknown forwarded
    headers cannot change the peer key. The bounded capacity fails closed for
    new keys rather than evicting active entries and resetting their budgets.
    """

    def __init__(self, *, attempts: int = 8, window_seconds: int = 300,
                 ip_attempts: int = 40, max_keys: int = 10000,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if min(attempts, window_seconds, ip_attempts, max_keys) < 1:
            raise ValueError("Throttle values must be positive")
        self.attempts = attempts
        self.window = window_seconds
        self.ip_attempts = ip_attempts
        self.max_keys = max_keys
        self.clock = clock
        self._buckets: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _keys(peer: str, username: str) -> tuple[str, str]:
        normalized = username.strip().casefold()[:200]
        return (
            "ip:" + hashlib.sha256(peer.encode("utf-8")).hexdigest(),
            "pair:" + hashlib.sha256(
                (peer + "\x00" + normalized).encode("utf-8")
            ).hexdigest(),
        )

    def consume(self, peer: str, username: str) -> tuple[bool, int]:
        now = self.clock()
        cutoff = now - self.window
        keys = self._keys(peer, username)
        with self._lock:
            for key in list(self._buckets):
                values = self._buckets[key]
                while values and values[0] <= cutoff:
                    values.popleft()
                if not values:
                    del self._buckets[key]
            waits = []
            for key, limit in zip(keys, (self.ip_attempts, self.attempts)):
                values = self._buckets.get(key)
                if values and len(values) >= limit:
                    waits.append(math.ceil(values[0] + self.window - now))
            if waits:
                return False, max(1, max(waits))
            missing = sum(key not in self._buckets for key in keys)
            if len(self._buckets) + missing > self.max_keys:
                return False, self.window
            for key in keys:
                self._buckets.setdefault(key, deque()).append(now)
            return True, 0

    def successful_login(self, peer: str, username: str) -> None:
        # Preserve the IP budget so rotating valid credentials cannot bypass it.
        with self._lock:
            self._buckets.pop(self._keys(peer, username)[1], None)


def _extension() -> dict[str, Any]:
    return current_app.extensions["briva_security"]


def _credential_fingerprint(user: Any) -> str:
    key = str(current_app.secret_key).encode("utf-8")
    value = f"{user.id}:{user.password_hash}".encode("utf-8")
    return hmac.new(key, value, hashlib.sha256).hexdigest()


def csrf_token() -> str:
    token = session.get("_csrf_token")
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def establish_session(user: Any, *, mfa_verified: bool = False) -> None:
    """Rotate the complete cookie session after a verified login/password change."""
    if not isinstance(getattr(user, "id", None), int) or not user.company:
        raise SecurityValidationError("Kullanıcı veya şirket bilgisi geçersiz.")
    if user.role not in KNOWN_ROLES:
        raise SecurityValidationError("Kullanıcı rolü geçersiz.")
    raw_session = None
    if "briva_auth" in current_app.extensions:
        from briva_totp import issue_session_grant, revoke_current_session
        raw_session = issue_session_grant(user, mfa_verified=mfa_verified)
        revoke_current_session()
    session.clear()
    session.permanent = True
    session.update({
        "user": user.id, "username": user.username, "company": user.company,
        "role": user.role, "telephone": user.phone,
        "_credential_fingerprint": _credential_fingerprint(user),
        "_issued_at": int(time.time()), "_last_seen": int(time.time()),
    })
    if raw_session is not None:
        session["_session_token"] = raw_session
    csrf_token()
    g.briva_current_user = user
    g.briva_user_loaded = True
    if request.endpoint in {"login", "briva_auth.login_challenge"}:
        throttle = _extension()["throttle"]
        if getattr(throttle, "defer_success", False):
            g.briva_login_reset = (client_address(), user.username)
        else:
            throttle.successful_login(client_address(), user.username)
    audit_event("login_succeeded" if request.endpoint == "login" else "session_rotated")


def current_user() -> Any | None:
    """Load DB identity once per request; never trust cookie roles/permissions."""
    if not has_request_context():
        return None
    if getattr(g, "briva_user_loaded", False):
        return getattr(g, "briva_current_user", None)
    g.briva_user_loaded = True
    g.briva_current_user = None
    user_id = session.get("user")
    if type(user_id) is not int or user_id < 1:
        if user_id is not None:
            session.clear()
        return None
    extension = _extension()
    user = extension["db"].session.get(extension["user_model"], user_id)
    now = int(time.time())
    issued = session.get("_issued_at")
    seen = session.get("_last_seen")
    fingerprint = session.get("_credential_fingerprint")
    valid = (
        user is not None
        and getattr(user, "is_active", True) is not False
        and user.company
        and user.role in KNOWN_ROLES
        and session.get("company") == user.company
        and isinstance(fingerprint, str)
        and hmac.compare_digest(fingerprint, _credential_fingerprint(user))
        and type(issued) is int and type(seen) is int
        and 0 <= now - issued <= current_app.config["BRIVA_SESSION_MAX_SECONDS"]
        and 0 <= now - seen <= current_app.config["BRIVA_SESSION_IDLE_SECONDS"]
    )
    if valid and "briva_auth" in current_app.extensions:
        from briva_totp import validate_session_grant
        valid = validate_session_grant(user)
    if not valid:
        session.clear()
        audit_event("session_invalidated", actor_id=user_id)
        return None
    # All legacy views now see the DB identity, including demoted roles.
    session["role"] = user.role
    session["username"] = user.username
    session["telephone"] = user.phone
    if now - seen >= 60:
        session["_last_seen"] = now
    g.briva_current_user = user
    return user


def is_owner(user: Any | None = None) -> bool:
    user = user if user is not None else current_user()
    return user is not None and user.role in OWNER_ROLES


def has_permission(permission: str, user: Any | None = None) -> bool:
    if permission not in PERMISSIONS:
        raise SecurityValidationError("Bilinmeyen izin.")
    user = user if user is not None else current_user()
    return bool(user is not None and (
        is_owner(user) or getattr(user, permission, False) is True
    ))


def require_permission(*permissions: str) -> Callable:
    for permission in permissions:
        if permission not in PERMISSIONS:
            raise SecurityValidationError("Bilinmeyen izin.")

    def decorate(view: Callable) -> Callable:
        @wraps(view)
        def protected(*args: Any, **kwargs: Any) -> Any:
            if current_user() is None:
                return _error("Oturum açmanız gerekiyor.", 401, "authentication_required")
            if not all(has_permission(permission) for permission in permissions):
                return _error("Bu işlem için yetkiniz yok.", 403, "permission_denied")
            return view(*args, **kwargs)
        return protected
    return decorate


def require_owner(view: Callable) -> Callable:
    @wraps(view)
    def protected(*args: Any, **kwargs: Any) -> Any:
        if current_user() is None:
            return _error("Oturum açmanız gerekiyor.", 401, "authentication_required")
        if not is_owner():
            return _error("Bu işlem yalnız işletme sahibine açıktır.", 403, "owner_required")
        return view(*args, **kwargs)
    return protected


def require_company_resource(model: Any, identity: Any, *, field: str = "id",
                             company_field: str = "company_id") -> Any:
    """Scoped query returns the same 404 for missing and foreign objects."""
    user = current_user()
    if user is None:
        abort(401)
    if field == "id":
        identity = positive_id(identity)
    resource = model.query.filter_by(**{
        field: identity, company_field: user.company,
    }).first()
    if resource is None:
        abort(404)
    return resource


def positive_id(value: Any) -> int:
    if type(value) is int and 0 < value <= 9223372036854775807:
        return value
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,17}", value):
        return int(value)
    raise SecurityValidationError("Geçerli bir kayıt numarası gerekli.")


def _static_filename() -> str:
    # Werkzeug normalizes dot segments when resolving a static file. Apply the
    # same normalization before classifying a request as a private upload, so
    # /static/js/../uploads/secret.png cannot bypass the authentication branch.
    raw = str((request.view_args or {}).get("filename", "")).replace("\\", "/")
    return posixpath.normpath(raw).lstrip("/")


def validate_employee_payload(payload: Any, *, actor: Any | None = None,
                              target: Any | None = None,
                              creating: bool = True) -> dict[str, Any]:
    """Normalize employee writes without accepting arbitrary privilege fields.

    Delegated employee managers may create/edit ordinary employees in their own
    company. Only the owner can appoint managers or change permission grants.
    No HTTP employee endpoint may create a second global admin/owner account.
    """
    actor = actor if actor is not None else current_user()
    if actor is None or not has_permission("can_manage_employees", actor):
        raise SecurityValidationError("Çalışan yönetme yetkiniz yok.")
    if not isinstance(payload, dict):
        raise SecurityValidationError("Geçersiz çalışan bilgisi.")
    allowed = {"name", "username", "password", "phone", "company", "role"}
    if set(payload) - allowed:
        raise SecurityValidationError("Desteklenmeyen çalışan alanı.")
    if target is not None:
        if target.company != actor.company:
            raise SecurityValidationError("Başka işletmenin çalışanı değiştirilemez.")
        if is_owner(target):
            raise SecurityValidationError("Sahip hesabı çalışan ekranından değiştirilemez.")
        if not is_owner(actor) and target.role not in EMPLOYEE_ROLES:
            raise SecurityValidationError("Yönetici hesabını yalnız sahip değiştirebilir.")
    role = payload.get("role", getattr(target, "role", "employee"))
    if not isinstance(role, str) or role not in EMPLOYEE_ROLES | {"manager"}:
        raise SecurityValidationError("Çalışan için geçerli bir rol seçin.")
    if role == "manager" and not is_owner(actor):
        raise SecurityValidationError("Yönetici atama yetkiniz yok.")
    company = payload.get("company", actor.company)
    if company != actor.company:
        raise SecurityValidationError("Çalışan başka işletmeye taşınamaz.")
    username = payload.get("username", payload.get("name", getattr(target, "username", "")))
    if (not isinstance(username, str) or not 2 <= len(username.strip()) <= 100
            or any(ord(char) < 32 or ord(char) == 127 for char in username)):
        raise SecurityValidationError("Kullanıcı adı 2–100 karakter olmalı.")
    password = payload.get("password")
    if creating or password not in (None, ""):
        validate_password(password)
    phone = payload.get("phone", getattr(target, "phone", None))
    if phone is not None and (not isinstance(phone, str) or len(phone) > 15):
        raise SecurityValidationError("Telefon bilgisi geçersiz.")
    return {
        "username": username.strip(), "password": password or None,
        "phone": phone, "company": actor.company, "role": role,
    }


def validate_password(password: Any) -> str:
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise SecurityValidationError("Şifre 12–256 karakter olmalı.")
    if password.isspace():
        raise SecurityValidationError("Şifre yalnız boşluklardan oluşamaz.")
    return password


def validate_permissions_payload(payload: Any, *, actor: Any | None = None,
                                 target: Any | None = None) -> dict[str, bool]:
    actor = actor if actor is not None else current_user()
    # Employee management does not imply authority to delegate financial access.
    if not is_owner(actor):
        raise SecurityValidationError("İzinleri yalnız işletme sahibi değiştirebilir.")
    if target is not None and (target.company != actor.company or is_owner(target)):
        raise SecurityValidationError("Bu hesabın izinleri bu ekrandan değiştirilemez.")
    if not isinstance(payload, dict) or set(payload) - set(PERMISSIONS):
        raise SecurityValidationError("Bilinmeyen izin alanı.")
    if any(type(value) is not bool for value in payload.values()):
        raise SecurityValidationError("İzin değerleri true veya false olmalı.")
    # An omitted permission preserves its current value; a partial save must not
    # revoke unrelated grants silently. New users default to false.
    return {
        permission: payload.get(permission, bool(getattr(target, permission, False)))
        for permission in PERMISSIONS
    }


def client_address() -> str:
    """Use the direct peer unless its proxy IP is explicitly allowlisted.

    If a deployment uses ProxyFix, configure it only behind a known proxy;
    this helper cannot recover an address already overwritten by middleware.
    """
    peer = request.remote_addr or "unknown"
    trusted = current_app.config.get("BRIVA_TRUSTED_PROXY_IPS", ())
    if peer not in trusted:
        return peer
    forwarded = request.headers.get("X-Forwarded-For", "").split(",")
    if len(forwarded) != 1:
        return peer
    try:
        return str(ipaddress.ip_address(forwarded[0].strip()))
    except ValueError:
        return peer


def audit_event(event: str, *, actor_id: int | None = None,
                outcome: str = "ok") -> None:
    """Fixed metadata only: no names, phones, credentials, cookies, or bodies."""
    if not re.fullmatch(r"[a-z_]{1,64}", event):
        raise ValueError("Audit event must be a fixed identifier")
    if actor_id is None:
        actor_id = session.get("user") if has_request_context() else None
    metadata = {
        "event": event,
        "actor_id": actor_id if type(actor_id) is int else None,
        "endpoint": request.endpoint if has_request_context() else None,
        "outcome": outcome if outcome in {"ok", "denied", "error"} else "error",
        "request_id": getattr(g, "briva_request_id", None) if has_request_context() else None,
    }
    logging.getLogger("briva.security").info("security_event %s", metadata)


def _error(message: str, status: int, code: str) -> Any:
    response = jsonify(status="error", message=message, code=code)
    response.status_code = status
    return response


def _origin_is_same() -> bool:
    origin = request.headers.get("Origin")
    if not origin:
        # Non-browser scanners/clients may omit Origin. They still need a token.
        return True
    try:
        incoming = urlsplit(origin)
        expected = urlsplit(request.host_url)
        incoming_port = incoming.port or (443 if incoming.scheme == "https" else 80)
        expected_port = expected.port or (443 if expected.scheme == "https" else 80)
        return (
            incoming.scheme in {"http", "https"}
            and incoming.scheme == expected.scheme
            and incoming.hostname == expected.hostname
            and incoming_port == expected_port
            and not incoming.username and not incoming.password
            and incoming.path in {"", "/"} and not incoming.query and not incoming.fragment
        )
    except ValueError:
        return False


def _check_csrf() -> bool:
    expected = session.get("_csrf_token")
    supplied = request.headers.get("X-CSRF-Token")
    if not supplied:
        supplied = request.form.get("csrf_token")
    return bool(
        isinstance(expected, str) and isinstance(supplied, str)
        and re.fullmatch(r"[A-Za-z0-9_-]{32,128}", expected)
        and re.fullmatch(r"[A-Za-z0-9_-]{32,128}", supplied)
        and hmac.compare_digest(expected, supplied)
        and _origin_is_same()
        and request.headers.get("Sec-Fetch-Site") != "cross-site"
    )


def _resource_guards(endpoint: str, models: Mapping[str, Any]) -> Any:
    """Defense for legacy handlers which used an unscoped primary-key query."""
    args = request.view_args or {}
    path_targets = {
        "delete_customer": ("Customer", "customer_id", "id"),
        "get_customer_debt_history": ("Customer", "customer_id", "id"),
        "delete_color": ("Color", "code", "code"),
        "delete_category": ("Category", "id", "id"),
        "delete_shelf": ("Shelf", "id", "id"),
        "delete_product_route": ("Product", "product_id", "id"),
        "product_detail": ("Product", "product_id", "id"),
        "generate_order_pdf": ("Order", "order_id", "id"),
        "get_picking_list_route": ("Order", "order_id", "id"),
    }
    if endpoint in path_targets:
        name, parameter, field = path_targets[endpoint]
        if name not in models:
            return _error("Kaynak yetkilendirmesi yapılandırılmadı.", 503, "resource_scope_not_configured")
        require_company_resource(models[name], args.get(parameter), field=field)
    body_targets = {
        "manage_order_route": ("Order", "order_id"),
        "revert_order_route": ("Order", "order_id"),
        "collect_payment": ("Customer", "customer_id"),
    }
    # Parse only legacy bodies this hook actually owns. New API/auth commands
    # enforce their smaller limits before decoding; eagerly parsing them here
    # would bypass those boundaries and turn deeply nested JSON into a 500.
    body_endpoints = set(body_targets) | {
        "send_employee_message", "send_bulk_notification_route", "add_user_py",
        "add_manager", "update_user_route", "update_user_permissions",
    }
    payload = None
    if endpoint in body_endpoints and request.is_json:
        try:
            payload = request.get_json(silent=True)
        except (RecursionError, ValueError):
            raise SecurityValidationError("JSON iç içe veri sınırını aşıyor veya geçersiz.") from None
    if endpoint in body_targets:
        name, parameter = body_targets[endpoint]
        if name not in models:
            return _error("Kaynak yetkilendirmesi yapılandırılmadı.", 503, "resource_scope_not_configured")
        if not isinstance(payload, dict):
            raise SecurityValidationError("JSON nesnesi gerekli.")
        require_company_resource(models[name], payload.get(parameter))
    user_endpoints = {"get_user_permissions", "update_user_permissions",
                      "delete_user_route", "update_user_route"}
    if endpoint in user_endpoints:
        target = require_company_resource(
            _extension()["user_model"], args.get("user_id"), company_field="company",
        )
        g.briva_target_user = target
        if endpoint != "get_user_permissions" and is_owner(target):
            return _error("Sahip hesabı bu ekrandan değiştirilemez.", 403, "owner_protected")
        if not is_owner() and target.role not in EMPLOYEE_ROLES:
            return _error("Yönetici hesabı için sahip yetkisi gerekli.", 403, "owner_required")
    if endpoint == "send_employee_message":
        if not isinstance(payload, dict):
            raise SecurityValidationError("JSON nesnesi gerekli.")
        require_company_resource(
            _extension()["user_model"], payload.get("user_id"), company_field="company",
        )
    if endpoint == "send_bulk_notification_route":
        values = payload.get("user_ids") if isinstance(payload, dict) else None
        if not isinstance(values, list) or not values or len(values) > 500:
            raise SecurityValidationError("1–500 kullanıcı seçin.")
        for value in values:
            require_company_resource(_extension()["user_model"], value, company_field="company")
    if endpoint in {"add_user_py", "add_manager"}:
        validate_employee_payload(payload)
    if endpoint == "update_user_route":
        validate_employee_payload(payload, target=g.briva_target_user, creating=False)
    if endpoint == "update_user_permissions":
        validate_permissions_payload(payload, target=g.briva_target_user)
    return None


def endpoint_security_report(app: Any) -> list[dict[str, Any]]:
    """Inspection aid; a policy entry is not proof of row-level correctness."""
    report = []
    for rule in sorted(app.url_map.iter_rules(), key=lambda item: item.rule):
        policy = ENDPOINT_POLICIES.get(rule.endpoint, EndpointPolicy())
        report.append({
            "route": rule.rule, "endpoint": rule.endpoint,
            "methods": sorted(rule.methods - {"HEAD", "OPTIONS"}),
            "authenticated": rule.endpoint not in PUBLIC_ENDPOINTS and rule.endpoint != "static",
            "owner_only": policy.owner,
            "permissions_all": list(policy.permissions),
            "permissions_any": list(policy.any_permissions),
            "retired": rule.endpoint in RETIRED_MUTATION_ENDPOINTS,
            "csrf": bool(rule.methods - SAFE_METHODS),
        })
    return report


def init_security(app: Any, db: Any, user_model: Any, *,
                  resource_models: Mapping[str, Any] | None = None) -> None:
    """Install hooks without owning any database transaction or schema change.

    ``resource_models`` should contain Customer, Color, Category, Shelf,
    Product and Order under those exact keys. Pass all of them for legacy
    ownership checks; absence is recorded as an integration limitation.
    """
    if "briva_security" in app.extensions:
        raise RuntimeError("BRIVA security is already installed")
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
    app.config.setdefault("BRIVA_SESSION_IDLE_SECONDS", 8 * 60 * 60)
    app.config.setdefault("BRIVA_SESSION_MAX_SECONDS", 24 * 60 * 60)
    app.config.setdefault("BRIVA_TRUSTED_PROXY_IPS", ())
    # Flask default cookie duration is intentionally replaced with a bounded
    # local work-day session. Server timestamps also enforce idle/absolute age.
    app.permanent_session_lifetime = timedelta(seconds=app.config["BRIVA_SESSION_MAX_SECONDS"])
    if app.config.get("BRIVA_REQUIRE_HTTPS", False):
        app.config["SESSION_COOKIE_SECURE"] = True
    throttle_type = LoginThrottle
    if "briva_auth" in app.extensions:
        from briva_totp import SharedLoginThrottle
        throttle_type = SharedLoginThrottle
    app.extensions["briva_security"] = {
        "db": db, "user_model": user_model,
        "resource_models": dict(resource_models or {}),
        "throttle": throttle_type(
            attempts=app.config.get("BRIVA_LOGIN_ATTEMPTS", 8),
            window_seconds=app.config.get("BRIVA_LOGIN_WINDOW_SECONDS", 300),
            ip_attempts=app.config.get("BRIVA_LOGIN_IP_ATTEMPTS", 40),
            max_keys=app.config.get("BRIVA_LOGIN_MAX_KEYS", 10000),
        ),
    }
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.route("/api/security/csrf", methods=["GET"], endpoint="security_csrf")
    def security_csrf() -> Any:
        return jsonify(csrf_token=csrf_token())

    @app.before_request
    def security_before_request() -> Any:
        g.briva_request_id = secrets.token_hex(12)
        endpoint = request.endpoint
        if endpoint is None:
            return None
        # CSS/JS/fonts remain public. Product images are company information;
        # the default Flask static handler cannot filter an image by company.
        if endpoint == "static":
            filename = _static_filename()
            if not filename.startswith("uploads/"):
                return None
        user = current_user()
        if endpoint not in PUBLIC_ENDPOINTS and user is None:
            if endpoint in PAGE_ENDPOINTS and request.method in {"GET", "HEAD"}:
                return redirect(url_for("login"))
            return _error("Oturum açmanız gerekiyor.", 401, "authentication_required")
        if endpoint == "static":
            filename = _static_filename()
            product_model = _extension()["resource_models"].get("Product")
            if filename.startswith("uploads/"):
                if product_model is None or not hasattr(product_model, "image_path"):
                    return _error("Özel görsel yetkilendirmesi hazır değil.", 503, "resource_guard_unavailable")
                owned_image = product_model.query.filter_by(
                    company_id=user.company, image_path=filename,
                ).first()
                if owned_image is None:
                    abort(404)
        if ((endpoint == "login" or endpoint.startswith("briva_auth."))
                and request.method not in SAFE_METHODS):
            # Bound credentials before form parsing, database lookup or KDF.
            request.max_content_length = 8_192
            if request.content_length is not None and request.content_length > 8_192:
                return _error("Hesap güvenliği isteği çok büyük.", 413,
                              "login_payload_too_large" if endpoint == "login" else "auth_payload_too_large")
        if request.method not in SAFE_METHODS and not _check_csrf():
            audit_event("csrf_denied", outcome="denied")
            return _error("Güvenlik doğrulaması başarısız. Sayfayı yenileyin.", 400, "csrf_failed")
        if endpoint == "login" and request.method == "POST":
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            if (not 1 <= len(username.strip()) <= 100 or not 1 <= len(password) <= 256
                    or any(ord(char) < 32 or ord(char) == 127 for char in username)):
                return _error("Kullanıcı adı veya şifre bilgisi geçersiz.", 400, "invalid_login_payload")
            allowed, retry_after = _extension()["throttle"].consume(client_address(), username)
            if not allowed:
                audit_event("login_throttled", outcome="denied")
                response = _error("Çok fazla giriş denemesi. Bir süre sonra tekrar deneyin.", 429, "login_throttled")
                response.headers["Retry-After"] = str(retry_after)
                return response
        if endpoint in RETIRED_MUTATION_ENDPOINTS:
            return _error("Bu eski işlem kapatıldı. Yeni ürün ve stok akışını kullanın.", 410, "legacy_operation_retired")
        # The archived provider can invoke legacy DB tools directly, bypassing
        # HTTP mutation gates. No flag may reopen that stock/account path.
        if endpoint == "chat_ai":
            return _error("Eski AI işlem yolu kapatıldı. Analiz katmanı hazırlanıyor.", 503, "legacy_ai_disabled")
        policy = ENDPOINT_POLICIES.get(endpoint, EndpointPolicy())
        if policy.owner and not is_owner(user):
            audit_event("owner_permission_denied", outcome="denied")
            return _error("Bu işlem yalnız işletme sahibine açıktır.", 403, "owner_required")
        permitted = all(has_permission(permission, user) for permission in policy.permissions)
        if policy.any_permissions:
            permitted = permitted and any(has_permission(permission, user) for permission in policy.any_permissions)
        if not permitted:
            audit_event("permission_denied", outcome="denied")
            return _error("Bu işlem için yetkiniz yok.", 403, "permission_denied")
        try:
            return _resource_guards(endpoint, _extension()["resource_models"])
        except SecurityValidationError as exc:
            return _error(str(exc), 400, "invalid_security_payload")

    @app.after_request
    def security_after_request(response: Any) -> Any:
        reset = getattr(g, "briva_login_reset", None)
        if reset is not None and response.status_code < 400:
            try:
                _extension()["throttle"].successful_login(*reset)
            except Exception:
                # The authenticated handler already committed. A busy reset
                # must not undo login or expose diagnostics; attempt limits
                # remain stricter until their short window expires.
                audit_event("login_throttle_reset_deferred", outcome="error")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["X-Request-ID"] = getattr(g, "briva_request_id", "")
        # Inline scripts/handlers exist throughout legacy templates. This policy
        # restricts remote origins, framing and object embeds without falsely
        # claiming that existing DOM injection is prevented by a strict nonce.
        response.headers.setdefault("Content-Security-Policy", (
            "default-src 'self'; base-uri 'self'; object-src 'none'; "
            "frame-ancestors 'none'; form-action 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
            "font-src 'self' data: https://fonts.gstatic.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
            "img-src 'self' data: blob:; connect-src 'self'; "
            "media-src 'self' blob: https://actions.google.com; worker-src 'self' blob:"
        ))
        if request.endpoint != "static" or _static_filename().startswith("uploads/"):
            response.headers["Cache-Control"] = "no-store, private"
            response.headers["Pragma"] = "no-cache"
        if request.is_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response
