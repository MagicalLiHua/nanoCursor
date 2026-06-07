"""Short-lived approval tokens shared with the optional Go runtime."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any


DEFAULT_APPROVAL_TOKEN_TTL_SECONDS = 300
DEFAULT_DEV_SECRET = "nanocursor-local-runtime-approval-secret"


def approval_secret() -> str:
    return os.getenv("NANOCURSOR_RUNTIME_APPROVAL_SECRET", DEFAULT_DEV_SECRET)


def create_approval_token(
    *,
    approval_id: str,
    command: str,
    workspace_dir: str,
    permission_level: str,
    ttl_seconds: int = DEFAULT_APPROVAL_TOKEN_TTL_SECONDS,
) -> str:
    payload = {
        "approval_id": approval_id,
        "command": command,
        "workspace_dir": workspace_dir,
        "permission_level": permission_level,
        "expires_at": int(time.time()) + max(1, ttl_seconds),
    }
    raw_payload = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = _sign(raw_payload)
    return f"{raw_payload}.{signature}"


def decode_unsigned_payload(token: str) -> dict[str, Any]:
    raw_payload = token.split(".", 1)[0]
    padded = raw_payload + "=" * (-len(raw_payload) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))


def _sign(raw_payload: str) -> str:
    digest = hmac.new(approval_secret().encode("utf-8"), raw_payload.encode("utf-8"), hashlib.sha256).digest()
    return _b64(digest)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")

