"""Password, invitation, and opaque bearer-session helpers."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from .models import now_utc


PBKDF2_ROUNDS = 310_000


def normalize_email(value: str) -> str:
    email = str(value or "").strip().lower()
    if "@" not in email or len(email) > 320:
        raise ValueError("A valid email address is required.")
    return email


def hash_password(password: str) -> str:
    if len(password) < 8 or len(password) > 256:
        raise ValueError("Password must contain between 8 and 256 characters.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(rounds)
        ).hex()
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(actual, expected)


def digest_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def issue_session_token(*, lifetime_hours: int = 12) -> tuple[str, str, object]:
    token = secrets.token_urlsafe(40)
    return token, digest_secret(token), now_utc() + timedelta(hours=lifetime_hours)


def issue_invite_code() -> tuple[str, str]:
    code = "RG3D-" + secrets.token_urlsafe(8).upper().replace("-", "X").replace("_", "Y")
    return code, digest_secret(code)

