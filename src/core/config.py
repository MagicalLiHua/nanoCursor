"""
全局配置模块
定义项目路径、沙盒参数、工具阈值等核心配置。
"""

import os
import logging

logger = logging.getLogger(__name__)

# ==========================================
# 环境沙盒
# ==========================================

# 1. 获取当前 config.py 文件的绝对路径
_current_file = os.path.abspath(__file__)

# 2. 向上推算项目根目录
# config.py 在 src/core/ 下，所以向上退两级就是项目根目录
_core_dir = os.path.dirname(_current_file)
_src_dir = os.path.dirname(_core_dir)
PROJECT_ROOT = os.path.dirname(_src_dir)

# 3. 稳妥地在项目根目录下定义 workspace
WORKSPACE_DIR = os.path.join(PROJECT_ROOT, "workspace")

# 确保文件夹存在
os.makedirs(WORKSPACE_DIR, exist_ok=True)

# 打印一下，方便你启动时核对路径是否正确（可自行注释掉）
logger.info(f"当前工作区路径已锁定为: {WORKSPACE_DIR}")

# ==========================================
# 沙盒与 Docker 配置
# ==========================================

SANDBOX_IMAGE = os.getenv("SANDBOX_IMAGE", "python:3.10-slim")
SANDBOX_MEM_LIMIT = os.getenv("SANDBOX_MEM_LIMIT", "256m")
SANDBOX_TIMEOUT_SECONDS = int(os.getenv("SANDBOX_TIMEOUT_SECONDS", "60"))
SANDBOX_CONTAINER_STARTUP_TIMEOUT = 5  # container.stop() 等待秒数

# ==========================================
# 文件工具阈值
# ==========================================

LARGE_FILE_THRESHOLD: int = int(os.getenv("LARGE_FILE_THRESHOLD", "5000"))
"""超过此字符数的文件将被视为大文件，read_file 返回 AST 大纲而非全文。"""

FUZZY_MATCH_THRESHOLD: float = float(os.getenv("FUZZY_MATCH_THRESHOLD", "0.9"))
"""edit_file 模糊匹配的最低相似度阈值 (0.0 ~ 1.0)。"""

MAX_FUZZY_MATCH_LINES: int = int(os.getenv("MAX_FUZZY_MATCH_LINES", "2000"))
"""超过此行数的文件将跳过 fuzzy matching，避免 O(N*M) 性能问题。"""

# ==========================================
# Coder 步数控制
# ==========================================

MAX_CODER_STEPS: int = int(os.getenv("MAX_CODER_STEPS", "15"))
"""Coder 单次微循环最大工具调用步数。"""

MAX_PLANNER_STEPS: int = int(os.getenv("MAX_PLANNER_STEPS", "10"))
"""Planner 最大工具调用步数，防止无限探索。"""

# ==========================================
# 🌟 上下文管理配置 (v2.0)
# ==========================================
# 采用分层上下文管理策略，配置项已迁移到 context_manager.py 的 DEFAULT_CONFIG
# 主要配置项：
#   - max_context_tokens: 上下文最大 Token 数 (默认 8000)
#   - coder_keep_turns: Coder 保留的对话轮数 (默认 4)
#   - planner_keep_turns: Planner 保留的对话轮数 (默认 3)
#   - reviewer_keep_turns: Reviewer 保留的对话轮数 (默认 2)
#
# Token 计数使用 tiktoken (cl100k_base encoding)，如果 tiktoken 不可用则回退到估算模式
logger.info("上下文管理器 v2.0 已启用 (tiktoken 精确计数 + LLM 智能摘要 + 动态窗口)")