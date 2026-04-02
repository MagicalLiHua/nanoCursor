"""
文件操作工具模块
支持文件读取、写入、编辑，以及文件备份和回滚功能。
"""

import difflib
import os
import shutil
import logging
from datetime import datetime
from typing import Optional
from langchain_core.tools import tool
from src.core.config import WORKSPACE_DIR
from src.core.logger import logger

# 备份目录
BACKUP_DIR = os.path.join(WORKSPACE_DIR, ".backups")
os.makedirs(BACKUP_DIR, exist_ok=True)

logger = logging.getLogger(__name__)


def _get_safe_filepath(filename: str) -> str:
    """
    将用户提供的相对路径转换为绝对路径，并严格校验其是否在 WORKSPACE_DIR 内部。
    如果发生目录穿越 (如 ../../)，将抛出 ValueError。
    """
    workspace_abs = os.path.abspath(WORKSPACE_DIR)
    target_abs = os.path.abspath(os.path.join(workspace_abs, filename))

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
        logger.debug(f"读取文件: {filename} ({len(content)} 字符)")
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

    # 如果文件已存在，先备份
    if os.path.exists(filepath):
        backup_path = backup_file(filename)
        if backup_path:
            logger.warning(f"write_file 覆盖了已存在的文件 {filename}，原文件已备份到 {backup_path}")

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


# 基础工具列表（用于 Agent 绑定）
tools = [write_file, edit_file, read_file]

# 扩展工具列表（包含备份管理工具）
extended_tools = tools + [rollback_file_tool, list_backups_tool]