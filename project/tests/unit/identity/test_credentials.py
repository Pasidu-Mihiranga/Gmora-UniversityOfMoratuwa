"""SEC-1 to SEC-3 building blocks: Argon2id, HS256 tokens on the Clock, opaque token hashes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from suwapath.modules.identity.domain import credentials as c

NOW = datetime(2026, 11, 28, 9, 0, tzinfo=UTC)
SECRET = "x" * 32


def test_passwords_are_argon2id_and_verify() -> None:
    stored = c.hash_password("correct horse")
    assert stored.startswith("$argon2id$")
    assert c.verify_password(stored, "correct horse")
    assert not c.verify_password(stored, "wrong")
    assert not c.verify_password(None, "anything")  # unknown account: same work, always false
    assert not c.verify_password("not-a-hash", "x")


def test_access_tokens_carry_the_spec_claims_and_expire_on_the_clock() -> None:
    user, session = uuid4(), uuid4()
    token, expires = c.issue_access_token(
        SECRET, user_id=user, role="patient", session_id=session, now=NOW
    )
    assert expires == NOW + timedelta(minutes=15)
    claims = c.read_access_token(SECRET, token, NOW + timedelta(minutes=14))
    assert claims["sub"] == str(user) and claims["sid"] == str(session)
    assert claims["role"] == "patient"
    assert set(claims) == {"sub", "role", "sid", "iat", "exp", "jti"}
    with pytest.raises(c.TokenInvalid):
        c.read_access_token(SECRET, token, NOW + timedelta(minutes=15))


@pytest.mark.parametrize("tamper", ["secret", "alg_none", "garbage"])
def test_forged_tokens_are_refused(tamper: str) -> None:
    claims = {
        "sub": str(uuid4()),
        "role": "system_admin",
        "sid": "s",
        "iat": 0,
        "exp": 2**40,
        "jti": "j",
    }
    token = {
        "secret": jwt.encode(claims, "y" * 32, algorithm="HS256"),
        "alg_none": jwt.encode(claims, None, algorithm="none"),
        "garbage": "a.b.c",
    }[tamper]
    with pytest.raises(c.TokenInvalid):
        c.read_access_token(SECRET, token, NOW)


def test_opaque_tokens_are_random_and_only_their_hash_is_kept() -> None:
    a, b = c.opaque_token(), c.opaque_token()
    assert a != b and len(a) >= 43  # 256 bits
    assert c.token_hash(a) != a and len(c.token_hash(a)) == 64
    assert c.login_hash(" Amma@Example.test ") == c.login_hash("amma@example.test")
