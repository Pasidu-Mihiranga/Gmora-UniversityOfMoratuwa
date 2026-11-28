"""Passwords, access tokens and opaque tokens (SEC-1 to SEC-3, SEC-6).

Pure functions; time is passed in (EVT-16). Nothing here logs a secret, a token or a password.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

ACCESS_TTL = timedelta(minutes=15)  # SEC-2
REFRESH_TTL = timedelta(days=7)  # SEC-3
ALGORITHM = "HS256"
_hasher = PasswordHasher()  # argon2-cffi defaults: Argon2id (SEC-1)
# A real hash of a random password, so a login for an unknown account costs the same time.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


class TokenInvalid(Exception):  # noqa: N818  (a verdict, carries no detail on purpose)
    pass


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def opaque_token() -> str:
    """256 random bits (SEC-3); also used for stream tickets (SEC-5)."""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    """Only this is stored (SEC-3)."""
    return hashlib.sha256(token.encode()).hexdigest()


def login_hash(login: str) -> str:
    """Login attempts are counted by a hash, so the attempts table holds no emails or phones."""
    return hashlib.sha256(normalize_login(login).encode()).hexdigest()


def normalize_login(login: str) -> str:
    return login.strip().casefold()


def issue_access_token(
    secret: str, *, user_id: UUID, role: str, session_id: UUID, now: datetime
) -> tuple[str, datetime]:
    expires = now + ACCESS_TTL
    claims = {
        "sub": str(user_id),
        "role": role,
        "sid": str(session_id),
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(claims, secret, algorithm=ALGORITHM), expires


def read_access_token(secret: str, token: str, now: datetime) -> dict[str, Any]:
    """Claims of a valid token. Expiry is checked against the Clock port, not the wall clock, so
    the simulated clock (EVT-17) and tokens agree."""
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            options={
                "require": ["sub", "role", "sid", "iat", "exp", "jti"],
                "verify_exp": False,
                "verify_iat": False,
                "verify_nbf": False,
            },
        )
    except jwt.PyJWTError:
        raise TokenInvalid() from None
    if int(claims["exp"]) <= int(now.timestamp()):
        raise TokenInvalid()
    return claims


def same(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)
