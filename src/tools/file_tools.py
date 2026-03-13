import difflib
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
    读取现有文件的完整内容。
    【重要提醒】：在调用 edit_file 修改任何文件之前，你必须先使用此工具读取该文件！这能确保你后续提供的 search_block 与文件中的实际内容完全一致。

    参数 (Args):
        filename (str): 要读取的目标文件的相对路径 (例如: "src/main.py")。

    返回 (Returns):
        str: 包含该文件所有完整内容的字符串。如果文件不存在则返回错误提示。
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
    创建一个全新的文件并写入内容。
    【警告】：切勿使用此工具来修改已存在的文件！如果需要修改文件，请务必使用 edit_file 工具。

    参数 (Args):
        filename (str): 要创建的新文件的相对路径 (例如: "tests/test_new.py")。如果目录不存在，系统会自动创建。
        content (str): 要写入该新文件的完整代码或文本内容。

    返回 (Returns):
        str: 文件创建成功或失败的系统提示信息。
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
    通过替换指定的代码块来精准修改现有的文件。
    系统支持智能容错，但请尽量保证 search_block 与原文件内容一致。

    参数 (Args):
        filename (str): 要修改的现有文件的相对路径 (例如: "src/utils.py")。
        search_block (str): 原文件中需要被替换的具体代码块。必须从 read_file 的结果中一字不差地提取（连空格和换行都必须一样）。
        replace_block (str): 用于替换的新代码块。

    返回 (Returns):
        str: 替换成功或失败的系统提示信息。如果 search_block 未找到，会返回详细的失败原因。
    """
    try:
        filepath = _get_safe_filepath(filename)
    except ValueError as e:
        return str(e)

    if not os.path.exists(filepath):
        return f"错误：文件 {filename} 不存在。请先使用 write_file 创建它。"

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        new_content = None
        match_strategy = ""

        # 策略 1: 完美精确匹配 (最快，最安全)
        if search_block in content:
            new_content = content.replace(search_block, replace_block)
            match_strategy = "精确匹配 (Exact Match)"

        # 策略 2: 忽略首尾空白与换行符匹配
        elif search_block.strip() in content:
            new_content = content.replace(search_block.strip(), replace_block.strip())
            match_strategy = "首尾去空匹配 (Stripped Match)"

        # 策略 3: 基于 difflib 的模糊匹配 (解决大模型缩进/换行幻觉)
        else:
            content_lines = content.splitlines()
            search_lines = search_block.splitlines()

            # 过滤掉空行，寻找最高相似度的代码块
            best_ratio = 0
            best_start = -1
            best_end = -1
            search_len = len(search_lines)

            # 滑动窗口计算文本块相似度
            for i in range(len(content_lines) - search_len + 1):
                window = content_lines[i:i + search_len]
                # 将块拼起来计算相似度
                ratio = difflib.SequenceMatcher(None, '\n'.join(window), '\n'.join(search_lines)).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_start = i
                    best_end = i + search_len

            # 设定相似度阈值 (比如 80% 以上才认为是 LLM 想要修改的块)
            if best_ratio > 0.9:
                # 执行块替换
                before_block = '\n'.join(content_lines[:best_start])
                after_block = '\n'.join(content_lines[best_end:])
                # 重新拼接文件内容
                new_content = f"{before_block}\n{replace_block}\n{after_block}".strip() + "\n"
                match_strategy = f"模糊匹配 (Fuzzy Match, 相似度 {best_ratio:.1%})"
            else:
                return (
                    f"修改失败：未能在 {filename} 中找到指定的 `search_block`。\n"
                    f"最佳匹配相似度仅为 {best_ratio:.1%}，低于安全阈值(80%)。\n"
                    f"可能原因：你产生了文本幻觉，或者遗漏了重要注释。请先调用 read_file 重新确认文件内容。"
                )

        # 写入新内容
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_content)

        return f"成功修改 {filename}。使用策略: [{match_strategy}]。"
    except Exception as e:
        return f"修改文件 {filename} 时发生错误: {str(e)}"


@tool
def list_directory(sub_path: str = "") -> str:
    """
    列出工作区内指定目录的内容，用于探索项目结构，确认文件是否存在。

    参数 (Args):
        sub_path (str, optional): 相对于工作区根目录的路径 (例如 "src" 或 "tests")。如果想查看项目根目录，请留空传入 ""。默认值为 ""。

    返回 (Returns):
        str: 包含该目录下所有文件和子目录列表的字符串表示。
    """
    try:
        target_dir = _get_safe_filepath(sub_path)
    except ValueError as e:
        return str(e)

    if not os.path.exists(target_dir):
        return f"错误：目录 '{sub_path}' 不存在。"

    if not os.path.isdir(target_dir):
        return f"错误：'{sub_path}' 是一个文件，不是目录。请使用 read_file 工具读取它。"

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

        output = f"工作区目录 workspace/{sub_path} 的内容:\n" + "\n".join(dirs + files)
        if not dirs and not files:
            output += "(空目录)"

        return output
    except Exception as e:
        return f"列出目录时发生错误: {str(e)}"


# 注册所有工具
tools = [write_file, edit_file, list_directory, read_file]