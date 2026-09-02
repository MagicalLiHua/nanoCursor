from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from nanocursor.eval.contract import PROTOCOL_VERSION, ToolName


class BridgeError(RuntimeError):
    pass


@dataclass(frozen=True)
class BridgeCallResult:
    result: Any
    duration_ms: int


class BridgeClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_seconds: float = 200.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"}:
            raise ValueError("Tool bridge URL must use HTTP on the local loopback interface.")
        if not token:
            raise ValueError("Tool bridge token must not be empty.")
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"authorization": f"Bearer {token}"},
            timeout=timeout_seconds,
            transport=transport,
        )

    async def health(self) -> None:
        try:
            response = await self._client.get("/health")
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise BridgeError(f"Tool bridge health check failed: {error}") from error
        if not isinstance(payload, dict) or payload.get("protocolVersion") != PROTOCOL_VERSION:
            raise BridgeError("Tool bridge returned an incompatible protocol version.")

    async def call(
        self,
        tool: ToolName,
        arguments: dict[str, Any],
        *,
        tool_call_id: str | None = None,
    ) -> BridgeCallResult:
        payload = {
            "protocolVersion": PROTOCOL_VERSION,
            "toolCallId": tool_call_id or uuid4().hex,
            "tool": tool,
            "arguments": arguments,
        }
        try:
            response = await self._client.post("/v1/tool-call", json=payload)
            body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise BridgeError(f"Tool bridge request failed: {error}") from error
        if not isinstance(body, dict):
            raise BridgeError("Tool bridge returned a non-object response.")
        if response.status_code >= 400 or body.get("ok") is not True:
            message = body.get("error")
            raise BridgeError(str(message) if message else f"Tool bridge returned HTTP {response.status_code}.")
        if body.get("protocolVersion") != PROTOCOL_VERSION:
            raise BridgeError("Tool bridge returned an incompatible protocol version.")
        duration_ms = body.get("durationMs", 0)
        if not isinstance(duration_ms, int):
            duration_ms = 0
        return BridgeCallResult(result=body.get("result"), duration_ms=duration_ms)

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> BridgeClient:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.close()
