"""
全局配置模块
定义项目路径、沙盒参数、工具阈值等核心配置。
使用 Pydantic BaseModel 进行类型校验和范围验证。
"""

import logging
import os
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

# ==========================================
# 环境沙盒
# ==========================================

# 1. 获取当前 config.py 文件的绝对路径
_current_file = os.path.abspath(__file__)

# 2. 向上推算项目根目录
# config.py 在 src/infra/ 下，所以向上退两级就是项目根目录
_core_dir = os.path.dirname(_current_file)
_src_dir = os.path.dirname(_core_dir)
PROJECT_ROOT = os.path.dirname(_src_dir)

# 3. 运行数据和默认用户工作区分离
# - PROJECT_ROOT 是 nanoCursor 源码目录。
# - RUNTIME_ROOT 存放 nanoCursor 自身运行状态，默认被 git 忽略。
# - WORKSPACE_ROOT 存放默认/临时用户项目工作区。
# - 用户真正打开项目时，会通过 API 显式切换 WORKSPACE_DIR。
RUNTIME_ROOT = os.path.join(PROJECT_ROOT, ".nanocursor")
WORKSPACE_ROOT = os.path.abspath(os.path.expanduser(
    os.getenv("NANOCURSOR_WORKSPACE_ROOT", os.path.join(RUNTIME_ROOT, "workspaces"))
))
DEFAULT_WORKSPACE_DIR = os.path.abspath(os.path.expanduser(
    os.getenv("NANOCURSOR_WORKSPACE_DIR", os.path.join(WORKSPACE_ROOT, "default"))
))
WORKSPACE_DIR = DEFAULT_WORKSPACE_DIR


def _resolve_workspace_dir() -> str:
    """可被子类覆盖的工作区路径解析（API server 使用）"""
    return WORKSPACE_DIR


# 确保文件夹存在（模块级，失败时记录但不阻塞）
try:
    os.makedirs(RUNTIME_ROOT, exist_ok=True)
    os.makedirs(WORKSPACE_ROOT, exist_ok=True)
    os.makedirs(WORKSPACE_DIR, exist_ok=True)
except OSError as e:
    logger.warning(f"无法创建 WORKSPACE_DIR ({WORKSPACE_DIR}): {e}")

logger.info(f"当前工作区路径已锁定为: {WORKSPACE_DIR}")


# ==========================================
# Pydantic 配置模型
# ==========================================

class AppConfig(BaseModel):
    """所有可配置项的 Pydantic 模型，提供类型校验和范围验证"""

    # --- Sandbox ---
    SANDBOX_IMAGE: str = Field(default="python:3.10-slim")
    SANDBOX_MEM_LIMIT: str = Field(default="256m")
    SANDBOX_TIMEOUT_SECONDS: int = Field(default=60, ge=1, le=600)
    SANDBOX_CONTAINER_STARTUP_TIMEOUT: int = Field(default=5, ge=1, le=30)
    SANDBOX_CPU_QUOTA_PERCENT: int = Field(default=50, ge=1, le=100)

    # --- File Tools ---
    LARGE_FILE_THRESHOLD: int = Field(default=5000, ge=100, le=1_000_000)
    FUZZY_MATCH_THRESHOLD: float = Field(default=0.9, ge=0.0, le=1.0)
    MAX_FUZZY_MATCH_LINES: int = Field(default=2000, ge=10, le=100_000)

    # --- Agent Steps ---
    MAX_CODER_STEPS: int = Field(default=15, ge=1, le=100)
    MAX_PLANNER_STEPS: int = Field(default=10, ge=1, le=50)

    # --- Supervisor Architecture (Direction B) ---
    MAX_TASKS: int = Field(default=50, ge=1, le=500)
    SUPERVISOR_MODEL: str | None = Field(None)
    SUPERVISOR_PROVIDER: str | None = Field(None)
    SUPERVISOR_TEMPERATURE: float = Field(default=0.1, ge=0.0, le=1.0)
    MAX_STEPS: int = Field(default=100, ge=10, le=1000)

    # --- LLM ---
    LLM_TEMPERATURE: float = Field(default=0.2, ge=0.0, le=2.0)
    LLM_MAX_TOKENS: int = Field(default=4096, ge=256, le=128_000)
    LLM_TIMEOUT_SECONDS: int = Field(default=30, ge=5, le=120)

    # --- Context Management ---
    CONTEXT_MAX_TOKENS: int = Field(default=8000, ge=1024, le=256_000)
    CODER_KEEP_TURNS: int = Field(default=4, ge=1, le=20)
    PLANNER_KEEP_TURNS: int = Field(default=3, ge=1, le=20)
    REVIEWER_KEEP_TURNS: int = Field(default=2, ge=1, le=20)

    # --- Concurrency ---
    MAX_CONCURRENT_RUNS: int = Field(default=5, ge=1, le=50)

    @field_validator("*", mode="before")
    @classmethod
    def _strip_env_strings(cls, v: Any, info) -> Any:
        """将字符串类型的 env var 去除首尾空白"""
        if isinstance(v, str):
            return v.strip()
        return v


def load_app_config(environ: Mapping[str, str] | None = None) -> AppConfig:
    """Load environment overrides independently, falling back per invalid field."""
    env = os.environ if environ is None else environ
    values = AppConfig().model_dump()
    for field_name in AppConfig.model_fields:
        if field_name not in env:
            continue
        try:
            candidate = AppConfig.model_validate({**values, field_name: env[field_name]})
        except ValidationError as exc:
            logger.warning(
                "配置项 %s=%r 无效，保留默认值 %r：%s",
                field_name,
                env[field_name],
                values[field_name],
                exc.errors()[0]["msg"],
            )
            continue
        values[field_name] = getattr(candidate, field_name)
    return AppConfig.model_validate(values)


config = load_app_config()
globals().update(config.model_dump())

logger.info("上下文管理器 v2.0 已启用 (tiktoken 精确计数 + LLM 智能摘要 + 动态窗口)")
