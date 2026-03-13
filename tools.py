import os
from langchain_core.tools import tool
from config import WORKSPACE_DIR

# ==========================================
# 工具定义 (Tools)
# ==========================================
from pydantic import BaseModel, Field

class WritePythonFileArgs(BaseModel):
    filename: str = Field(description="文件名，例如 factorial.py")
    code: str = Field(description="完整的 Python 代码内容，请注意转义换行符")


@tool(args_schema=WritePythonFileArgs)
def write_python_file(filename: str, code: str) -> str:
    """
    将 Python 代码写入到指定的文件中。
    对于 MVP 版本，请传入该文件的【全量最新代码】。

    参数:
    - filename: 文件名 (例如 'main.py' 或 'utils.py')
    - code: 完整的 Python 代码内容
    """
    # 🚨 安全壁垒：防止目录穿越攻击 (Directory Traversal)
    # 无论大模型传来什么路径 (如 ../../etc/passwd)，只取最后的文件名
    safe_filename = os.path.basename(filename)
    file_path = os.path.join(WORKSPACE_DIR, safe_filename)

    # 执行物理写入
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(code)
        return f"✅ 成功将代码写入工作区文件: {safe_filename}"
    except Exception as e:
        return f"❌ 写入文件失败: {str(e)}"

tools = [write_python_file]