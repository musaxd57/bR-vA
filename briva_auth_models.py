"""Additive authentication metadata; importing never creates/changes tables.

Create these briva_* tables through the explicit local schema upgrade. Raw
browser session tokens and recovery codes are never stored in this database.
"""
from extensions import db


class AuthSession(db.Model):
    __tablename__ = "briva_auth_session"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    company_id = db.Column(db.String(100), nullable=False, index=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    credential_fingerprint = db.Column(db.String(64), nullable=False)
    device_label = db.Column(db.String(60), nullable=False)
    created_at = db.Column(db.BigInteger, nullable=False)
    expires_at = db.Column(db.BigInteger, nullable=False)
    revoked_at = db.Column(db.BigInteger, nullable=True)
    mfa_verified_at = db.Column(db.BigInteger, nullable=True)


class AuthFactor(db.Model):
    __tablename__ = "briva_auth_factor"
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), primary_key=True)
    company_id = db.Column(db.String(100), nullable=False, index=True)
    enabled = db.Column(db.Boolean, nullable=False, default=False)
    secret_ciphertext = db.Column(db.Text, nullable=True)
    pending_ciphertext = db.Column(db.Text, nullable=True)
    pending_session_hash = db.Column(db.String(64), nullable=True)
    pending_expires_at = db.Column(db.BigInteger, nullable=True)
    last_counter = db.Column(db.BigInteger, nullable=False, default=-1)
    failed_attempts = db.Column(db.Integer, nullable=False, default=0)
    failure_window_started = db.Column(db.BigInteger, nullable=False, default=0)
    blocked_until = db.Column(db.BigInteger, nullable=False, default=0)
    enabled_at = db.Column(db.BigInteger, nullable=True)
    disabled_at = db.Column(db.BigInteger, nullable=True)


class AuthRecoveryCode(db.Model):
    __tablename__ = "briva_auth_recovery"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    company_id = db.Column(db.String(100), nullable=False, index=True)
    code_hash = db.Column(db.String(64), nullable=False, unique=True)
    created_at = db.Column(db.BigInteger, nullable=False)
    used_at = db.Column(db.BigInteger, nullable=True)
    retired_at = db.Column(db.BigInteger, nullable=True)


class AuthChallenge(db.Model):
    __tablename__ = "briva_auth_challenge"
    token_hash = db.Column(db.String(64), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    company_id = db.Column(db.String(100), nullable=False)
    credential_fingerprint = db.Column(db.String(64), nullable=False)
    created_at = db.Column(db.BigInteger, nullable=False)
    expires_at = db.Column(db.BigInteger, nullable=False)
    used_at = db.Column(db.BigInteger, nullable=True)


class AuthAccess(db.Model):
    __tablename__ = "briva_auth_access"
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), primary_key=True)
    company_id = db.Column(db.String(100), nullable=False, index=True)
    disabled_at = db.Column(db.BigInteger, nullable=True)
    disabled_reason = db.Column(db.String(240), nullable=True)
    changed_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    changed_at = db.Column(db.BigInteger, nullable=False)


class AuthLoginBucket(db.Model):
    """Bounded aggregate slots; slot zero serializes allocation, never users."""
    __tablename__ = "briva_auth_login_bucket"
    id = db.Column(db.Integer, primary_key=True, autoincrement=False)
    key_hash = db.Column(db.String(64), nullable=False, unique=True)
    kind = db.Column(db.String(8), nullable=False)
    attempt_count = db.Column(db.Integer, nullable=False)
    window_started = db.Column(db.BigInteger, nullable=False)
    expires_at = db.Column(db.BigInteger, nullable=False, index=True)


AUTH_TABLE_NAMES = frozenset({
    AuthSession.__tablename__, AuthFactor.__tablename__,
    AuthRecoveryCode.__tablename__, AuthChallenge.__tablename__,
    AuthAccess.__tablename__,
    AuthLoginBucket.__tablename__,
})
