import os
from langchain_core.tools import tool
from src.core.config import WORKSPACE_DIR


# ==========================================
# 🛡️ 核心安全守卫 (Path Security Guard)
# ==========================================
def _get_safe_filepath(filename: str) -> str:
    """
    将用户提供的相对路径转换为绝对路径，并严格校验其是否在 WORKSPACE_DIR 内部。
    如果发生目录穿越 (如 ../../)，将抛出 ValueError。
    """
    # 获取工作区的绝对路径
    workspace_abs = os.path.abspath(WORKSPACE_DIR)

    # 拼接并获取目标文件的绝对路径
    target_abs = os.path.abspath(os.path.join(workspace_abs, filename))

    # 🚨 核心校验：如果目标路径不是以工作区路径开头，说明越界了！
    if not target_abs.startswith(workspace_abs):
        raise ValueError(f"安全拦截：禁止访问工作区之外的路径 -> {filename}")

    return target_abs


# ==========================================
# 工具定义 (Tools)
# ==========================================

@tool
def read_file(filename: str) -> str:
    """
    READ the exact content of an existing file.
    Always use this BEFORE editing a file to ensure your search_block matches the file's content perfectly.
    """
    try:
        filepath = _get_safe_filepath(filename)
    except ValueError as e:
        return str(e)

    if not os.path.exists(filepath):
        return f"Error: File '{filename}' does not exist. Cannot read."

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        return f"--- Content of {filename} ---\n{content}\n--- End of {filename} ---"
    except Exception as e:
        return f"Error reading file {filename}: {str(e)}"


@tool
def write_file(filename: str, content: str) -> str:
    """
    Only use this to CREATE a completely new file. Do not use this to modify existing files.
    It will automatically create missing subdirectories if needed (e.g., 'src/new_module.py').
    """
    try:
        filepath = _get_safe_filepath(filename)
    except ValueError as e:
        return str(e)

    # 💡 增强功能：如果大模型想在不存在的子目录创建文件，自动帮它创建目录
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully created new file: {filename}"
    except Exception as e:
        return f"Error writing file {filename}: {str(e)}"


@tool
def edit_file(filename: str, search_block: str, replace_block: str) -> str:
    """
    MODIFY an existing file by replacing a specific block of code.
    search_block MUST match the file's content perfectly (including indentation and line breaks).
    """
    try:
        filepath = _get_safe_filepath(filename)
    except ValueError as e:
        return str(e)

    if not os.path.exists(filepath):
        return f"Error: File {filename} does not exist. Use write_file to create it first."

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        # 增加容错率
        if search_block not in content:
            if search_block.strip() in content:
                search_block = search_block.strip()
            else:
                return (
                    f"Error: `search_block` not found in {filename}.\n"
                    f"Make sure you copy the exact lines from the file, including all indentation."
                )

        new_content = content.replace(search_block, replace_block)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_content)

        return f"Successfully edited {filename}. Replaced the requested block."
    except Exception as e:
        return f"Error editing file {filename}: {str(e)}"


@tool
def list_directory(sub_path: str = "") -> str:
    """
    List the contents of a directory within the workspace.
    Args:
        sub_path: The relative path to the directory (e.g., "src" or "tests"). Pass "" for root workspace.
    """
    try:
        target_dir = _get_safe_filepath(sub_path)
    except ValueError as e:
        return str(e)

    if not os.path.exists(target_dir):
        return f"Error: Directory '{sub_path}' does not exist."

    if not os.path.isdir(target_dir):
        return f"Error: '{sub_path}' is a file, not a directory. Use read_file instead."

    try:
        entries = os.listdir(target_dir)
        ignore_list = ['.git', '__pycache__', '.idea', 'venv', 'node_modules', '.DS_Store']
        valid_entries = [e for e in entries if e not in ignore_list]

        dirs, files = [], []
        for entry in valid_entries:
            if os.path.isdir(os.path.join(target_dir, entry)):
                dirs.append(f"📁 {entry}/")
            else:
                files.append(f"📄 {entry}")

        dirs.sort()
        files.sort()

        output = f"Contents of workspace/{sub_path}:\n" + "\n".join(dirs + files)
        if not dirs and not files:
            output += "(Empty directory)"

        return output
    except Exception as e:
        return f"Error listing directory: {str(e)}"


# 注册所有工具
tools = [write_file, edit_file, list_directory, read_file]