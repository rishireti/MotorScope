"""Password hashing, one-time token generation, and signed session cookies."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import settings

_PBKDF2_ALGORITHM = "pbkdf2_sha256"
_PBKDF2_ITERATIONS = 600_000
_SALT_BYTES = 16


@dataclass(frozen=True)
class PasswordHash:
    algorithm: str
    iterations: int
    salt: str
    digest: str

    def encode(self) -> str:
        return f"{self.algorithm}${self.iterations}${self.salt}${self.digest}"

    @classmethod
    def decode(cls, raw: str) -> "PasswordHash | None":
        parts = raw.split("$")
        if len(parts) != 4:
            return None
        algorithm, iterations, salt, digest = parts
        if algorithm != _PBKDF2_ALGORITHM:
            return None
        try:
            return cls(algorithm, int(iterations), salt, digest)
        except ValueError:
            return None


def _derive(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations, dklen=32
    )


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = _derive(password, salt, _PBKDF2_ITERATIONS)
    return PasswordHash(
        _PBKDF2_ALGORITHM, _PBKDF2_ITERATIONS, salt.hex(), digest.hex()
    ).encode()


def verify_password(password: str, encoded: str) -> bool:
    parsed = PasswordHash.decode(encoded)
    if parsed is None:
        return False
    try:
        expected = bytes.fromhex(parsed.digest)
        salt = bytes.fromhex(parsed.salt)
    except ValueError:
        return False
    candidate = _derive(password, salt, parsed.iterations)
    return hmac.compare_digest(candidate, expected)


def generate_token() -> str:
    """Return a high-entropy single-use token handed out to the user."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Tokens are stored only as digests so a DB leak cannot be replayed."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


_serializer = URLSafeTimedSerializer(settings.secret_key, salt="motorscope.session")


def issue_session(user_id: int, email: str) -> str:
    return _serializer.dumps({"uid": user_id, "email": email})


def read_session(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        return _serializer.loads(raw, max_age=settings.session_max_age)
    except (BadSignature, SignatureExpired):
        return None


_oauth_serializer = URLSafeTimedSerializer(
    settings.secret_key, salt="motorscope.oauth"
)


def issue_oauth_state(state: str, code_verifier: str, next_path: str) -> str:
    """Signed bundle carried across the Google round trip in a short-lived cookie."""
    return _oauth_serializer.dumps(
        {"state": state, "verifier": code_verifier, "next": next_path}
    )


def read_oauth_state(raw: str | None, max_age: int = 600) -> dict | None:
    if not raw:
        return None
    try:
        return _oauth_serializer.loads(raw, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
