"""Credentials stay outside project configuration and are bound to an endpoint."""
from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from nanocursor.runtime import app_home
from nanocursor.storage import read_json
from nanocursor.validator import ConfigError


def normalize_endpoint(url: str) -> str:
    if not isinstance(url, str) or any(ch.isspace() or ord(ch) < 32 for ch in url):
        raise ConfigError("base_url must be a valid HTTP(S) URL without whitespace")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (ValueError, TypeError) as exc:
        raise ConfigError("base_url must be a valid HTTP(S) URL") from exc
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ConfigError("base_url must be HTTP(S), without credentials, query or fragment")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    if port and not (parsed.scheme == "https" and port == 443 or parsed.scheme == "http" and port == 80):
        host += f":{port}"
    return urlunsplit((parsed.scheme, host, parsed.path.rstrip("/"), "", ""))


def target(provider) -> dict[str, str]:
    return {"provider": provider.name, "protocol": provider.protocol,
            "base_url": normalize_endpoint(provider.base_url)}


def read_credential(provider) -> str:
    data = read_json(app_home() / "credentials.json")
    if data.get("schema_version", 1) != 1:
        raise ConfigError("Unsupported credentials schema; update nanoCursor")
    records = data.get("credentials", {})
    if not isinstance(records, dict):
        raise ConfigError("Invalid credentials store")
    record = records.get(provider.credential_ref)
    if record is None:
        return ""
    if not isinstance(record, dict) or record.get("target") != target(provider):
        raise ConfigError("Saved credential belongs to another provider or endpoint; run nanocursor setup")
    key = record.get("api_key")
    if not isinstance(key, str) or not key.strip():
        raise ConfigError("Saved credential is invalid; run nanocursor setup")
    return key
