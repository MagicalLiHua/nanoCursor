"""
全局配置模块
定义项目路径、沙盒参数、工具阈值等核心配置。
使用 Pydantic BaseModel 进行类型校验和范围验证。
"""

import logging
import os
from typing import Any

from pydantic import BaseModel, Field, field_validator

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
    LARGE_FILE_THRESHOLD: int = Field(default=5000, ge=100)
    FUZZY_MATCH_THRESHOLD: float = Field(default=0.9, ge=0.0, le=1.0)
    MAX_FUZZY_MATCH_LINES: int = Field(default=2000, ge=10)

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
    LLM_MAX_TOKENS: int = Field(default=4096, ge=256)
    LLM_TIMEOUT_SECONDS: int = Field(default=30, ge=5, le=120)

    # --- Context Management ---
    CONTEXT_MAX_TOKENS: int = Field(default=8000, ge=1024)
    CODER_KEEP_TURNS: int = Field(default=4, ge=1)
    PLANNER_KEEP_TURNS: int = Field(default=3, ge=1)
    REVIEWER_KEEP_TURNS: int = Field(default=2, ge=1)

    # --- Concurrency ---
    MAX_CONCURRENT_RUNS: int = Field(default=5, ge=1, le=50)

    @field_validator("*", mode="before")
    @classmethod
    def _strip_env_strings(cls, v: Any, info) -> Any:
        """将字符串类型的 env var 去除首尾空白"""
        if isinstance(v, str):
            return v.strip()
        return v


def _safe_int(val: str | None, default: int, min_val: int, max_val: int) -> int:
    """安全地将 env var 转换为 int，超出范围时使用默认值并报警"""
    if val is None:
        return default
    try:
        v = int(val)
        if v < min_val or v > max_val:
            logger.warning(f"{val} 超出范围 [{min_val},{max_val}]，使用默认值 {default}")
            return default
        return v
    except ValueError:
        logger.warning(f"无效的整数值 {val!r}，使用默认值 {default}")
        return default


def _safe_float(val: str | None, default: float, min_val: float, max_val: float) -> float:
    """安全地将 env var 转换为 float，超出范围时使用默认值并报警"""
    if val is None:
        return default
    try:
        v = float(val)
        if v < min_val or v > max_val:
            logger.warning(f"{val} 超出范围 [{min_val},{max_val}]，使用默认值 {default}")
            return default
        return v
    except ValueError:
        logger.warning(f"无效的浮点数值 {val!r}，使用默认值 {default}")
        return default


# ==========================================
# 从环境变量加载（带类型安全的 fallback）
# ==========================================

try:
    _raw_config = {
        "SANDBOX_IMAGE": os.getenv("SANDBOX_IMAGE", "python:3.10-slim"),
        "SANDBOX_MEM_LIMIT": os.getenv("SANDBOX_MEM_LIMIT", "256m"),
        "SANDBOX_TIMEOUT_SECONDS": _safe_int(os.getenv("SANDBOX_TIMEOUT_SECONDS"), 60, 1, 600),
        "SANDBOX_CONTAINER_STARTUP_TIMEOUT": 5,
        "SANDBOX_CPU_QUOTA_PERCENT": _safe_int(os.getenv("SANDBOX_CPU_QUOTA_PERCENT"), 50, 1, 100),
        "LARGE_FILE_THRESHOLD": _safe_int(os.getenv("LARGE_FILE_THRESHOLD"), 5000, 100, 1000000),
        "FUZZY_MATCH_THRESHOLD": _safe_float(os.getenv("FUZZY_MATCH_THRESHOLD"), 0.9, 0.0, 1.0),
        "MAX_FUZZY_MATCH_LINES": _safe_int(os.getenv("MAX_FUZZY_MATCH_LINES"), 2000, 10, 100000),
        "MAX_CODER_STEPS": _safe_int(os.getenv("MAX_CODER_STEPS"), 15, 1, 100),
        "MAX_PLANNER_STEPS": _safe_int(os.getenv("MAX_PLANNER_STEPS"), 10, 1, 50),
        "MAX_TASKS": _safe_int(os.getenv("MAX_TASKS"), 50, 1, 500),
        "SUPERVISOR_MODEL": os.getenv("SUPERVISOR_MODEL"),
        "SUPERVISOR_PROVIDER": os.getenv("SUPERVISOR_PROVIDER"),
        "SUPERVISOR_TEMPERATURE": _safe_float(os.getenv("SUPERVISOR_TEMPERATURE"), 0.1, 0.0, 1.0),
        "MAX_STEPS": _safe_int(os.getenv("MAX_STEPS"), 100, 10, 1000),
        "LLM_TEMPERATURE": _safe_float(os.getenv("LLM_TEMPERATURE"), 0.2, 0.0, 2.0),
        "LLM_MAX_TOKENS": _safe_int(os.getenv("LLM_MAX_TOKENS"), 4096, 256, 128000),
        "LLM_TIMEOUT_SECONDS": _safe_int(os.getenv("LLM_TIMEOUT_SECONDS"), 30, 5, 120),
        "CONTEXT_MAX_TOKENS": _safe_int(os.getenv("CONTEXT_MAX_TOKENS"), 8000, 1024, 256000),
        "CODER_KEEP_TURNS": _safe_int(os.getenv("CODER_KEEP_TURNS"), 4, 1, 20),
        "PLANNER_KEEP_TURNS": _safe_int(os.getenv("PLANNER_KEEP_TURNS"), 3, 1, 20),
        "REVIEWER_KEEP_TURNS": _safe_int(os.getenv("REVIEWER_KEEP_TURNS"), 2, 1, 20),
        "MAX_CONCURRENT_RUNS": _safe_int(os.getenv("MAX_CONCURRENT_RUNS"), 5, 1, 50),
    }
    config = AppConfig(**_raw_config)
    # 导出为模块级属性（向后兼容）
    SANDBOX_IMAGE = config.SANDBOX_IMAGE
    SANDBOX_MEM_LIMIT = config.SANDBOX_MEM_LIMIT
    SANDBOX_TIMEOUT_SECONDS = config.SANDBOX_TIMEOUT_SECONDS
    SANDBOX_CONTAINER_STARTUP_TIMEOUT = config.SANDBOX_CONTAINER_STARTUP_TIMEOUT
    SANDBOX_CPU_QUOTA_PERCENT = config.SANDBOX_CPU_QUOTA_PERCENT
    LARGE_FILE_THRESHOLD = config.LARGE_FILE_THRESHOLD
    FUZZY_MATCH_THRESHOLD = config.FUZZY_MATCH_THRESHOLD
    MAX_FUZZY_MATCH_LINES = config.MAX_FUZZY_MATCH_LINES
    MAX_CODER_STEPS = config.MAX_CODER_STEPS
    MAX_PLANNER_STEPS = config.MAX_PLANNER_STEPS
    MAX_TASKS = config.MAX_TASKS
    SUPERVISOR_MODEL = config.SUPERVISOR_MODEL
    SUPERVISOR_PROVIDER = config.SUPERVISOR_PROVIDER
    SUPERVISOR_TEMPERATURE = config.SUPERVISOR_TEMPERATURE
    MAX_STEPS = config.MAX_STEPS
    LLM_TEMPERATURE = config.LLM_TEMPERATURE
    LLM_MAX_TOKENS = config.LLM_MAX_TOKENS
    LLM_TIMEOUT_SECONDS = config.LLM_TIMEOUT_SECONDS
    CONTEXT_MAX_TOKENS = config.CONTEXT_MAX_TOKENS
    CODER_KEEP_TURNS = config.CODER_KEEP_TURNS
    PLANNER_KEEP_TURNS = config.PLANNER_KEEP_TURNS
    REVIEWER_KEEP_TURNS = config.REVIEWER_KEEP_TURNS
    MAX_CONCURRENT_RUNS = config.MAX_CONCURRENT_RUNS
except Exception as e:
    logger.error(f"配置加载失败: {e}，使用硬编码默认值")
    # 硬编码默认值（确保应用仍可启动）
    SANDBOX_IMAGE = "python:3.10-slim"
    SANDBOX_MEM_LIMIT = "256m"
    SANDBOX_TIMEOUT_SECONDS = 60
    SANDBOX_CONTAINER_STARTUP_TIMEOUT = 5
    SANDBOX_CPU_QUOTA_PERCENT = 50
    LARGE_FILE_THRESHOLD = 5000
    FUZZY_MATCH_THRESHOLD = 0.9
    MAX_FUZZY_MATCH_LINES = 2000
    MAX_CODER_STEPS = 15
    MAX_PLANNER_STEPS = 10
    MAX_TASKS = 50
    SUPERVISOR_MODEL = None
    SUPERVISOR_PROVIDER = None
    SUPERVISOR_TEMPERATURE = 0.1
    MAX_STEPS = 100
    LLM_TEMPERATURE = 0.2
    LLM_MAX_TOKENS = 4096
    LLM_TIMEOUT_SECONDS = 30
    CONTEXT_MAX_TOKENS = 8000
    CODER_KEEP_TURNS = 4
    PLANNER_KEEP_TURNS = 3
    REVIEWER_KEEP_TURNS = 2
    MAX_CONCURRENT_RUNS = 5

logger.info("上下文管理器 v2.0 已启用 (tiktoken 精确计数 + LLM 智能摘要 + 动态窗口)")
