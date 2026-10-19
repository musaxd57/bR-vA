"""Revocable browser sessions and explicit, owner-enabled TOTP enrollment.

Cryptographic primitives come from the installed ``cryptography`` package.
Session/factor service functions never commit the request ORM transaction.
Authentication HTTP commands own that boundary; the password throttle owns a
separate short connection transaction. Identity checks remain read-only.
There is no schema mutation, network call, QR service or enrollment on import.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from functools import wraps
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from typing import Any
from urllib.parse import unquote

from flask import Blueprint, current_app, g, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import case, func, inspect, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.pool import SingletonThreadPool, StaticPool
from werkzeug.exceptions import RequestEntityTooLarge

from extensions import db
from briva_auth_models import AuthAccess, AuthChallenge, AuthFactor, AuthLoginBucket, AuthRecoveryCode, AuthSession, AUTH_TABLE_NAMES


blueprint = Blueprint("briva_auth", __name__)
OTP_STEP = 30
OTP_DIGITS = 6
CHALLENGE_SECONDS = 300
SETUP_SECONDS = 600
FAILURE_WINDOW = 300
FAILURE_LIMIT = 5
RECOVERY_COUNT = 8


class AuthError(ValueError):
    def __init__(self, message: str, code: str = "authentication_failed", status: int = 400,
                 *, persist_attempt: bool = False, retry_after: int | None = None):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status
        self.persist_attempt, self.retry_after = persist_attempt, retry_after


def _now() -> int:
    return int(time.time())


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _fingerprint(user: Any) -> str:
    from briva_security import _credential_fingerprint
    return _credential_fingerprint(user)


def _audit(event: str, user_id: int | None = None) -> None:
    from briva_security import audit_event
    audit_event(event, actor_id=user_id)


def require_auth_schema() -> None:
    extension = current_app.extensions["briva_auth"]
    if not extension.get("schema_ready"):
        existing = set(inspect(db.engine).get_table_names())
        if not AUTH_TABLE_NAMES.issubset(existing):
            raise AuthError("Hesap güvenliği için yerel şema yükseltmesi gerekiyor.", "auth_schema_required", 503)
        extension["schema_ready"] = True


def create_auth_schema() -> list[str]:
    """Explicit additive upgrade, restricted to these authentication tables."""
    tables = [table for table in db.metadata.sorted_tables if table.name in AUTH_TABLE_NAMES]
    if {table.name for table in tables} != AUTH_TABLE_NAMES:
        raise RuntimeError("Authentication table metadata is incomplete")
    db.metadata.create_all(bind=db.engine, tables=tables, checkfirst=True)
    extension = current_app.extensions.get("briva_auth")
    if extension is not None:
        extension["schema_ready"] = False
    return [table.name for table in tables]


class SharedLoginThrottle:
    """DB-wide fixed-window aggregates with bounded, reusable nonhistorical slots.

    Each password attempt owns a short, separate connection transaction. It
    never commits/rolls back the request's ORM session. Successful-login reset
    is deferred by the security hook until the login handler has committed.
    """
    defer_success = True

    def __init__(self, *, attempts=8, ip_attempts=40, window_seconds=300,
                 max_keys=10000, lock_wait_ms=350, clock=None):
        if min(attempts, ip_attempts, window_seconds, max_keys, lock_wait_ms) < 1:
            raise ValueError("Throttle values must be positive")
        self.attempts, self.ip_attempts = attempts, ip_attempts
        self.window, self.max_keys, self.lock_wait_ms = window_seconds, max_keys, lock_wait_ms
        self.clock = clock or _now

    @staticmethod
    def _keys(peer, username):
        key = str(current_app.secret_key).encode("utf-8")
        normalized = username.strip().casefold()[:200]
        def digest(kind, value):
            return hmac.new(key, b"briva-login-v1\x00" + kind.encode("ascii") + b"\x00" + value.encode("utf-8"), hashlib.sha256).hexdigest()
        return ((digest("ip", peer), "ip"), (digest("pair", peer + "\x00" + normalized), "pair"))

    @contextmanager
    def _transaction(self):
        # In-memory SQLite fixtures may pool one physical connection for every
        # SQLAlchemy Connection. Refuse before checkout if the request already
        # owns a real write transaction; even closing another checkout could
        # otherwise roll that unrelated request back through pool reset.
        if db.engine.dialect.name == "sqlite" and isinstance(db.engine.pool, (StaticPool, SingletonThreadPool)):
            orm = db.session()
            if orm.in_transaction() and orm.connection().connection.dbapi_connection.in_transaction:
                raise AuthError("Giriş denetimi için bağımsız bağlantı şu anda meşgul.",
                                "login_store_unavailable", 503, retry_after=1)
        with db.engine.connect() as connection:
            previous_wait = None
            try:
                dialect = connection.dialect.name
                if dialect == "sqlite":
                    previous_wait = int(connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one())
                    connection.exec_driver_sql(f"PRAGMA busy_timeout={int(self.lock_wait_ms)}")
                    connection.exec_driver_sql("BEGIN IMMEDIATE")
                elif dialect == "postgresql":
                    connection.begin()
                    connection.exec_driver_sql(f"SET LOCAL lock_timeout = '{int(self.lock_wait_ms)}ms'")
                    connection.exec_driver_sql("SET LOCAL statement_timeout = '1500ms'")
                else:
                    raise AuthError("Bu veritabanı için ortak giriş sınırı yapılandırılmalı.", "login_store_unavailable", 503, retry_after=1)
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                if previous_wait is not None:
                    try:
                        connection.exec_driver_sql(f"PRAGMA busy_timeout={previous_wait}")
                        connection.rollback()
                    except Exception:
                        connection.invalidate()

    def _guard(self, connection):
        extension = current_app.extensions["briva_auth"]
        if not extension.get("schema_ready"):
            if not AUTH_TABLE_NAMES.issubset(set(inspect(connection).get_table_names())):
                raise AuthError("Hesap güvenliği için yerel şema yükseltmesi gerekiyor.", "auth_schema_required", 503)
            extension["schema_ready"] = True
        if connection.dialect.name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        else:
            from sqlalchemy.dialects.postgresql import insert
        table = AuthLoginBucket.__table__
        # Slot zero is an allocation mutex, never a user's record. A constant
        # domain-separated identifier also avoids storing raw IP/usernames.
        statement = insert(table).values(id=0, key_hash=_hash("briva-login-allocation-guard-v1"),
            kind="guard", attempt_count=0, window_started=0, expires_at=0)
        connection.execute(statement.on_conflict_do_nothing(index_elements=["id"]))
        return connection.execute(select(table).where(table.c.id == 0).with_for_update()).mappings().one()

    def _store_error(self):
        _audit("login_throttle_store_unavailable")
        return AuthError("Giriş denetimi şu anda meşgul. Biraz sonra tekrar deneyin.",
                         "login_store_unavailable", 503, retry_after=1)

    def consume(self, peer, username):
        keys, now, table = self._keys(peer, username), int(self.clock()), AuthLoginBucket.__table__
        try:
            with self._transaction() as connection:
                guard = self._guard(connection)
                found = {row["key_hash"]: row for row in connection.execute(select(table).where(
                    table.c.id > 0, table.c.key_hash.in_([key for key, _kind in keys]))).mappings()}
                waits = []
                for (key, _kind), limit in zip(keys, (self.ip_attempts, self.attempts)):
                    row = found.get(key)
                    if row is not None and row["expires_at"] > now and row["attempt_count"] >= limit:
                        waits.append(row["expires_at"] - now)
                if waits:
                    return False, max(1, min(self.window, max(waits)))
                missing = [pair for pair in keys if pair[0] not in found]
                reusable = list(connection.execute(select(table).where(table.c.id > 0,
                    table.c.expires_at <= now,
                    table.c.key_hash.not_in([key for key, _kind in keys]))
                    .order_by(table.c.expires_at, table.c.id).limit(len(missing))).mappings()) if missing else []
                free_capacity = max(0, self.max_keys - guard["attempt_count"])
                if len(reusable) + free_capacity < len(missing):
                    earliest = connection.execute(select(func.min(table.c.expires_at)).where(
                        table.c.id > 0, table.c.expires_at > now)).scalar_one()
                    return False, max(1, min(self.window, (earliest or now + self.window) - now))
                allocated = guard["attempt_count"]
                for key, kind in keys:
                    row = found.get(key)
                    values = {"key_hash": key, "kind": kind, "attempt_count": 1,
                              "window_started": now, "expires_at": now + self.window}
                    if row is not None:
                        if row["expires_at"] > now:
                            values.update(attempt_count=row["attempt_count"] + 1,
                                          window_started=row["window_started"], expires_at=row["expires_at"])
                        connection.execute(update(table).where(table.c.id == row["id"]).values(**values))
                    elif reusable:
                        slot = reusable.pop(0)
                        connection.execute(update(table).where(table.c.id == slot["id"]).values(**values))
                    else:
                        allocated += 1
                        connection.execute(table.insert().values(id=allocated, **values))
                if allocated != guard["attempt_count"]:
                    connection.execute(update(table).where(table.c.id == 0).values(attempt_count=allocated))
            return True, 0
        except (OperationalError, IntegrityError):
            raise self._store_error() from None

    def successful_login(self, peer, username):
        """Reset only pair budget after password+optional factor commit."""
        key = self._keys(peer, username)[1][0]
        table, now = AuthLoginBucket.__table__, int(self.clock())
        try:
            with self._transaction() as connection:
                self._guard(connection)
                connection.execute(update(table).where(table.c.id > 0, table.c.key_hash == key)
                    .values(attempt_count=0, window_started=now, expires_at=now))
        except (OperationalError, IntegrityError):
            raise self._store_error() from None


def _factor(user: Any, *, create: bool = False) -> AuthFactor | None:
    require_auth_schema()
    row = db.session.get(AuthFactor, user.id)
    if row is not None and row.company_id != user.company:
        raise AuthError("Hesap güvenliği kaydı bu işletmeyle eşleşmiyor.", "auth_company_changed", 403)
    if row is None and create:
        row = AuthFactor(user_id=user.id, company_id=user.company)
        db.session.add(row)
        db.session.flush()
    return row


def mfa_required(user: Any) -> bool:
    factor = _factor(user)
    return factor is not None and factor.enabled


def is_user_enabled(user: Any) -> bool:
    require_auth_schema()
    access = db.session.get(AuthAccess, user.id)
    return access is None or (access.company_id == user.company and access.disabled_at is None)


def set_user_access(actor: Any, target: Any, enabled: bool, reason: str) -> dict[str, Any]:
    """Preserve the user/history; revoke access without deleting or changing role."""
    from briva_security import is_owner
    require_auth_schema()
    if not is_owner(actor) or actor.company != target.company:
        raise AuthError("Bu çalışanın erişimini değiştirme yetkiniz yok.", "permission_denied", 403)
    if is_owner(target) or actor.id == target.id:
        raise AuthError("Sahip hesabı çalışan ekranından kapatılamaz.", "owner_protected", 403)
    if type(enabled) is not bool or not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 240:
        raise AuthError("İşlem için 3–240 karakterlik bir neden yazın.", "invalid_access_payload")
    if any(ord(char) < 32 for char in reason):
        raise AuthError("İşlem nedeni kontrol karakteri içeremez.", "invalid_access_payload")
    row = db.session.get(AuthAccess, target.id)
    if row is not None and row.company_id != actor.company:
        raise AuthError("Çalışanın güvenlik kaydı işletmeyle eşleşmiyor.", "permission_denied", 403)
    if row is None:
        row = AuthAccess(user_id=target.id, company_id=actor.company,
                         changed_by=actor.id, changed_at=_now())
        db.session.add(row)
    row.disabled_at = None if enabled else _now()
    row.disabled_reason = None if enabled else reason.strip()
    row.changed_by, row.changed_at = actor.id, _now()
    if not enabled:
        revoke_user_sessions(target)
        db.session.execute(update(AuthChallenge).where(AuthChallenge.user_id == target.id,
            AuthChallenge.used_at.is_(None)).values(used_at=_now()))
    db.session.flush()
    _audit("employee_access_enabled" if enabled else "employee_access_disabled", actor.id)
    return {"id": target.id, "active": enabled, "disabled_at": row.disabled_at}


def deactivate_user(actor: Any, target: Any, reason: str = "Çalışan yönetiminden erişim kapatıldı") -> dict[str, Any]:
    return set_user_access(actor, target, False, reason)


def _session_token() -> str | None:
    raw = session.get("_session_token")
    return raw if isinstance(raw, str) and re.fullmatch(r"[A-Za-z0-9_-]{43}", raw) else None


def _device_label() -> str:
    agent = request.headers.get("User-Agent", "")[:500]
    browser = "Edge" if "Edg/" in agent else "Chrome" if "Chrome/" in agent else "Firefox" if "Firefox/" in agent else "Safari" if "Safari/" in agent else "Tarayıcı"
    platform = "Android" if "Android" in agent else "iPhone/iPad" if "iPhone" in agent or "iPad" in agent else "Windows" if "Windows" in agent else "macOS" if "Macintosh" in agent else "Linux" if "Linux" in agent else "Yerel cihaz"
    return browser + " · " + platform


def issue_session_grant(user: Any, *, mfa_verified: bool = False) -> str:
    """Called by establish_session; caller owns the final database commit."""
    require_auth_schema()
    if not is_user_enabled(user):
        raise AuthError("Bu hesabın erişimi kapalı. İşletme sahibiyle görüşün.", "account_disabled", 403)
    if mfa_required(user) and not mfa_verified:
        raise AuthError("İki aşamalı doğrulamayı tamamlayın.", "two_factor_required", 401)
    raw = secrets.token_urlsafe(32)
    now = _now()
    grant = AuthSession(user_id=user.id, company_id=user.company, token_hash=_hash(raw),
                        credential_fingerprint=_fingerprint(user), device_label=_device_label(),
                        created_at=now, expires_at=now + current_app.config["BRIVA_SESSION_MAX_SECONDS"],
                        mfa_verified_at=now if mfa_verified else None)
    db.session.add(grant)
    db.session.flush()
    return raw


def validate_session_grant(user: Any) -> bool:
    """Read-only validation, including cross-worker server-side revocation."""
    require_auth_schema()
    if not is_user_enabled(user):
        return False
    raw = _session_token()
    if raw is None:
        return False
    grant = db.session.execute(select(AuthSession).where(
        AuthSession.token_hash == _hash(raw), AuthSession.user_id == user.id,
        AuthSession.company_id == user.company, AuthSession.revoked_at.is_(None),
        AuthSession.expires_at > _now(),
    )).scalar_one_or_none()
    if grant is None or not hmac.compare_digest(grant.credential_fingerprint, _fingerprint(user)):
        return False
    if mfa_required(user) and grant.mfa_verified_at is None:
        return False
    g.briva_authenticated_grant = grant
    return True


def has_authenticated_factor(user: Any) -> bool:
    """Proof already checked for this request, usable when rotating a password."""
    grant = getattr(g, "briva_authenticated_grant", None)
    return bool(grant is not None and grant.user_id == user.id and grant.mfa_verified_at is not None)


def revoke_current_session() -> int:
    """No commit; works during logout and during complete session rotation."""
    require_auth_schema()
    raw = _session_token()
    user_id = session.get("user")
    if raw is None or type(user_id) is not int:
        return 0
    return db.session.execute(update(AuthSession).where(
        AuthSession.token_hash == _hash(raw), AuthSession.user_id == user_id,
        AuthSession.revoked_at.is_(None),
    ).values(revoked_at=_now())).rowcount


def revoke_user_sessions(user: Any, *, keep_token: str | None = None) -> int:
    require_auth_schema()
    statement = update(AuthSession).where(AuthSession.user_id == user.id,
        AuthSession.company_id == user.company, AuthSession.revoked_at.is_(None))
    if keep_token is not None:
        statement = statement.where(AuthSession.token_hash != _hash(keep_token))
    return db.session.execute(statement.values(revoked_at=_now())).rowcount


def _fernet():
    key = current_app.config.get("BRIVA_AUTH_ENCRYPTION_KEY") or os.environ.get("BRIVA_AUTH_ENCRYPTION_KEY", "")
    try:
        from cryptography.fernet import Fernet
        if not key:
            raise ValueError("missing encryption key")
        return Fernet(key.encode("ascii") if isinstance(key, str) else key)
    except (ImportError, ValueError, TypeError, UnicodeEncodeError):
        raise AuthError("İki aşamalı doğrulama için yerel güvenlik anahtarı hazırlanmalı.", "mfa_not_configured", 503)


def _encrypt_secret(user: Any, secret: bytes) -> str:
    value = json.dumps({"user_id": user.id, "company": user.company,
                        "secret": base64.b64encode(secret).decode("ascii")}, separators=(",", ":")).encode("utf-8")
    return _fernet().encrypt(value).decode("ascii")


def _decrypt_secret(user: Any, ciphertext: str) -> bytes:
    try:
        value = json.loads(_fernet().decrypt(ciphertext.encode("ascii")))
        if value["user_id"] != user.id or value["company"] != user.company:
            raise ValueError("secret identity mismatch")
        secret = base64.b64decode(value["secret"], validate=True)
        if len(secret) < 20:
            raise ValueError("secret length")
        return secret
    except AuthError:
        raise
    except Exception:
        raise AuthError("Doğrulama anahtarı açılamadı. Kurtarma kodunu kullanabilir veya yerel anahtarı kontrol edebilirsiniz.", "mfa_key_unavailable", 503)


def _totp(secret: bytes):
    from cryptography.hazmat.primitives.hashes import SHA1
    from cryptography.hazmat.primitives.twofactor.totp import TOTP
    return TOTP(secret, OTP_DIGITS, SHA1(), OTP_STEP)


def _matching_counter(secret: bytes, code: str, *, last_counter: int = -1) -> int | None:
    if not re.fullmatch(r"[0-9]{6}", code):
        return None
    from cryptography.hazmat.primitives.twofactor import InvalidToken
    counter = _now() // OTP_STEP
    verifier = _totp(secret)
    for candidate in (counter, counter - 1, counter + 1):
        if candidate < 0 or candidate <= last_counter:
            continue
        try:
            verifier.verify(code.encode("ascii"), candidate * OTP_STEP)
            return candidate
        except InvalidToken:
            pass
    return None


def _check_block(factor: AuthFactor) -> None:
    if factor.blocked_until > _now():
        raise AuthError("Çok fazla doğrulama denemesi. Bir süre sonra tekrar deneyin.", "auth_throttled", 429,
                        retry_after=max(1, factor.blocked_until - _now()))


def _record_failure(user: Any, factor: AuthFactor) -> None:
    now = _now()
    window_expired = AuthFactor.failure_window_started <= now - FAILURE_WINDOW
    db.session.execute(update(AuthFactor).where(AuthFactor.user_id == user.id).values(
        failed_attempts=case((window_expired, 1), else_=AuthFactor.failed_attempts + 1),
        failure_window_started=case((window_expired, now), else_=AuthFactor.failure_window_started),
    ))
    db.session.execute(update(AuthFactor).where(AuthFactor.user_id == user.id,
        AuthFactor.failed_attempts >= FAILURE_LIMIT).values(blocked_until=now + FAILURE_WINDOW))
    _audit("factor_verification_denied", user.id)


def _verify_password(user: Any, factor: AuthFactor, password: Any) -> None:
    _check_block(factor)
    if not isinstance(password, str) or not 1 <= len(password) <= 256 or not user.check_password(password):
        _record_failure(user, factor)
        raise AuthError("Mevcut şifrenizi doğrulayın.", "reauthentication_failed", 401, persist_attempt=True)


def _recovery_hash(user_id: int, code: str) -> str:
    return _hash(f"briva-recovery-v1:{user_id}:{code}")


def _verify_factor(user: Any, factor: AuthFactor, value: Any) -> None:
    _check_block(factor)
    code = value.strip() if isinstance(value, str) and len(value) <= 80 else ""
    compact = code.replace("-", "").replace(" ", "").upper()
    now = _now()
    changed = 0
    if re.fullmatch(r"[A-F0-9]{32}", compact):
        # The conditional update is the single-use boundary, including when
        # several workers or two concurrent browser requests present a code.
        changed = db.session.execute(update(AuthRecoveryCode).where(
            AuthRecoveryCode.user_id == user.id, AuthRecoveryCode.company_id == user.company,
            AuthRecoveryCode.code_hash == _recovery_hash(user.id, compact),
            AuthRecoveryCode.used_at.is_(None), AuthRecoveryCode.retired_at.is_(None),
        ).values(used_at=now)).rowcount
    elif re.fullmatch(r"[0-9]{6}", code) and factor.secret_ciphertext:
        counter = _matching_counter(_decrypt_secret(user, factor.secret_ciphertext), code,
                                    last_counter=factor.last_counter)
        if counter is not None:
            changed = db.session.execute(update(AuthFactor).where(
                AuthFactor.user_id == user.id, AuthFactor.enabled.is_(True),
                AuthFactor.secret_ciphertext == factor.secret_ciphertext,
                AuthFactor.last_counter < counter,
            ).values(last_counter=counter)).rowcount
    if changed != 1:
        _record_failure(user, factor)
        raise AuthError("Kod geçersiz, süresi geçmiş veya kullanılmış.", "factor_invalid", 401, persist_attempt=True)
    db.session.execute(update(AuthFactor).where(AuthFactor.user_id == user.id).values(
        failed_attempts=0, blocked_until=0, failure_window_started=0))


def _retire_recovery_codes(user: Any) -> None:
    db.session.execute(update(AuthRecoveryCode).where(AuthRecoveryCode.user_id == user.id,
        AuthRecoveryCode.company_id == user.company, AuthRecoveryCode.retired_at.is_(None)).values(retired_at=_now()))


def _generate_recovery_codes(user: Any) -> list[str]:
    _retire_recovery_codes(user)
    codes = []
    for _ in range(RECOVERY_COUNT):
        raw = secrets.token_hex(16).upper()
        codes.append("-".join(raw[index:index + 8] for index in range(0, 32, 8)))
        db.session.add(AuthRecoveryCode(user_id=user.id, company_id=user.company,
            code_hash=_recovery_hash(user.id, raw), created_at=_now()))
    db.session.flush()
    return codes


def begin_login_challenge(user: Any) -> bool:
    """After password verification: clear full auth and issue a short challenge.

    Returns false when MFA is disabled. Caller commits the challenge before
    redirecting to briva_auth.login_challenge. No full user identity is placed
    in the cookie until the second factor has been verified.
    """
    if not is_user_enabled(user):
        raise AuthError("Bu hesabın erişimi kapalı. İşletme sahibiyle görüşün.", "account_disabled", 403)
    if not mfa_required(user):
        return False
    factor = _factor(user)
    _check_block(factor)
    raw = secrets.token_urlsafe(32)
    now = _now()
    revoke_current_session()
    session.clear()
    g.briva_current_user = None
    g.briva_user_loaded = True
    session["_pending_auth"] = raw
    from briva_security import csrf_token
    csrf_token()
    db.session.add(AuthChallenge(token_hash=_hash(raw), user_id=user.id,
        company_id=user.company, credential_fingerprint=_fingerprint(user),
        created_at=now, expires_at=now + CHALLENGE_SECONDS))
    db.session.flush()
    _audit("factor_challenge_created", user.id)
    return True


def _pending_challenge() -> tuple[AuthChallenge, Any]:
    require_auth_schema()
    raw = session.get("_pending_auth")
    if not isinstance(raw, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", raw):
        raise AuthError("Giriş doğrulamasını yeniden başlatın.", "challenge_expired", 401)
    challenge = db.session.get(AuthChallenge, _hash(raw))
    if challenge is None or challenge.used_at is not None or challenge.expires_at <= _now():
        raise AuthError("Doğrulamanın süresi doldu. Yeniden giriş yapın.", "challenge_expired", 401)
    user = db.session.get(current_app.extensions["briva_auth"]["user_model"], challenge.user_id)
    if (user is None or user.company != challenge.company_id or not is_user_enabled(user) or not mfa_required(user)
            or not hmac.compare_digest(challenge.credential_fingerprint, _fingerprint(user))):
        raise AuthError("Giriş bilgileri değişti. Yeniden giriş yapın.", "challenge_expired", 401)
    return challenge, user


def _error_response(error: AuthError):
    response = jsonify(status="error", error={"code": error.code, "message": error.message}, message=error.message)
    response.status_code = error.status
    if error.retry_after is not None:
        response.headers["Retry-After"] = str(error.retry_after)
    return response


def _json(fields: set[str]) -> dict[str, Any]:
    try:
        value = request.get_json(silent=True)
    except (RecursionError, ValueError):
        raise AuthError("JSON iç içe veri sınırını aşıyor veya geçersiz.", "invalid_auth_payload") from None
    if not isinstance(value, dict) or set(value) - fields:
        raise AuthError("Geçerli alanlardan oluşan bir JSON nesnesi gönderin.", "invalid_auth_payload")
    return value


def _check_actor_context(user: Any) -> None:
    """Bind a rendered account screen to its original live user/company."""
    actor = request.headers.get("X-Briva-Actor")
    company = request.headers.get("X-Briva-Company")
    if actor is None and company is None:
        return
    if (not isinstance(actor, str) or not re.fullmatch(r"[1-9][0-9]{0,18}", actor)
            or not isinstance(company, str) or not 1 <= len(company) <= 1200):
        raise AuthError("Hesap bağlamı geçersiz. Sayfayı yenileyin.", "invalid_auth_context")
    try:
        company = unquote(company, errors="strict")
    except UnicodeDecodeError:
        raise AuthError("Hesap bağlamı geçersiz. Sayfayı yenileyin.", "invalid_auth_context")
    if int(actor) != user.id or company != user.company:
        raise AuthError("Bu sekme başka bir hesaba ait. Sayfayı yenileyin.", "auth_context_changed", 409)


def _command(*, owner: bool = False, public: bool = False):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            from briva_security import current_user, is_owner, client_address
            try:
                require_auth_schema()
                user = current_user()
                if not public and user is None:
                    raise AuthError("Oturum açmanız gerekiyor.", "authentication_required", 401)
                if not public:
                    _check_actor_context(user)
                if owner and not is_owner(user):
                    raise AuthError("Bu ayarı yalnız işletme sahibi değiştirebilir.", "owner_required", 403)
                if request.content_length is not None and request.content_length > 8_192:
                    raise AuthError("Güvenlik isteği çok büyük.", "auth_payload_too_large", 413)
                request.max_content_length = 8_192
                identity = str(user.id) if user is not None else str(session.get("_pending_auth", ""))[:80]
                allowed, retry_after = current_app.extensions["briva_auth"]["throttle"].consume(client_address(), identity)
                if not allowed:
                    raise AuthError("Çok fazla güvenlik isteği. Bir süre sonra tekrar deneyin.", "auth_throttled", 429, retry_after=retry_after)
                result = function(user, *args, **kwargs)
                db.session.commit()
                return jsonify(data=result)
            except AuthError as error:
                if error.persist_attempt:
                    try:
                        db.session.commit()
                    except OperationalError:
                        db.session.rollback()
                        return _error_response(AuthError("Güvenlik kaydı şu anda meşgul. Tekrar deneyin.", "auth_database_busy", 503))
                else:
                    db.session.rollback()
                return _error_response(error)
            except RequestEntityTooLarge:
                db.session.rollback()
                return _error_response(AuthError("Güvenlik isteği çok büyük.", "auth_payload_too_large", 413))
            except OperationalError:
                db.session.rollback()
                return _error_response(AuthError("Güvenlik kaydı şu anda meşgul. Tekrar deneyin.", "auth_database_busy", 503))
            except Exception:
                db.session.rollback()
                current_app.logger.error("Authentication command failed", exc_info=False)
                return _error_response(AuthError("Güvenlik işlemi tamamlanamadı. Yeniden deneyin.", "auth_operation_failed", 500))
        return wrapped
    return decorate


@blueprint.get("/account-security")
def account_security():
    return render_template("account_security.html", security_mode="account")


@blueprint.get("/api/account-security")
def status():
    from briva_security import current_user, is_owner
    require_auth_schema()
    user = current_user()
    if user is None:
        raise AuthError("Oturum açmanız gerekiyor.", "authentication_required", 401)
    factor = _factor(user)
    try:
        _fernet()
        configured = True
    except AuthError:
        configured = False
    now, raw = _now(), _session_token()
    rows = db.session.execute(select(AuthSession).where(AuthSession.user_id == user.id,
        AuthSession.company_id == user.company, AuthSession.revoked_at.is_(None),
        AuthSession.expires_at > now, AuthSession.credential_fingerprint == _fingerprint(user)
    ).order_by(AuthSession.created_at.desc(), AuthSession.id.desc()).limit(50)).scalars().all()
    count = db.session.query(AuthRecoveryCode).filter(AuthRecoveryCode.user_id == user.id,
        AuthRecoveryCode.company_id == user.company, AuthRecoveryCode.used_at.is_(None),
        AuthRecoveryCode.retired_at.is_(None)).count()
    return jsonify(data={"actor": {"id": user.id, "company": user.company, "username": user.username},
        "mfa_enabled": bool(factor and factor.enabled), "mfa_configured": configured,
        "can_manage_mfa": is_owner(user), "recovery_codes_remaining": count,
        "sessions": [{"id": row.id, "device": row.device_label, "created_at": row.created_at,
            "expires_at": row.expires_at, "is_current": bool(raw and hmac.compare_digest(row.token_hash, _hash(raw))),
            "mfa_verified": row.mfa_verified_at is not None} for row in rows]})


@blueprint.post("/api/account-security/sessions/revoke")
@_command()
def revoke_sessions(user):
    payload = _json({"current_password", "session_id", "all_others"})
    factor = _factor(user, create=True)
    _verify_password(user, factor, payload.get("current_password"))
    if payload.get("all_others") is True and "session_id" not in payload:
        count = revoke_user_sessions(user, keep_token=_session_token())
    elif (type(payload.get("session_id")) is int
          and 0 < payload["session_id"] <= 9223372036854775807 and "all_others" not in payload):
        row = db.session.execute(select(AuthSession).where(AuthSession.id == payload["session_id"],
            AuthSession.user_id == user.id, AuthSession.company_id == user.company)).scalar_one_or_none()
        if row is None:
            raise AuthError("Oturum bulunamadı.", "not_found", 404)
        count = db.session.execute(update(AuthSession).where(AuthSession.id == row.id,
            AuthSession.revoked_at.is_(None)).values(revoked_at=_now())).rowcount
        if _session_token() and hmac.compare_digest(row.token_hash, _hash(_session_token())):
            session.clear()
    else:
        raise AuthError("Kapatılacak oturumu seçin.", "invalid_auth_payload")
    _audit("sessions_revoked", user.id)
    return {"revoked": count}


@blueprint.post("/api/account-security/mfa/setup")
@_command(owner=True)
def setup_mfa(user):
    payload = _json({"current_password"})
    factor = _factor(user, create=True)
    _verify_password(user, factor, payload.get("current_password"))
    if factor.enabled:
        raise AuthError("İki aşamalı doğrulama zaten açık.", "mfa_already_enabled", 409)
    raw = _session_token()
    if raw is None:
        raise AuthError("Oturumu yenileyin.", "authentication_required", 401)
    secret = secrets.token_bytes(20)
    factor.pending_ciphertext = _encrypt_secret(user, secret)
    factor.pending_session_hash = _hash(raw)
    factor.pending_expires_at = _now() + SETUP_SECONDS
    db.session.flush()
    _audit("factor_setup_started", user.id)
    return {"secret": base64.b32encode(secret).decode("ascii"),
            "account": user.username, "issuer": "BRIVA", "digits": OTP_DIGITS,
            "period": OTP_STEP, "expires_in": SETUP_SECONDS}


@blueprint.post("/api/account-security/employees/<int:user_id>/access")
@_command(owner=True)
def employee_access(actor, user_id):
    from briva_security import positive_id, SecurityValidationError
    payload = _json({"enabled", "reason"})
    try:
        user_id = positive_id(user_id)
    except SecurityValidationError:
        raise AuthError("Geçerli bir çalışan seçin.", "invalid_access_payload")
    target = db.session.execute(select(current_app.extensions["briva_auth"]["user_model"]).where(
        current_app.extensions["briva_auth"]["user_model"].id == user_id,
        current_app.extensions["briva_auth"]["user_model"].company == actor.company,
    )).scalar_one_or_none()
    if target is None:
        raise AuthError("Çalışan bulunamadı.", "not_found", 404)
    return set_user_access(actor, target, payload.get("enabled"), payload.get("reason"))


@blueprint.post("/api/account-security/mfa/confirm")
@_command(owner=True)
def confirm_mfa(user):
    payload = _json({"current_password", "code"})
    factor = _factor(user, create=True)
    _verify_password(user, factor, payload.get("current_password"))
    raw = _session_token()
    if (factor.enabled or not factor.pending_ciphertext or factor.pending_expires_at is None
            or factor.pending_expires_at <= _now() or raw is None
            or factor.pending_session_hash != _hash(raw)):
        raise AuthError("Kurulumu bu oturumda yeniden başlatın.", "mfa_setup_expired", 409)
    code = payload.get("code")
    counter = _matching_counter(_decrypt_secret(user, factor.pending_ciphertext), code.strip() if isinstance(code, str) else "")
    if counter is None:
        _record_failure(user, factor)
        raise AuthError("Doğrulama kodu geçersiz.", "factor_invalid", 401, persist_attempt=True)
    # Session binding and enabled=false are checked again at the atomic write.
    changed = db.session.execute(update(AuthFactor).where(AuthFactor.user_id == user.id,
        AuthFactor.enabled.is_(False), AuthFactor.pending_session_hash == _hash(raw),
        AuthFactor.pending_ciphertext == factor.pending_ciphertext,
        AuthFactor.pending_expires_at > _now()).values(enabled=True,
            secret_ciphertext=factor.pending_ciphertext, last_counter=counter,
            enabled_at=_now(), disabled_at=None, pending_ciphertext=None,
            pending_session_hash=None, pending_expires_at=None,
            failed_attempts=0, failure_window_started=0, blocked_until=0)).rowcount
    if changed != 1:
        raise AuthError("Kurulum başka bir işlemde değişti. Yeniden kontrol edin.", "mfa_setup_changed", 409)
    codes = _generate_recovery_codes(user)
    revoke_user_sessions(user)
    from briva_security import establish_session
    establish_session(user, mfa_verified=True)
    _audit("factor_enabled", user.id)
    return {"mfa_enabled": True, "recovery_codes": codes}


@blueprint.post("/api/account-security/mfa/disable")
@_command(owner=True)
def disable_mfa(user):
    payload = _json({"current_password", "code"})
    factor = _factor(user, create=True)
    _verify_password(user, factor, payload.get("current_password"))
    if not factor.enabled:
        raise AuthError("İki aşamalı doğrulama zaten kapalı.", "mfa_not_enabled", 409)
    _verify_factor(user, factor, payload.get("code"))
    db.session.execute(update(AuthFactor).where(AuthFactor.user_id == user.id).values(
        enabled=False, secret_ciphertext=None, pending_ciphertext=None,
        pending_session_hash=None, pending_expires_at=None, disabled_at=_now()))
    _retire_recovery_codes(user)
    db.session.execute(update(AuthChallenge).where(AuthChallenge.user_id == user.id,
        AuthChallenge.used_at.is_(None)).values(used_at=_now()))
    revoke_user_sessions(user)
    from briva_security import establish_session
    establish_session(user)
    _audit("factor_disabled", user.id)
    return {"mfa_enabled": False}


@blueprint.post("/api/account-security/mfa/recovery-codes")
@_command(owner=True)
def regenerate_recovery(user):
    payload = _json({"current_password", "code"})
    factor = _factor(user, create=True)
    _verify_password(user, factor, payload.get("current_password"))
    if not factor.enabled:
        raise AuthError("Önce iki aşamalı doğrulamayı açın.", "mfa_not_enabled", 409)
    _verify_factor(user, factor, payload.get("code"))
    codes = _generate_recovery_codes(user)
    _audit("recovery_codes_rotated", user.id)
    return {"recovery_codes": codes}


@blueprint.route("/login/verify", methods=["GET", "POST"])
def login_challenge():
    if request.method == "GET":
        try:
            _pending_challenge()
        except AuthError:
            return redirect(url_for("login"))
        return render_template("account_security.html", security_mode="challenge")
    return _complete_login()


@_command(public=True)
def _complete_login(_user):
    payload = _json({"code"})
    challenge, user = _pending_challenge()
    factor = _factor(user)
    _verify_factor(user, factor, payload.get("code"))
    changed = db.session.execute(update(AuthChallenge).where(
        AuthChallenge.token_hash == challenge.token_hash, AuthChallenge.used_at.is_(None),
        AuthChallenge.expires_at > _now()).values(used_at=_now())).rowcount
    if changed != 1:
        raise AuthError("Giriş doğrulaması kullanılmış. Yeniden giriş yapın.", "challenge_used", 409)
    from briva_security import establish_session
    establish_session(user, mfa_verified=True)
    _audit("factor_login_succeeded", user.id)
    return {"redirect": url_for("workbench")}


@blueprint.post("/login/verify/cancel")
@_command(public=True)
def cancel_challenge(_user):
    raw = session.get("_pending_auth")
    if isinstance(raw, str):
        db.session.execute(update(AuthChallenge).where(AuthChallenge.token_hash == _hash(raw),
            AuthChallenge.used_at.is_(None)).values(used_at=_now()))
    session.clear()
    return {"redirect": url_for("login")}


def init_auth(app: Any, database: Any, user_model: Any) -> None:
    """Register metadata/routes only. Explicit upgrade creates all six tables."""
    if database is not db:
        raise RuntimeError("Authentication must share the application's database")
    if "briva_auth" in app.extensions:
        raise RuntimeError("Authentication is already installed")
    from briva_security import LoginThrottle
    app.extensions["briva_auth"] = {"db": db, "user_model": user_model,
        "schema_ready": False, "throttle": LoginThrottle(attempts=20, ip_attempts=80, window_seconds=300)}
    app.register_blueprint(blueprint)

    @app.errorhandler(AuthError)
    def handle_auth_error(error):
        db.session.rollback()
        return _error_response(error)
