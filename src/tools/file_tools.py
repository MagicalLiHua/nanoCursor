"""
文件操作工具模块
支持文件读取、写入、编辑，以及文件备份和回滚功能。
新增 AST 感知的智能读取能力，避免大文件内容被压缩丢失。
"""

import ast
import difflib
import os
import shutil
import logging
from datetime import datetime
from typing import Optional, List
from langchain_core.tools import tool
from src.core.config import (
    WORKSPACE_DIR, LARGE_FILE_THRESHOLD, FUZZY_MATCH_THRESHOLD, MAX_FUZZY_MATCH_LINES
)
from src.core.logger import logger

# 备份目录
BACKUP_DIR = os.path.join(WORKSPACE_DIR, ".backups")
os.makedirs(BACKUP_DIR, exist_ok=True)

logger = logging.getLogger(__name__)


def _get_safe_filepath(filename: str) -> str:
    """
    将用户提供的相对路径转换为绝对路径，并严格校验其是否在 WORKSPACE_DIR 内部。
    如果发生目录穿越 (如 ../../)，将抛出 ValueError。
    使用 os.path.realpath 解析符号链接，防止通过软链接逃逸工作区。
    """
    workspace_abs = os.path.realpath(WORKSPACE_DIR)
    target_abs = os.path.realpath(os.path.join(workspace_abs, filename))

    if not target_abs.startswith(workspace_abs):
        raise ValueError(f"安全拦截：禁止访问工作区之外的路径 -> {filename}")

    return target_abs


def _get_backup_filepath(filename: str) -> str:
    """
    获取文件的备份路径。
    格式: .backups/{filename}.bak.{timestamp}
    """
    safe_name = filename.replace(os.sep, "_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(BACKUP_DIR, f"{safe_name}.bak.{timestamp}")


def backup_file(filename: str) -> Optional[str]:
    """
    备份指定文件到 .backups 目录。
    
    Args:
        filename: 要备份的文件相对路径
    
    Returns:
        备份文件路径，如果文件不存在则返回 None
    """
    try:
        filepath = _get_safe_filepath(filename)
    except ValueError as e:
        return str(e)

    if not os.path.exists(filepath):
        return None

    backup_path = _get_backup_filepath(filename)
    try:
        shutil.copy2(filepath, backup_path)
        logger.info(f"已备份文件 {filename} 到 {backup_path}")
        return backup_path
    except Exception as e:
        logger.error(f"备份文件 {filename} 失败: {e}")
        return None


def rollback_file(filename: str, backup_index: int = -1) -> str:
    """
    回滚文件到指定备份版本。
    
    Args:
        filename: 要回滚的文件相对路径
        backup_index: 备份索引，-1 表示最新备份，0 表示最旧备份
    
    Returns:
        回滚结果消息
    """
    safe_name = filename.replace(os.sep, "_")
    backup_pattern = f"{safe_name}.bak."
    
    try:
        # 获取所有备份文件
        backups = [
            f for f in os.listdir(BACKUP_DIR) 
            if f.startswith(backup_pattern)
        ]
        
        if not backups:
            return f"未找到文件 {filename} 的备份。"
        
        # 按备份时间排序
        backups.sort()
        selected_backup = backups[backup_index]
        backup_path = os.path.join(BACKUP_DIR, selected_backup)
        
        # 恢复文件
        filepath = _get_safe_filepath(filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        shutil.copy2(backup_path, filepath)
        
        logger.info(f"已回滚文件 {filename} 到备份 {selected_backup}")
        return f"成功回滚文件 {filename}，使用备份: {selected_backup}"
    
    except Exception as e:
        logger.error(f"回滚文件 {filename} 失败: {e}")
        return f"回滚失败: {str(e)}"


def list_backups(filename: Optional[str] = None) -> str:
    """
    列出所有备份文件。
    
    Args:
        filename: 可选，指定文件名只列出该文件的备份
    
    Returns:
        备份文件列表
    """
    try:
        backups = os.listdir(BACKUP_DIR)
        
        if filename:
            safe_name = filename.replace(os.sep, "_")
            backups = [b for b in backups if b.startswith(safe_name)]
        
        if not backups:
            return "没有备份文件。"
        
        backups.sort()
        result = f"找到的 {len(backups)} 个备份:\n"
        for i, b in enumerate(backups):
            backup_path = os.path.join(BACKUP_DIR, b)
            size = os.path.getsize(backup_path)
            result += f"  {i}: {b} ({size} bytes)\n"
        
        return result
    except Exception as e:
        return f"获取备份列表失败: {e}"


# ==========================================
# AST 辅助函数
# ==========================================

def _extract_ast_outline(filepath: str) -> str:
    """
    使用 ast 提取文件的函数/类大纲，返回结构化摘要。
    包含：函数名、参数名、起始行号、结束行号。
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except Exception as e:
        return f"(AST 解析失败: {e})"

    lines = source.splitlines()
    total_lines = len(lines)

    parts: List[str] = []
    _claimed_funcs: set = set()  # Track (func_name, lineno) claimed by classes

    # Handle only module-level nodes to avoid double-counting methods inside classes
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods_info = []
            for child in ast.walk(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child is not node:
                    key = (child.name, child.lineno)
                    _claimed_funcs.add(key)
                    methods_info.append(_format_function(child))
            class_info = f"class {node.name} (line {node.lineno}-{getattr(node, 'end_lineno', node.lineno)})"
            if methods_info:
                parts.append(f"{class_info}:\n    " + "\n    ".join(methods_info))
            else:
                parts.append(f"{class_info}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            key = (node.name, node.lineno)
            if key not in _claimed_funcs:
                parts.append(_format_function(node))

    if not parts:
        return "(无函数或类定义)"

    summary = f"[文件结构大纲] 共 {total_lines} 行\n"
    summary += "\n".join(parts)
    summary += "\n\n提示：使用 read_function 工具读取特定函数的完整代码，或使用 read_file_range 读取指定行范围。"
    return summary


def _format_function(node) -> str:
    """格式化一个函数定义为简短摘要"""
    args = []
    for arg in node.args.args:
        arg_name = arg.arg
        if arg_name == "self":
            continue
        args.append(arg_name)
    args_str = ", ".join(args)
    start = node.lineno
    end = getattr(node, "end_lineno", start)
    return f"def {node.name}({args_str}) (line {start}-{end})"


def _extract_function_source(filepath: str, function_name: str) -> str:
    """
    使用 ast 定位指定函数，返回其完整源码（带行号前缀）。
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except Exception as e:
        return f"(AST 解析失败: {e})"

    lines = source.splitlines()
    
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            start = node.lineno
            end = getattr(node, "end_lineno", start)
            func_lines = lines[start - 1:end]
            numbered = "\n".join(f"  {i + start - 1} | {line}" for i, line in enumerate(func_lines))
            return f"[函数 {function_name} 源码] (line {start}-{end})\n\n{numbered}"
    
    return f"未找到函数 '{function_name}'。请检查函数名是否正确，或使用 read_file 查看文件完整内容。"


def _extract_class_source(filepath: str, class_name: str) -> str:
    """
    使用 ast 定位指定类，返回其完整源码（带行号前缀）。
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except Exception as e:
        return f"(AST 解析失败: {e})"

    lines = source.splitlines()
    
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            start = node.lineno
            end = getattr(node, "end_lineno", start)
            class_lines = lines[start - 1:end]
            numbered = "\n".join(f"  {i + start - 1} | {line}" for i, line in enumerate(class_lines))
            return f"[类 {class_name} 源码] (line {start}-{end})\n\n{numbered}"
    
    return f"未找到类 '{class_name}'。请检查类名是否正确，或使用 read_file 查看文件完整内容。"


# ==========================================
# 工具定义 (Tools)
# ==========================================

@tool
def list_directory(path: str = ".") -> str:
    """
    列出指定目录中的文件和子目录。

    参数 (Args):
        path (str): 目录的相对路径，默认为工作区根目录 "."。

    返回 (Returns):
        str: 目录内容列表，包含文件/文件夹类型和名称。
    """
    try:
        filepath = _get_safe_filepath(path)
    except ValueError as e:
        return str(e)

    if not os.path.isdir(filepath):
        return f"Error: '{path}' 不是一个存在的目录。"

    entries = []
    try:
        for entry in sorted(os.listdir(filepath)):
            full_path = os.path.join(filepath, entry)
            is_dir = os.path.isdir(full_path)
            if not entry.startswith((".backups", ".snapshots")):
                prefix = "[DIR]  " if is_dir else "[FILE] "
                entries.append(f"  {prefix}{entry}")

        if not entries:
            return f"目录 '{path}' 为空。"

        return f"目录 '{path}' 的内容:\n" + "\n".join(entries)
    except Exception as e:
        return f"Error listing directory {path}: {str(e)}"


@tool
def read_file(filename: str) -> str:
    """
    读取现有文件的内容。
    - 对于小文件（< 5000 字符），返回完整内容
    - 对于大文件，返回 AST 解析的结构大纲，包含所有函数/类的名称和行号范围
      然后你可以使用 read_function 或 read_file_range 工具获取需要的具体代码段

    参数 (Args):
        filename (str): 要读取的目标文件的相对路径 (例如: "src/main.py")。

    返回 (Returns):
        str: 文件完整内容（小文件）或结构大纲（大文件）。
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
        logger.debug(f"读取文件: {filename} ({len(content)} 字符)")
        
        # 小文件直接返回完整内容
        if len(content) <= LARGE_FILE_THRESHOLD:
            return f"--- Content of {filename} ---\n{content}\n--- End of {filename} ---"
        
        # 大文件返回 AST 结构大纲
        outline = _extract_ast_outline(filepath)
        return f"--- Structure of {filename} ({len(content)} 字符, 大文件) ---\n{outline}\n--- End of {filename} ---"
    except Exception as e:
        return f"Error reading file {filename}: {str(e)}"


@tool
def read_function(filename: str, function_name: str) -> str:
    """
    使用 AST 解析，精确提取指定函数的完整源码。
    这是读取大文件特定函数内容的推荐方式。

    参数 (Args):
        filename (str): 目标文件的相对路径。
        function_name (str): 要提取的函数名称。

    返回 (Returns):
        str: 函数的完整源码，带行号前缀。
    """
    try:
        filepath = _get_safe_filepath(filename)
    except ValueError as e:
        return str(e)

    if not os.path.exists(filepath):
        return f"Error: File '{filename}' does not exist."

    return _extract_function_source(filepath, function_name)


@tool
def read_class(filename: str, class_name: str) -> str:
    """
    使用 AST 解析，精确提取指定类的完整源码。
    这是读取大文件特定类内容的推荐方式。

    参数 (Args):
        filename (str): 目标文件的相对路径。
        class_name (str): 要提取的类名称。

    返回 (Returns):
        str: 类的完整源码，带行号前缀。
    """
    try:
        filepath = _get_safe_filepath(filename)
    except ValueError as e:
        return str(e)

    if not os.path.exists(filepath):
        return f"Error: File '{filename}' does not exist."

    return _extract_class_source(filepath, class_name)


@tool
def read_file_range(filename: str, start_line: int, end_line: int) -> str:
    """
    读取文件的指定行范围内容。

    参数 (Args):
        filename (str): 目标文件的相对路径。
        start_line (int): 起始行号（1-based，包含）。
        end_line (int): 结束行号（1-based，包含）。

    返回 (Returns):
        str: 指定行范围的代码，带行号前缀。
    """
    try:
        filepath = _get_safe_filepath(filename)
    except ValueError as e:
        return str(e)

    if not os.path.exists(filepath):
        return f"Error: File '{filename}' does not exist."

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        total_lines = len(lines)
        # 校验行号范围
        if start_line < 1:
            return f"Error: start_line 必须 >= 1，当前值: {start_line}"
        if end_line > total_lines:
            return f"Error: end_line 超出文件范围。文件共 {total_lines} 行，请求结束行: {end_line}"
        if start_line > end_line:
            return f"Error: start_line ({start_line}) 不能大于 end_line ({end_line})"
        
        selected = lines[start_line - 1:end_line]
        numbered = "".join(f"  {i + start_line} | {line}" for i, line in enumerate(selected))
        return f"--- Lines {start_line}-{end_line} of {filename} ---\n{numbered}\n--- End ---"
    except Exception as e:
        return f"Error reading file range {filename}: {str(e)}"


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

    # 如果文件已存在，拒绝覆盖（应使用 edit_file 修改已有文件）
    if os.path.exists(filepath):
        return f"错误：文件 {filename} 已存在。write_file 仅用于创建新文件，请使用 edit_file 工具修改已有文件。"

    # 增强功能：如果大模型想在不存在的子目录创建文件，自动帮它创建目录
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info(f"写入文件: {filename} ({len(content)} 字符)")
        return f"Successfully created/updated file: {filename}"
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

        # 在修改前备份文件
        backup_path = backup_file(filename)

        new_content = None
        match_strategy = ""

        # 策略 1: 完美精确匹配
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
            # 性能保护：超过 MAX_FUZZY_MATCH_LINES 行的文件跳过模糊匹配
            if len(content_lines) > MAX_FUZZY_MATCH_LINES:
                return (
                    f"修改失败：{filename} 行数过多 ({len(content_lines)})，无法执行模糊匹配。\n"
                    f"请先使用 read_file_range 读取目标区域，再使用精确匹配的 search_block 重试。"
                )

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

            # 设定相似度阈值
            if best_ratio > FUZZY_MATCH_THRESHOLD:
                # 执行块替换，保留文件首尾空白不被 strip 破坏
                before_block = '\n'.join(content_lines[:best_start])
                after_block = '\n'.join(content_lines[best_end:])
                new_content = before_block + '\n' + replace_block + '\n' + after_block
                # 规范化连续多余空行 (4+ blank lines → 2)
                import re as _re
                new_content = _re.sub(r'\n{4,}', '\n\n\n', new_content)
                if not new_content.endswith('\n'):
                    new_content += '\n'
                match_strategy = f"模糊匹配 (Fuzzy Match, 相似度 {best_ratio:.1%})"
            else:
                return (
                    f"修改失败：未能在 {filename} 中找到指定的 `search_block`。\n"
                    f"最佳匹配相似度仅为 {best_ratio:.1%}，低于安全阈值(90%)。\n"
                    f"可能原因：你产生了文本幻觉，或者遗漏了重要注释。请先调用 read_file 重新确认文件内容。"
                )

        # 写入新内容
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_content)

        backup_info = f" (原文件已备份到 {os.path.basename(backup_path) if backup_path else '无'})" if backup_path else ""
        logger.info(f"修改文件: {filename} [{match_strategy}]{backup_info}")
        return f"成功修改 {filename}。使用策略: [{match_strategy}]。{backup_info}"
    except Exception as e:
        return f"修改文件 {filename} 时发生错误: {str(e)}"


@tool
def rollback_file_tool(filename: str, backup_index: int = -1) -> str:
    """
    回滚文件到指定备份版本。
    
    参数 (Args):
        filename (str): 要回滚的文件的相对路径。
        backup_index (int): 备份索引，-1 表示最新备份（默认），0 表示最旧备份。
    
    返回 (Returns):
        str: 回滚成功或失败的信息。
    """
    return rollback_file(filename, backup_index)


@tool
def list_backups_tool(filename: str = None) -> str:
    """
    列出所有备份文件。
    
    参数 (Args):
        filename (str, optional): 指定文件名只列出该文件的备份。
    
    返回 (Returns):
        str: 备份文件列表。
    """
    return list_backups(filename)


# 基础工具列表（用于 Agent 绑定）- 包含新的 AST 感知读取工具
tools = [write_file, edit_file, read_file, read_function, read_class, read_file_range]

# Planner 专属工具列表：只有读取类工具 + 目录浏览
planner_tools = [read_file, list_directory]

# 扩展工具列表（包含备份管理工具）
extended_tools = tools + [rollback_file_tool, list_backups_tool]