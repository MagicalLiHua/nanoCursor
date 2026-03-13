import os
from langchain_core.tools import tool
from src.core.config import WORKSPACE_DIR

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


@tool
def write_file(filename: str, content: str) -> str:
    """Only use this to CREATE a completely new file. Do not use this to modify existing files."""
    filepath = os.path.join(WORKSPACE_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return f"Successfully created new file {filename}."


@tool
def edit_file(filename: str, search_block: str, replace_block: str) -> str:
    """
    MODIFY an existing file by replacing a specific block of code.

    Args:
        filename: The name of the file to edit.
        search_block: The EXACT code block you want to replace. Must match the file's content perfectly (including indentation and line breaks).
        replace_block: The new code block to insert in place of search_block.
    """
    filepath = os.path.join(WORKSPACE_DIR, filename)

    if not os.path.exists(filepath):
        return f"Error: File {filename} does not exist. Use write_file to create it first."

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # 核心痛点防范：大模型有时会在 search_block 找不到对应内容（比如多加了空格）
    if search_block not in content:
        # 尝试去掉首尾的换行符再找一次（增加容错率）
        if search_block.strip() in content:
            search_block = search_block.strip()
        else:
            # 找不到时，返回明确的报错，让 Reviewer 或 Coder 知道定位失败，重试
            return (
                f"Error: `search_block` not found in {filename}.\n"
                f"Make sure you copy the exact lines from the file, including all indentation."
            )

    # 执行替换
    new_content = content.replace(search_block, replace_block)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(new_content)

    return f"Successfully edited {filename}. Replaced the requested block."


tools = [write_file, edit_file]


# tools = [write_python_file]