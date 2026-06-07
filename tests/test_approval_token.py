"""Approval token tests for Python/Go runtime handoff."""

from __future__ import annotations

import time

from src.runtime.approval_token import create_approval_token, decode_unsigned_payload


def test_create_approval_token_contains_expected_claims(tmp_path):
    token = create_approval_token(
        approval_id="approval_123",
        command="rm -rf dist",
        workspace_dir=str(tmp_path),
        permission_level="shell_risky",
    )

    payload = decode_unsigned_payload(token)

    assert payload["approval_id"] == "approval_123"
    assert payload["command"] == "rm -rf dist"
    assert payload["workspace_dir"] == str(tmp_path)
    assert payload["permission_level"] == "shell_risky"
    assert payload["expires_at"] > int(time.time())
