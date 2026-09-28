from __future__ import annotations

import os
import re
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .validator import (
    ConfigError,
    DEFAULT_CONTEXT_WINDOW,
    VALID_PERMISSION_MODES,
    VALID_PROTOCOLS,
    VALID_TEAMMATE_MODES,
    lookup_model_context_window,
    validate_config_structure,
    MissingConfigError,
    CredentialError,
)
from .runtime import app_home


_ENV_KEY_MAP = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openai-compat": "OPENAI_API_KEY",
}

_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")


@dataclass
class ProviderConfig:
    name: str
    protocol: str
    base_url: str
    model: str
    api_key: str = field(default="", repr=False)
    thinking: bool = False
    # 0 表示"未设置" — get_context_window() 通过四层 fallback 解析真实窗口大小。
    # 正数表示配置文件里显式指定的覆盖值。
    context_window: int = 0
    max_output_tokens: int = 0
    api_key_env: str = ""
    credential_ref: str = ""
    auth: str = "key"
    _needs_trust: bool = field(default=False, repr=False)
    # 运行时 cache，存放从 provider 的 /v1/models 端点自动拉取的 context window
    # （get_context_window 的第 2 层）。通过 set_fetched_context_window() 写入一次；
    # 0 表示"尚未拉取"。不会持久化。
    _fetched_context_window: int = field(default=0, repr=False)

    def resolve_api_key(self) -> str:
        if self._needs_trust:
            raise ConfigError("Project provider needs approval of its endpoint; run nanocursor in a terminal first")
        if self.auth == "none":
            return "not-required"
        if self.api_key_env:
            return os.environ.get(self.api_key_env, "")
        if self.credential_ref:
            from .credentials import read_credential
            return read_credential(self)
        if self.api_key:
            return self.api_key
        env_var = _ENV_KEY_MAP.get(self.protocol, "")
        return os.environ.get(env_var, "")

    def set_fetched_context_window(self, window: int) -> None:
        """记录从 provider 自动拉取到的 context window（第 2 层）。

        非正数会被忽略，这样一次失败的拉取就不会污染 cache。在解析
        context window 时，每个 provider 只会调用一次。
        """
        if window > 0:
            self._fetched_context_window = window

    def get_context_window(self) -> int:
        """通过四层 fallback 解析模型的 context window，按优先级从高到低：

          1. 配置文件提供的 context_window（> 0）——显式覆盖，永远优先。
          2. 从 provider 的 /v1/models 端点自动拉取并通过 set_fetched_context_window
             缓存的值（只有 anthropic 协议的 provider 才会设置它；拉取失败或缺失时
             保持为 0 并跳过）。
          3. 内置的「模型名 -> window」映射表（按子串匹配）。
          4. 通用回退值 200000；已知模型的映射仍优先。
        """
        if self.context_window > 0:
            return self.context_window
        if self._fetched_context_window > 0:
            return self._fetched_context_window
        window = lookup_model_context_window(self.model)
        if window > 0:
            return window
        return DEFAULT_CONTEXT_WINDOW

    def get_max_output_tokens(self) -> int:
        if self.max_output_tokens > 0:
            return self.max_output_tokens
        if self.thinking:
            return 64000
        return 8192


def resolve_env_vars(value: str) -> str:
    return _ENV_VAR_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)


def build_child_env(declared_env: dict[str, str] | None) -> dict[str, str]:
    env: dict[str, str] = {}
    path = os.environ.get("PATH", "")
    if path:
        env["PATH"] = path
    for key, value in (declared_env or {}).items():
        env[key] = resolve_env_vars(value)
    return env


@dataclass
class MCPServerConfig:
    name: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)


    @property
    def is_stdio(self) -> bool:
        return self.command is not None


@dataclass
class WorktreeConfig:
    symlink_directories: list[str] = field(default_factory=list)
    stale_cleanup_interval: int = 3600
    stale_cutoff_hours: int = 24


@dataclass
class SandboxAppConfig:
    """沙箱相关的配置项。"""
    enabled: bool = False         # 是否启用 OS 级沙箱
    auto_allow: bool = False      # 是否自动放行命令（沙箱兜底）
    network_enabled: bool = False  # 沙箱内是否允许网络访问


@dataclass
class ApprovalConfig:
    mode: str = "manual"
    provider: str | None = None
    timeout_seconds: float = 10.0


@dataclass
class MemoryRecallConfig:
    mode: str = "local"
    max_context_tokens: int = 4096
    model_timeout_ms: int = 2000


@dataclass
class AppConfig:
    providers: list[ProviderConfig]
    permission_mode: str = "default"
    mcp_servers: list[MCPServerConfig] = field(default_factory=list)
    raw_hooks: list[dict] = field(default_factory=list)
    enable_fork: bool = False
    enable_teams: bool = False
    memory_consolidation_enabled: bool = False
    memory_recall: MemoryRecallConfig = field(default_factory=MemoryRecallConfig)
    enable_verification_agent: bool = False
    worktree: WorktreeConfig = field(default_factory=WorktreeConfig)
    teammate_mode: str = ""
    enable_coordinator_mode: bool = False
    sandbox: SandboxAppConfig = field(default_factory=SandboxAppConfig)
    approval: ApprovalConfig = field(default_factory=ApprovalConfig)
    _approval_fields: set[str] = field(default_factory=set, repr=False)
    default_provider: str = ""
    schema_version: int = 1
    sources: dict[str, str] = field(default_factory=dict)
    _raw: dict = field(default_factory=dict, repr=False)
    project_fingerprint: str = ""
    project_trusted: bool = True
    project_files: list[str] = field(default_factory=list)

    @property
    def selected_provider(self) -> ProviderConfig:
        return next((p for p in self.providers if p.name == self.default_provider), self.providers[0])


def read_config_file(path: Path) -> dict:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        # YAML parser excerpts can contain a secret. Report location, never source.
        raise ConfigError(f"Invalid YAML in {path}{location}") from exc
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"Cannot read configuration file: {path}") from exc
    if not isinstance(raw, dict) or not all(isinstance(key, str) for key in raw):
        raise ConfigError(f"Config must be a mapping: {path}")
    schema = raw.get("schema_version", 1)
    if type(schema) is not int or schema not in (1, 2):
        raise ConfigError(f"Unsupported schema_version in {path}; update nanoCursor")
    return raw


def _from_raw(raw: dict) -> AppConfig:
    if type(raw.get("schema_version", 1)) is not int or raw.get("schema_version", 1) not in (1, 2):
        raise ConfigError("Unsupported schema_version; update nanoCursor")
    validated = validate_config_structure(raw)
    default = raw.get("default_provider", "")
    if not isinstance(default, str):
        raise ConfigError("default_provider must be a provider name")
    providers = [ProviderConfig(**p) for p in validated["providers"]]
    if default and default not in {p.name for p in providers}:
        raise ConfigError("default_provider does not name a configured provider")
    return AppConfig(
        providers=providers,
        permission_mode=validated["permission_mode"],
        mcp_servers=[MCPServerConfig(**s) for s in validated["mcp_servers"]],
        raw_hooks=validated["hooks"],
        enable_fork=validated["enable_fork"],
        enable_teams=validated["enable_teams"],
        memory_consolidation_enabled=validated["memory"]["consolidation"]["enabled"],
        memory_recall=MemoryRecallConfig(**validated["memory"]["recall"]),
        enable_verification_agent=validated["enable_verification_agent"],
        worktree=WorktreeConfig(**validated["worktree"]),
        teammate_mode=validated["teammate_mode"],
        enable_coordinator_mode=validated["enable_coordinator_mode"],
        sandbox=SandboxAppConfig(**validated["sandbox"]),
        approval=ApprovalConfig(**validated["approval"]),
        _approval_fields=set(raw.get("approval") or {}),
        default_provider=default,
        schema_version=raw.get("schema_version", 1),
        _raw=deepcopy(raw),
    )


def _load_single_file(path: Path) -> AppConfig:
    try:
        return _from_raw(read_config_file(path))
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def _merge_raw(base: dict, override: dict) -> dict:
    result = deepcopy(base)
    schema = override.get("schema_version", base.get("schema_version", 1))
    for key, value in override.items():
        if key == "memory" and isinstance(value, dict):
            previous = result.get("memory") or {}
            result[key] = {**previous, **deepcopy(value)}
            for section in ("consolidation", "recall"):
                if isinstance(value.get(section), dict):
                    result[key][section] = {
                        **previous.get(section, {}), **value[section]}
        elif key in {"approval", "sandbox", "worktree"} and isinstance(value, dict):
            result[key] = {**(result.get(key) or {}), **deepcopy(value)}
        elif key == "hooks" and schema == 1:
            result[key] = list(result.get(key) or []) + list(value or [])
        elif key == "mcp_servers" and isinstance(value, list) and value:
            servers = {s["name"]: deepcopy(s) for s in result.get(key, [])}
            for server in value:
                servers[server["name"]] = deepcopy(server)
            result[key] = list(servers.values())
        else:
            result[key] = deepcopy(value)
    return result


def _merge_config(base: AppConfig, override: AppConfig) -> AppConfig:
    # Keep the helper used by callers/tests while merging explicit raw fields.
    return _from_raw(_merge_raw(base._raw, override._raw))


def _validate_layer(raw: dict, *, partial_providers: bool = False) -> None:
    seed = {"providers": [{"name": "validation", "protocol": "openai-compat",
                           "base_url": "https://example.invalid", "model": "validation"}]}
    candidate = {**seed, **raw}
    if partial_providers:
        candidate["providers"] = seed["providers"]
    for key in ("approval", "sandbox", "worktree", "memory"):
        if key in raw and not isinstance(raw[key], dict):
            raise ConfigError(f"{key} must be a mapping; reset individual fields explicitly")
    validate_config_structure(candidate)


def load_config(path: Path | None = None, *, work_dir: Path | str | None = None,
                env_provider: str | None = None) -> AppConfig:
    if path is not None:
        if env_provider is not None:
            raise ConfigError("An explicit config path cannot be combined with an environment connection")
        if not path.exists():
            raise MissingConfigError(f"Config file not found: {path}")
        return _load_single_file(path)

    cwd = Path(work_dir or Path.cwd()).resolve()
    global_path = app_home() / "config.yaml"
    candidates = [global_path, cwd / ".nanocursor" / "config.yaml", cwd / ".nanocursor" / "config.local.yaml"]
    merged: dict = {}
    sources: dict[str, str] = {}
    project_payload: list[dict] = []
    global_targets: set[tuple[str, str]] = set()
    seen: set[Path] = set()
    found = False
    user_consolidation_enabled = False
    user_model_recall = False
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if not candidate.exists():
            continue
        found = True
        raw = read_config_file(candidate)
        project_raw = deepcopy(raw)
        project = candidate != global_path
        version = raw.get("schema_version", merged.get("schema_version", 1))
        if raw.get("schema_version", version) < merged.get("schema_version", 1):
            raise ConfigError(f"Cannot downgrade configuration schema in {candidate}")
        try:
            partial = project and version == 2 and "providers" in raw
            _validate_layer(raw, partial_providers=partial)
            if partial:
                entries = raw["providers"]
                if not isinstance(entries, list) or not entries:
                    raise ConfigError("Project providers must be a non-empty list of existing profiles")
                by_name = {p["name"]: deepcopy(p) for p in merged.get("providers", [])}
                names = set()
                for entry in entries:
                    if not isinstance(entry, dict) or entry.get("name") not in by_name:
                        raise ConfigError("Define new connection profiles globally using nanocursor setup")
                    if entry["name"] in names:
                        raise ConfigError("Duplicate project provider name")
                    names.add(entry["name"])
                    if set(entry) - {"name", "model", "thinking", "context_window", "max_output_tokens"}:
                        raise ConfigError("Project profiles may change models, not endpoints or credential sources; use setup")
                    by_name[entry["name"]].update(entry)
                raw = {**raw, "providers": list(by_name.values())}
            if not project:
                user_consolidation_enabled = raw.get("memory", {}).get("consolidation", {}).get("enabled", False)
                user_model_recall = raw.get("memory", {}).get("recall", {}).get("mode") == "model"
            elif raw.get("memory", {}).get("consolidation", {}).get("enabled", False) and not user_consolidation_enabled:
                raise ConfigError("Enable memory consolidation in your user configuration first; projects may only disable it")
            if project and raw.get("memory", {}).get("recall", {}).get("mode") == "model" and not user_model_recall:
                raise ConfigError("Enable model memory recall in your user configuration first; projects cannot enable model requests")
            merged = _merge_raw(merged, raw)
        except (ConfigError, TypeError, KeyError) as exc:
            if isinstance(exc, ConfigError):
                raise ConfigError(f"{candidate}: {exc}") from exc
            raise ConfigError(f"Invalid configuration fields in {candidate}") from exc
        for key, value in raw.items():
            if isinstance(value, dict):
                for field_name in value:
                    sources[f"{key}.{field_name}"] = str(candidate)
            else:
                sources[key] = str(candidate)
        if project:
            project_payload.append({"path": str(candidate), "raw": project_raw})
        else:
            from .credentials import normalize_endpoint
            global_targets = {(p["protocol"], normalize_endpoint(p["base_url"])) for p in raw.get("providers", [])}
    if env_provider is not None or "providers" not in merged:
        from .environment import environment_connection
        try:
            profile, source = environment_connection(env_provider)
        except MissingConfigError:
            if not found:
                raise
            # Preserve validation of an existing, incomplete configuration.
        else:
            if any(p["name"] == profile["name"] for p in merged.get("providers", [])):
                raise ConfigError("Environment connection name conflicts with a file profile; rename the file profile")
            merged["providers"] = [*merged.get("providers", []), profile]
            merged["default_provider"] = profile["name"]
            if not found:
                merged["schema_version"] = 2
            sources[f"providers.{profile['name']}"] = source
            sources["default_provider"] = source
            global_targets.add((profile["protocol"], profile["base_url"]))
    try:
        config = _from_raw(merged)
    except ConfigError as exc:
        raise ConfigError(f"Effective configuration ({', '.join(str(p) for p in seen if p.exists())}): {exc}") from exc
    config.sources = sources
    from .permissions.rules import RuleEngine
    RuleEngine(
        user_rules_path=app_home() / "permissions.yaml",
        project_rules_path=cwd / ".nanocursor" / "permissions.yaml",
        local_rules_path=cwd / ".nanocursor" / "permissions.local.yaml",
    ).validate()
    import hashlib
    for name in ("permissions.yaml", "permissions.local.yaml"):
        rules = cwd / ".nanocursor" / name
        if rules.exists():
            try:
                digest = hashlib.sha256(rules.read_bytes()).hexdigest()
            except OSError as exc:
                raise ConfigError(f"Cannot read project permissions: {rules}") from exc
            project_payload.append({"path": str(rules), "sha256": digest})
    # Trust records bind to the exact project settings, including secret-source
    # names, before hooks, MCP, permission overrides or API probes can run.
    from .trust import fingerprint, is_trusted
    if project_payload:
        config.project_files = [p["path"] for p in project_payload]
        config.project_fingerprint = fingerprint(cwd, project_payload)
        config.project_trusted = is_trusted(cwd, config.project_fingerprint)
        from .credentials import normalize_endpoint
        for provider in config.providers:
            known = (provider.protocol, normalize_endpoint(provider.base_url)) in global_targets
            provider._needs_trust = not config.project_trusted and not known
    return config
