"""Read one complete connection from the inherited process environment."""
from __future__ import annotations

import os

from nanocursor.validator import ConfigError, MissingConfigError


ENVIRONMENT_PROVIDERS = {
    "deepseek": ("DEEPSEEK", "openai-compat"),
    "anthropic": ("ANTHROPIC", "anthropic"),
    "openai": ("OPENAI", "openai"),
}


class EnvironmentSelectionRequired(ConfigError):
    code = "ENV_CONFIG_AMBIGUOUS"

    def __init__(self, choices: list[str]):
        self.choices = choices
        super().__init__(
            "Several complete environment connections found: " + ", ".join(choices)
            + ". Select one with --env NAME."
        )


def connection_variables(name: str) -> tuple[str, str, str]:
    prefix, _ = ENVIRONMENT_PROVIDERS[name]
    return tuple(f"{prefix}_{suffix}" for suffix in ("API_KEY", "BASE_URL", "MODEL"))


def environment_connection(name: str | None = None) -> tuple[dict, str]:
    """Never infer an endpoint from a key alone or combine different groups.

    The returned profile contains the key's variable name, never its value.
    Files and shell startup scripts are not loaded or modified here.
    """
    missing = {
        group: [variable for variable in connection_variables(group)
                if not os.environ.get(variable, "").strip()]
        for group in ENVIRONMENT_PROVIDERS
    }
    if name is None:
        complete = [group for group, fields in missing.items() if not fields]
        if len(complete) > 1:
            raise EnvironmentSelectionRequired(complete)
        if not complete:
            hints = [f"{group}: missing {', '.join(fields)}" for group, fields in missing.items()
                     if any(os.environ.get(v, "").strip() for v in connection_variables(group))]
            message = "No connection configured. Run nanocursor setup or export API_KEY, BASE_URL and MODEL with a DEEPSEEK_, ANTHROPIC_ or OPENAI_ prefix."
            if hints:
                message += " " + "; ".join(hints)
            raise MissingConfigError(message)
        name = complete[0]
    if name not in ENVIRONMENT_PROVIDERS:
        raise ConfigError("Unknown environment connection; choose deepseek, anthropic or openai")
    if missing[name]:
        raise ConfigError("Environment connection is incomplete; export " + ", ".join(missing[name]))
    key_var, url_var, model_var = connection_variables(name)
    _, protocol = ENVIRONMENT_PROVIDERS[name]
    sources = [key_var, url_var, model_var]
    if name == "openai" and os.environ.get("OPENAI_PROTOCOL", "").strip():
        protocol = os.environ["OPENAI_PROTOCOL"].strip()
        if protocol not in {"openai", "openai-compat"}:
            raise ConfigError("OPENAI_PROTOCOL must be openai or openai-compat")
        sources.append("OPENAI_PROTOCOL")
    from nanocursor.credentials import normalize_endpoint
    try:
        endpoint = normalize_endpoint(os.environ[url_var].strip())
    except ConfigError as exc:
        raise ConfigError(f"{url_var} must be a valid HTTP(S) base URL without credentials, query or fragment") from exc
    return {
        "name": f"env:{name}", "protocol": protocol,
        "base_url": endpoint, "model": os.environ[model_var].strip(), "api_key_env": key_var,
    }, "environment: " + ", ".join(sources)
