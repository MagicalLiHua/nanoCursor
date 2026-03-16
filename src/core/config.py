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

WINDOW_SIZE = 10  # 上下文窗口大小，Coder 节点会使用这个值来裁剪消息历史
print(f"[Config] 上下文窗口大小设置为: {WINDOW_SIZE} 条消息")