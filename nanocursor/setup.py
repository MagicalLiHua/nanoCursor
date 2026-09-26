"""Terminal onboarding and a reusable, transactional settings save operation."""
from __future__ import annotations

import asyncio
import getpass
import os
import uuid
import warnings
from copy import deepcopy
from urllib.parse import urlsplit

import yaml

from nanocursor.config import ProviderConfig, _from_raw, read_config_file
from nanocursor.credentials import normalize_endpoint, target
from nanocursor.runtime import app_home
from nanocursor.storage import atomic_write, read_json, read_private_file, state_lock, write_json
from nanocursor.validator import ConfigError


def save_profile(raw: dict, profile: dict, key: str | None, snapshot: bytes) -> None:
    """Write a new credential before referencing it. Existing records survive failure."""
    directory = app_home()
    if key is not None and (not isinstance(key, str) or not key.strip()):
        raise ConfigError("Credential must be a non-empty string")
    config_path = directory / "config.yaml"
    updated = deepcopy(raw)
    updated.setdefault("schema_version", 1 if snapshot else 2)
    entries = updated.setdefault("providers", [])
    profile = deepcopy(profile)
    if key is not None:
        profile["credential_ref"] = uuid.uuid4().hex
    updated["providers"] = [p for p in entries if p["name"] != profile["name"]] + [profile]
    updated["default_provider"] = profile["name"]
    _from_raw(updated)
    encoded = yaml.safe_dump(updated, allow_unicode=True, sort_keys=False).encode()
    with state_lock(directory):
        if read_private_file(config_path) != snapshot:
            raise ConfigError("Configuration changed during setup; rerun setup to avoid overwriting it")
        if snapshot:
            atomic_write(directory / f"config.backup-{uuid.uuid4().hex}.yaml", snapshot)
        if key is not None:
            path = directory / "credentials.json"
            store = read_json(path)
            if store.get("schema_version", 1) != 1 or not isinstance(store.get("credentials", {}), dict):
                raise ConfigError("Unsupported or damaged credentials store; repair it before saving")
            store.setdefault("schema_version", 1)
            record = {"target": target(ProviderConfig(**{k: v for k, v in profile.items()
                        if k in ProviderConfig.__dataclass_fields__})), "api_key": key}
            store.setdefault("credentials", {})[profile["credential_ref"]] = record
            write_json(path, store)
        atomic_write(config_path, encoded)


def run_setup(*, input_fn=None, secret_fn=None, output=print) -> bool:
    ask = input_fn or input
    read_secret = secret_fn or getpass.getpass
    path = app_home() / "config.yaml"
    snapshot = read_private_file(path)
    raw = read_config_file(path) if snapshot else {"schema_version": 2}
    if snapshot:
        _from_raw(raw)
    existing = {p["name"]: p for p in raw.get("providers", [])}

    def prompt(label: str, default: str = "", *, required: bool = False) -> str:
        while True:
            value = ask(f"{label}" + (f" [{default}]" if default else "") + ": ").strip() or default
            if value or not required:
                return value
            output("Please enter a value.")

    try:
        output("nanoCursor setup — configure one connection; Ctrl-C cancels without saving.")
        output("1 DeepSeek   2 Anthropic   3 OpenAI Responses   4 Custom Chat Completions")
        choice = prompt("Provider", "1")
        presets = {
            "1": ("deepseek", "openai-compat", "https://api.deepseek.com", "DEEPSEEK_API_KEY"),
            "2": ("anthropic", "anthropic", "https://api.anthropic.com", "ANTHROPIC_API_KEY"),
            "3": ("openai", "openai", "https://api.openai.com/v1", "OPENAI_API_KEY"),
            "4": ("custom", "openai-compat", "", "OPENAI_API_KEY"),
        }
        if choice not in presets:
            raise ConfigError("Choose provider 1, 2, 3 or 4")
        name, protocol, base, env_name = presets[choice]
        name = prompt("Profile name", name, required=True)
        old = existing.get(name, {})
        prefix = env_name.removesuffix("_API_KEY")
        base = normalize_endpoint(prompt("Base URL", old.get("base_url") or os.environ.get(f"{prefix}_BASE_URL", "").strip() or base, required=True))
        if choice == "4":
            protocol = prompt("Protocol (openai-compat / openai / anthropic)", old.get("protocol") or os.environ.get("OPENAI_PROTOCOL", "").strip() or protocol)
        model = prompt("Model ID (from your provider)", old.get("model") or os.environ.get(f"{prefix}_MODEL", "").strip(), required=True)
        output("Credential: 1 save in private plaintext file; 2 environment variable; 3 no authentication")
        variable = old.get("api_key_env") or env_name
        method = prompt("Credential method", "2" if os.environ.get(variable, "").strip() else "1")
        # Preserve model tuning/unknown provider fields, replace credential routing explicitly.
        profile = {k: deepcopy(v) for k, v in old.items()
                   if k not in {"api_key", "api_key_env", "credential_ref", "auth"}}
        profile.update(name=name, protocol=protocol, base_url=base, model=model)
        key = None
        if method == "1":
            output(f"Stored at {app_home() / 'credentials.json'} (plaintext, current-user permissions).")
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                key = read_secret("API key (hidden): ").strip()
            if not key:
                raise ConfigError("API key cannot be empty; nothing was saved")
        elif method == "2":
            profile["api_key_env"] = prompt("Environment variable name", old.get("api_key_env", env_name), required=True)
        elif method == "3":
            profile["auth"] = "none"
        else:
            raise ConfigError("Choose credential method 1, 2 or 3")
        check = {**profile, **({"api_key": key} if key is not None else {})}
        candidate = _from_raw({"providers": [check]}).providers[0]
        if method != "3" and urlsplit(base).scheme == "http" and urlsplit(base).hostname not in {"localhost", "127.0.0.1", "::1"}:
            if prompt("This endpoint sends credentials over HTTP. Continue? (y/N)", "n").lower() != "y":
                output("Cancelled; nothing saved.")
                return False
        output(f"Connection: {name} | {protocol} | {base} | {model}")
        output("Optional test sends only 'Reply OK.' and may incur a small API charge.")
        verified = False
        if prompt("Test connection now? (y/N)", "n").lower() == "y":
            from nanocursor.connection import check_connection
            result = asyncio.run(check_connection(candidate))
            output(f"{result.code}: {result.message}")
            verified = result.ok
        if not verified:
            output("Connection has not been verified; you can retry with nanocursor doctor --network.")
        if old:
            output("This replaces the selected profile's credential source. A private backup of the old config will be kept.")
        if prompt("Save and use as default? (Y/n)", "y").lower() != "y":
            output("Cancelled; nothing saved.")
            return False
        save_profile(raw, profile, key, snapshot)
        output(f"Saved {name}. Run nanocursor in your project directory.")
        return True
    except (EOFError, KeyboardInterrupt):
        output("\nCancelled; nothing saved.")
        return False
    except getpass.GetPassWarning as exc:
        raise ConfigError("Cannot hide secret input in this terminal; use an interactive terminal") from exc
