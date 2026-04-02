import os

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
print(f"[Config] 当前工作区路径已锁定为: {WORKSPACE_DIR}")

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
print("[Config] 上下文管理器 v2.0 已启用 (tiktoken 精确计数 + LLM 智能摘要 + 动态窗口)")
