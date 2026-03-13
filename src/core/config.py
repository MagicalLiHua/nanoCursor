import os

# ==========================================
# 环境沙盒
# ==========================================

# 定义安全的工作目录 (当前目录下的 workspace 文件夹)
WORKSPACE_DIR = os.path.join(os.getcwd(), "../../workspace")
os.makedirs(WORKSPACE_DIR, exist_ok=True)  # 如果不存在则自动创建