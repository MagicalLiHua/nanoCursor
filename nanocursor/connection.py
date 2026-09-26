"""Bounded provider checks; never load project files or enable model tools."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from nanocursor.config import ProviderConfig
from nanocursor.credentials import normalize_endpoint
from nanocursor.validator import ConfigError


@dataclass
class ConnectionResult:
    ok: bool
    code: str
    message: str


async def check_connection(provider: ProviderConfig, timeout: float = 10) -> ConnectionResult:
    try:
        key = provider.resolve_api_key()
        if not key:
            return ConnectionResult(False, "CREDENTIAL_MISSING", "No credential found; run nanocursor setup")
        base = normalize_endpoint(provider.base_url)
        headers = {}
        if provider.auth != "none":
            headers = {"Authorization": f"Bearer {key}"}
        if provider.protocol == "anthropic":
            # Match the Anthropic SDK's relative /v1/messages route exactly.
            url = base + "/v1/messages"
            headers = {"anthropic-version": "2023-06-01"}
            if provider.auth != "none":
                headers["x-api-key"] = key
            body = {"model": provider.model, "max_tokens": 16,
                    "messages": [{"role": "user", "content": "Reply OK."}]}
        elif provider.protocol == "openai":
            url = base + "/responses"
            body = {"model": provider.model, "input": "Reply OK.", "max_output_tokens": 16, "store": False}
        else:
            url = base + "/chat/completions"
            body = {"model": provider.model, "max_tokens": 16,
                    "messages": [{"role": "user", "content": "Reply OK."}]}
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                response = await client.post(url, headers=headers, json=body)
        status = response.status_code
        if status == 200:
            try:
                data = response.json()
                field = {"anthropic": "content", "openai": "output", "openai-compat": "choices"}[provider.protocol]
                if not isinstance(data, dict) or not isinstance(data.get(field), list):
                    raise ValueError
            except ValueError:
                return ConnectionResult(False, "PROTOCOL_ERROR", "Endpoint returned an unexpected response format")
            return ConnectionResult(True, "CONNECTED", "Provider accepted a short model request")
        code, message = {
            401: ("AUTH_FAILED", "Credential was rejected"),
            403: ("ACCESS_DENIED", "Provider denied access; check account and model permissions"),
            404: ("ENDPOINT_OR_MODEL", "Endpoint or model was not found; check protocol, Base URL and model ID"),
            429: ("RATE_LIMITED", "Provider rate or quota limit; check account limits and retry later"),
        }.get(status, ("PROVIDER_ERROR", "Provider rejected the request; check model, protocol and service status"))
        # Do not print response bodies: services can echo credentials/request data.
        return ConnectionResult(False, code, f"HTTP {status}: {message}")
    except (TimeoutError, httpx.TimeoutException):
        return ConnectionResult(False, "CONNECTION_TIMEOUT", "Connection test timed out")
    except httpx.ConnectError as exc:
        cause = str(exc).lower()
        if "certificate" in cause or "ssl" in cause:
            return ConnectionResult(False, "TLS_ERROR", "TLS verification failed; check certificates and proxy")
        if "name" in cause or "nodename" in cause or "resolve" in cause:
            return ConnectionResult(False, "DNS_ERROR", "Could not resolve provider host")
        return ConnectionResult(False, "CONNECTION_FAILED", "Could not connect; check network, host and proxy")
    except httpx.ProxyError:
        return ConnectionResult(False, "PROXY_ERROR", "Could not connect through the configured proxy")
    except httpx.HTTPError:
        return ConnectionResult(False, "NETWORK_ERROR", "Network request failed")
    except (ConfigError, OSError):
        return ConnectionResult(False, "CREDENTIAL_ERROR", "Credential source or endpoint binding is invalid; run doctor")
