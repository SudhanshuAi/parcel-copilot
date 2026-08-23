"""Server-issued mock authentication for the assessment's customer context."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable, Final


class AuthenticationError(ValueError):
    """Raised when an untrusted session cookie cannot produce an AuthContext."""


@dataclass(frozen=True)
class DemoIdentity:
    identity_id: str
    display_name: str
    user_id: str
    account_id: str
    role: str = "customer"


@dataclass(frozen=True)
class AuthContext:
    """Trusted request context injected by the server, never model/client tool input."""

    user_id: str
    account_id: str
    role: str
    session_id: str
    expires_at: datetime


DEMO_IDENTITIES: Final[dict[str, DemoIdentity]] = {
    "northstar_demo": DemoIdentity("northstar_demo", "Northstar Logistics demo user", "user-northstar-demo", "ACCT-001"),
    "lumenworks_demo": DemoIdentity("lumenworks_demo", "LumenWorks demo user", "user-lumenworks-demo", "ACCT-002"),
    "beacon_demo": DemoIdentity("beacon_demo", "Beacon Retail demo user", "user-beacon-demo", "ACCT-003"),
    "axis_demo": DemoIdentity("axis_demo", "Axis Labs demo user", "user-axis-demo", "ACCT-004"),
}


class SessionSigner:
    """HMAC-signed, short-lived cookie sessions with no client-controlled claims."""

    def __init__(
        self,
        secret: str,
        *,
        lifetime: timedelta = timedelta(hours=8),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if len(secret) < 32:
            raise ValueError("Session secret must be at least 32 characters long")
        self._secret = secret.encode("utf-8")
        self._lifetime = lifetime
        self._clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def _b64encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _b64decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)

    def issue(self, identity_id: str) -> str:
        identity = DEMO_IDENTITIES.get(identity_id)
        if identity is None:
            raise AuthenticationError("Unknown demo identity")
        now = self._clock()
        expires_at = now + self._lifetime
        payload = {
            "account_id": identity.account_id,
            "exp": int(expires_at.timestamp()),
            "role": identity.role,
            "session_id": secrets.token_urlsafe(18),
            "user_id": identity.user_id,
            "v": 1,
        }
        encoded_payload = self._b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        signature = hmac.new(self._secret, encoded_payload.encode("ascii"), hashlib.sha256).digest()
        return f"{encoded_payload}.{self._b64encode(signature)}"

    def verify(self, token: str) -> AuthContext:
        try:
            encoded_payload, encoded_signature = token.split(".", 1)
            raw_payload = self._b64decode(encoded_payload)
            raw_signature = self._b64decode(encoded_signature)
            if self._b64encode(raw_payload) != encoded_payload or self._b64encode(raw_signature) != encoded_signature:
                raise AuthenticationError("Non-canonical session encoding")
            expected = hmac.new(self._secret, encoded_payload.encode("ascii"), hashlib.sha256).digest()
            if not hmac.compare_digest(expected, raw_signature):
                raise AuthenticationError("Invalid session signature")
            payload = json.loads(raw_payload)
            required = {"account_id", "exp", "role", "session_id", "user_id", "v"}
            if set(payload) != required or payload["v"] != 1 or payload["role"] != "customer":
                raise AuthenticationError("Invalid session payload")
            expires_at = datetime.fromtimestamp(int(payload["exp"]), tz=UTC)
            if expires_at <= self._clock():
                raise AuthenticationError("Session has expired")
            if not all(isinstance(payload[key], str) and payload[key] for key in ("account_id", "session_id", "user_id")):
                raise AuthenticationError("Invalid session claims")
            return AuthContext(
                user_id=payload["user_id"],
                account_id=payload["account_id"],
                role=payload["role"],
                session_id=payload["session_id"],
                expires_at=expires_at,
            )
        except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError, base64.binascii.Error) as exc:
            if isinstance(exc, AuthenticationError):
                raise
            raise AuthenticationError("Malformed session") from exc

    def issue_csrf(self, session_id: str) -> str:
        nonce = secrets.token_urlsafe(18)
        message = f"v1.{session_id}.{nonce}".encode("utf-8")
        signature = hmac.new(self._secret, message, hashlib.sha256).digest()
        return f"{self._b64encode(message)}.{self._b64encode(signature)}"

    def verify_csrf(self, token: str, auth: AuthContext) -> bool:
        try:
            encoded_message, encoded_signature = token.split(".", 1)
            message = self._b64decode(encoded_message)
            signature = self._b64decode(encoded_signature)
            if self._b64encode(message) != encoded_message or self._b64encode(signature) != encoded_signature:
                return False
            expected = hmac.new(self._secret, message, hashlib.sha256).digest()
            if not hmac.compare_digest(expected, signature):
                return False
            version, session_id, nonce = message.decode("utf-8").split(".", 2)
            return version == "v1" and session_id == auth.session_id and bool(nonce)
        except (ValueError, UnicodeDecodeError, base64.binascii.Error):
            return False
