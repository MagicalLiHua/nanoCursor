"""Compatibility re-export for runtime feature flags."""

from src.runtime.runtime_feature_flags import (  # noqa: F401
    env_flag,
    go_filetools_addr,
    go_filetools_enabled,
    go_filetools_fallback_enabled,
    go_executor_addr,
    go_executor_enabled,
    go_executor_fallback_enabled,
    go_indexer_addr,
    go_indexer_enabled,
    go_indexer_fallback_enabled,
    go_indexer_failure_cooldown_seconds,
    go_runtime_enabled,
    go_runtime_fallback_enabled,
    go_runtime_timeout_ms,
    go_runtime_url,
)
