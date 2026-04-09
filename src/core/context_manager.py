"""
上下文管理器 (Context Manager) - v2.0
负责为不同的 Agent 构建最优化的上下文，避免 Token 浪费和上下文丢失问题。

分层策略：
- Layer 1: 核心记忆 (永久保留) - 用户需求、当前计划、报错信息
- Layer 2: 工作记忆 (动态窗口) - 最近 N 轮对话
- Layer 3: 参考记忆 (LLM 驱动摘要压缩) - 历史对话的结构化摘要

改进点：
1. 引入 tiktoken 精确 Token 计数
2. 激活 file_signatures 机制
3. LLM 驱动的智能记忆压缩
4. 动态上下文窗口大小
5. 上下文槽位优先级队列
"""

import re
import logging
from typing import List, Optional, Tuple
from langchain_core.messages import (
    BaseMessage,
    SystemMessage,
    HumanMessage,
    AIMessage,
    ToolMessage,
)

logger = logging.getLogger(__name__)

# ==========================================
# Token 估算工具 (v2.0: tiktoken 精确计数)
# ==========================================

# 全局缓存，避免重复加载
_encoding_cache = {}


def get_encoding(model_name: str = "cl100k_base"):
    """获取 tiktoken encoder，带缓存机制"""
    if model_name not in _encoding_cache:
        try:
            import tiktoken
            _encoding_cache[model_name] = tiktoken.get_encoding(model_name)
        except ImportError:
            logger.warning("tiktoken 未安装，回退到估算模式。运行: pip install tiktoken")
            _encoding_cache[model_name] = None
        except Exception as e:
            logger.warning(f"加载 tiktoken 失败: {e}，回退到估算模式")
            _encoding_cache[model_name] = None
    return _encoding_cache[model_name]


def estimate_token_count(text: str, model_name: str = "cl100k_base") -> int:
    """
    精确计算文本的 Token 数量（使用 tiktoken）。
    如果 tiktoken 不可用，回退到快速估算。
    
    Args:
        text: 要计算的文本
        model_name: tiktoken encoding 名称
            - cl100k_base: GPT-4, GPT-3.5-Turbo, Claude
                    
    Returns:
        token 数量
    """
    if not text:
        return 0

    encoder = get_encoding(model_name)
    if encoder is not None:
        try:
            return len(encoder.encode(text))
        except Exception:
            pass  # 如果编码失败，回退到估算

    # Fallback: 简单估算
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    total_chars = len(text)
    english_chars = total_chars - chinese_chars
    return max(int(chinese_chars / 1.5 + english_chars / 4), 1)


def estimate_messages_tokens(messages: List[BaseMessage]) -> int:
    """估算一组消息的总 Token 数（包含消息角色和工具调用开销）"""
    total = 0
    for msg in messages:
        content = msg.content if hasattr(msg, 'content') else str(msg)
        total += estimate_token_count(content)
        # 消息角色和格式开销
        total += 5
        # 工具调用额外开销
        if hasattr(msg, 'tool_calls') and msg.tool_calls:
            total += 50  # 工具调用结构开销
            for tc in msg.tool_calls:
                total += estimate_token_count(str(tc.get("args", "")))
    return total


# ==========================================
# 核心记忆提取
# ==========================================

def extract_original_request(messages: List[BaseMessage]) -> str:
    """从历史消息中提取用户最初的需求"""
    for msg in messages:
        if isinstance(msg, HumanMessage) or msg.type in ("human", "user"):
            return msg.content[:1000]  # 稍微增加长度限制
    return "未知需求"


def extract_file_signatures(messages: List[BaseMessage]) -> dict[str, str]:
    """
    从工具调用结果中提取文件签名。
    返回 {文件名: 摘要信息} 的字典。
    
    改进：提取更详细的函数/类签名，包含参数信息
    """
    signatures = {}
    for msg in messages:
        if isinstance(msg, ToolMessage) and msg.name == "read_file":
            content = msg.content
            # 从文件名提取路径
            filename_match = re.search(r'Content of (.+?) ---', content)
            if not filename_match:
                continue
            filename = filename_match.group(1).strip()
            
            # 提取更详细的信息
            # 函数定义 (带参数)
            functions = re.findall(r'def\s+(\w+)\s*\(([^)]*)\)', content[:5000])
            # 类定义 (带基类)
            classes = re.findall(r'class\s+(\w+)(?:\(([^)]*)\))?', content[:5000])
            
            sig_parts = []
            for func_name, params in functions[:10]:
                param_str = params.strip()[:50]  # 截断过长的参数列表
                sig_parts.append(f"def {func_name}({param_str})")
            for cls_name, bases in classes[:5]:
                base_str = f"({bases.strip()})" if bases else ""
                sig_parts.append(f"class {cls_name}{base_str}")
            
            if sig_parts:
                signatures[filename] = " | ".join(sig_parts)
    
    return signatures


# ==========================================
# 上下文压缩 (v2.0: LLM 驱动的智能压缩)
# ==========================================

def filter_orphan_tool_messages(messages: List[BaseMessage]) -> List[BaseMessage]:
    """
    移除孤立的 ToolMessage。

    OpenAI 兼容的 API 要求：每条 ToolMessage 前面必须紧跟着一个包含 tool_calls 的
    Assistant 消息。如果消息序列的开头是 ToolMessage，或者 ToolMessage 前面不是
    tool_calls 消息，API 会返回 400 错误。
    """
    filtered = []
    for i, msg in enumerate(messages):
        if isinstance(msg, ToolMessage):
            # 检查前一条消息是否是包含 tool_calls 的 AIMessage
            if not filtered:
                continue  # 第一条消息就是 ToolMessage，跳过
            prev = filtered[-1]
            if not (isinstance(prev, AIMessage) and getattr(prev, 'tool_calls', [])):
                continue  # 前一条不是 tool_calls 消息，跳过
        filtered.append(msg)
    return filtered


def compress_tool_messages(messages: List[BaseMessage], max_content_length: int = 10000) -> List[BaseMessage]:
    """
    压缩工具消息，保留头尾内容，避免超长文件内容挤占上下文。
    
    注意：由于 read_file 工具已对大文件返回 AST 结构大纲而非完整内容，
    实际的工具消息一般不会超过此阈值。此函数主要作为安全保护机制。
    
    Args:
        messages: 原始消息列表
        max_content_length: 单条消息内容的最大长度（默认 10000，相比之前的 1500 大幅提升）
    
    Returns:
        压缩后的消息列表
    """
    compressed = []
    for msg in messages:
        if isinstance(msg, ToolMessage):
            content = msg.content
            if len(content) > max_content_length * 2:
                head = content[:max_content_length]
                tail = content[-max_content_length:]
                truncated_len = len(content) - max_content_length * 2
                new_content = (
                    f"{head}\n\n"
                    f"... [中间 {truncated_len} 字符已压缩] ...\n\n"
                    f"{tail}"
                )
                msg = ToolMessage(
                    content=new_content,
                    name=msg.name,
                    tool_call_id=msg.tool_call_id
                )
        compressed.append(msg)
    return compressed


def build_edit_summary(messages: List[BaseMessage], max_entries: int = 5) -> str:
    """
    生成文件编辑的结构化摘要，替代冗长的 ToolMessage 内容。
    
    Args:
        messages: 历史消息
        max_entries: 最大条目数
    
    Returns:
        编辑摘要文本
    """
    edits = []
    for msg in messages:
        if isinstance(msg, ToolMessage) and msg.name in ("edit_file", "write_file"):
            content = msg.content[:150]
            edits.append(f"- {msg.name}: {content}")
    
    if not edits:
        return "无文件修改记录。"
    
    # 只保留最近的几条
    recent_edits = edits[-max_entries:]
    return "\n".join(recent_edits)


def summarize_conversation(
    old_messages: List[BaseMessage],
    llm=None,
    max_summary_length: int = 500
) -> str:
    """
    使用 LLM 对旧对话进行智能摘要。
    
    Args:
        old_messages: 需要压缩的旧消息列表
        llm: LLM 实例，用于生成摘要。如果为 None，使用基于规则的摘要。
        max_summary_length: 摘要最大长度
    
    Returns:
        结构化的对话摘要文本
    """
    if not old_messages:
        return ""

    # 如果提供了 LLM，尝试使用它生成智能摘要
    if llm is not None:
        try:
            # 提取关键对话内容
            conversation_text = _format_for_llm_summary(old_messages)
            if len(conversation_text) < 200:  # 太短不需要 LLM
                return _rule_based_summary(old_messages)
            
            summary_prompt = f"""请将以下对话压缩为结构化摘要。只保留关键技术决策和代码修改信息，忽略寒暄和重复内容。

格式要求：
1. 【用户需求】一句话概括原始需求
2. 【已完成的修改】列出所有文件改动
3. 【遇到的问题】列出遇到的错误和修复尝试
4. 【待解决】当前仍未解决的问题

对话内容：
{conversation_text[:3000]}

请输出简洁的摘要（不超过 {max_summary_length} 字）："""
            
            response = llm.invoke([HumanMessage(content=summary_prompt)])
            summary = response.content.strip()[:max_summary_length]
            if summary:
                return summary
        except Exception as e:
            logger.warning(f"LLM 摘要生成失败: {e}，回退到基于规则的摘要")
    
    # Fallback: 基于规则的摘要
    return _rule_based_summary(old_messages)


def _format_for_llm_summary(messages: List[BaseMessage]) -> str:
    """将消息格式化为适合 LLM 摘要的文本"""
    parts = []
    for msg in messages:
        role = "User" if isinstance(msg, HumanMessage) else "Assistant"
        if isinstance(msg, ToolMessage):
            role = f"Tool({msg.name})"
        content = msg.content[:500] if hasattr(msg, 'content') else str(msg)[:500]
        parts.append(f"[{role}]: {content}")
    return "\n".join(parts)


def _rule_based_summary(messages: List[BaseMessage]) -> str:
    """基于规则的消息摘要（LLM 不可用时的回退方案）"""
    summary_parts = []
    
    # 提取用户需求
    original_request = extract_original_request(messages)
    if original_request:
        summary_parts.append(f"【原始需求】{original_request[:200]}")
    
    # 提取文件操作
    file_ops = []
    for msg in messages:
        if isinstance(msg, ToolMessage):
            if msg.name == "edit_file":
                file_ops.append(f"修改了文件: {msg.content[:100]}")
            elif msg.name == "write_file":
                file_ops.append(f"创建了新文件: {msg.content[:100]}")
            elif msg.name == "read_file":
                filename_match = re.search(r'Content of (.+?) ---', msg.content)
                if filename_match:
                    file_ops.append(f"读取了文件: {filename_match.group(1)}")
    
    if file_ops:
        summary_parts.append(f"【文件操作】\n" + "\n".join(f"- {op}" for op in file_ops[-5:]))
    
    # 提取 AI 关键回复
    ai_summaries = []
    for msg in messages:
        if isinstance(msg, AIMessage) and getattr(msg, 'name', None):
            ai_summaries.append(f"{msg.name}: {msg.content[:100]}")
    
    if ai_summaries:
        summary_parts.append(f"【分析摘要】\n" + "\n".join(f"- {s}" for s in ai_summaries[-3:]))
    
    return "\n\n".join(summary_parts) if summary_parts else "无重要历史信息"


# ==========================================
# 动态上下文窗口管理
# ==========================================

# 默认配置
DEFAULT_CONFIG = {
    "max_context_tokens": 8000,       # 上下文最大 Token 数
    "coder_keep_turns": 4,            # Coder 保留的对话轮数
    "planner_keep_turns": 3,          # Planner 保留的对话轮数
    "reviewer_keep_turns": 2,         # Reviewer 保留的对话轮数
    "system_prompt_tokens": 800,      # 系统提示预留 Token
    "error_trace_tokens": 500,        # 错误信息预留 Token
}


def calculate_dynamic_window(
    total_tokens: int,
    max_tokens: int,
    current_turns: int,
    min_turns: int = 1,
    max_turns: int = 8
) -> int:
    """
    根据当前 Token 使用情况动态调整保留的对话轮数。
    
    Args:
        total_tokens: 当前上下文已使用的 Token 数
        max_tokens: 上下文最大 Token 限制
        current_turns: 当前默认的对话轮数
        min_turns: 最少保留轮数
        max_turns: 最多保留轮数
    
    Returns:
        调整后的对话轮数
    """
    token_ratio = total_tokens / max_tokens if max_tokens > 0 else 1.0
    
    if token_ratio > 0.8:
        # Token 使用紧张，减少上下文
        return max(min_turns, current_turns - 1)
    elif token_ratio < 0.3 and current_turns < max_turns:
        # Token 充裕，可以适当增加
        return min(max_turns, current_turns + 1)
    
    return current_turns


# ==========================================
# 上下文槽位管理 (优先级队列)
# ==========================================

class ContextSlot:
    """上下文槽位 - 用于管理不同类型的上下文片段"""
    def __init__(self, name: str, messages: List[BaseMessage], priority: int, is_fixed: bool = False):
        self.name = name
        self.messages = messages
        self.priority = priority  # 优先级数字越小越重要
        self.is_fixed = is_fixed  # 固定内容不可裁剪
        self.token_count = estimate_messages_tokens(messages)
    
    def trim(self, target_tokens: int) -> bool:
        """尝试裁剪消息到目标 Token 数，返回是否成功"""
        if self.is_fixed:
            return False
        
        # 从后往前裁剪
        while self.token_count > target_tokens and self.messages:
            removed = self.messages.pop()
            self.token_count = estimate_messages_tokens(self.messages)
        
        return self.token_count <= target_tokens


def build_context_with_priority(
    slots: List[ContextSlot],
    max_tokens: int
) -> List[BaseMessage]:
    """
    使用优先级队列构建上下文，当接近 Token 上限时自动裁剪低优先级内容。
    
    Args:
        slots: 上下文槽位列表（按优先级排序传入）
        max_tokens: 上下文最大 Token 限制
    
    Returns:
        构建好的消息列表
    """
    # 先计算总 Token 数
    total_tokens = sum(slot.token_count for slot in slots)
    
    # 如果超出限制，从低优先级开始裁剪
    if total_tokens > max_tokens:
        # 按优先级降序排列（先裁剪优先级低的）
        sorted_slots = sorted(slots, key=lambda s: s.priority, reverse=True)
        for slot in sorted_slots:
            if slot.is_fixed:
                continue
            if total_tokens > max_tokens:
                old_tokens = slot.token_count
                slot.trim(max_tokens - (total_tokens - old_tokens))
                total_tokens = sum(s.token_count for s in slots)
    
    # 按优先级顺序拼接
    sorted_slots = sorted(slots, key=lambda s: s.priority)
    result = []
    for slot in sorted_slots:
        result.extend(slot.messages)
    
    return result


# ==========================================
# 针对不同 Agent 的上下文构建器
# ==========================================

def build_coder_context(
    state: dict,
    workspace_map: str = "",
    max_tokens: int = None,
    llm=None
) -> List[BaseMessage]:
    """
    为 Coder Agent 构建优化上下文。
    
    Coder 需要：
    - 系统提示 (角色定义 + 规范)
    - 当前执行计划
    - 仓库地图
    - 报错信息 (如果有)
    - 最近的文件操作记录
    - 精简的历史消息 (避免过长文件内容)
    """
    config = DEFAULT_CONFIG.copy()
    if max_tokens:
        config["max_context_tokens"] = max_tokens
    
    plan = state.get("current_plan", "暂无计划")
    active_files = state.get("active_files", [])
    error_trace = state.get("error_trace", "")
    messages = list(state.get("messages", []))
    
    # 构建 Change Log
    change_log = build_edit_summary(messages)
    
    # 获取文件签名
    file_sigs = state.get("file_signatures") or extract_file_signatures(messages)
    file_sig_str = ""
    if file_sigs:
        sig_parts = [f"- {f}: {s}" for f, s in file_sigs.items()]
        file_sig_str = "\n".join(sig_parts[:10])
    
    # 构建系统提示
    system_content = f"""你是一位精通软件工程的专家 (Coder)。
你的目标是执行 Planner 的计划，或修复 Reviewer 发现的 Bug。

【当前工作区概览 (Repo Map)】
{workspace_map}

【当前执行计划】
{plan}

【本轮对话中，你已经完成的改动记录 (Change Log)】
{change_log}

【文件签名索引】
{file_sig_str if file_sig_str else "暂无文件签名信息"}

【目标文件】
{', '.join(active_files) if active_files else '未指定'}

【任务完成的判断标准（极其重要！）】
1. 当计划中要求创建/修改的所有文件都已经被 write_file 或 edit_file 工具成功操作后，你的任务就结束了。
2. 一旦某个工具调用返回了"Successfully created"/"成功"等确认信息，**该文件的操作即视为已完成**，你不需要、也不应该再次操作同一个文件。
3. 所有目标文件都处理完毕后，**直接回复空字符串或 "DONE"**，不要再调用任何工具。如果你继续调用工具，会导致 API 错误和工作流崩溃。
4. 判断你"做完了"的具体标准是：【目标文件】列表中的每一个路径都至少对应一次已经成功的 write_file / edit_file 调用。

【Coder 的职责边界】
1. 你只是一个"代码手术刀"。你没有执行、运行、测试或验证代码的环境与工具。
2. 如果计划中要求"测试"，你的任务仅仅是把测试逻辑**写到测试文件里**。
3. 不要在回答中设想代码运行的结果。

【测试文件编写规范（重要！）】
沙箱系统会自动扫描工作区中所有 `test_*.py` 和 `*_test.py` 文件并用 pytest / unittest 运行。
因此你在创建测试时必须遵循以下规范：
1. 测试文件命名：必须使用 `test_功能名.py` 或 `功能名_test.py` 格式。
2. 测试类格式：使用 `unittest.TestCase` 风格（推荐）或 `pytest` 风格均可。
   - `unittest` 风格：`class Test功能名(unittest.TestCase):` + `def test_具体用例(self):`
   - `pytest` 风格：`def test_具体用例():` 或使用 `assert` 的普通函数
3. 测试覆盖要求：
   - 正常输入（典型用例）
   - 边界情况（空值、单元素、极值、负数等）
   - 异常输入（预期抛出 TypeError、ValueError 等，使用 `self.assertRaises` 或 `pytest.raises`）
   - 重复元素、混合类型等特殊情况（视需求而定）
4. 如果项目有外部依赖（如 numpy、requests 等），请同时创建 `requirements.txt` 文件，沙箱会自动 `pip install -r requirements.txt`。
5. 测试文件应该包含 `class` / `def test_xxx` 这样清晰的结构，不要用 `if __name__ == '__main__': unittest.main()` 作为唯一入口——沙箱会自动发现所有 `Test*` 类和 `test_*` 函数，不需要手动入口。

【文件操作的强制性规范】
1. 创建：使用 `write_file` 创建全新文件。
2. 修改：你**必须先读取文件内容**，然后再使用 `edit_file` 工具进行精准替换。

【读取文件的策略（重要）】
- 对于小文件（< 5000 字符）：直接调用 `read_file(filename)` 获取完整内容
- 对于大文件（>= 5000 字符）：`read_file` 会返回 AST 结构大纲，列出所有函数/类的名称和行号范围
  - 使用 `read_function(filename, function_name)` 精确提取指定函数的完整源码
  - 使用 `read_class(filename, class_name)` 精确提取指定类的完整源码
  - 使用 `read_file_range(filename, start_line, end_line)` 读取指定行范围的代码

【推荐的读取流程】
1. 调用 `read_file(filename)` 查看文件结构
2. 如果是大文件，从 AST 大纲中确定你需要修改的函数/类名称
3. 调用 `read_function(filename, function_name)` 获取该函数的完整代码
4. 从返回的代码中提取精确的 search_block，调用 `edit_file` 进行替换
"""
    
    if error_trace:
        system_content += f"\n\n【沙盒运行报错信息，请先使用 read_file 查看文件，再修复 Bug】\n{error_trace}"
    
    # 构建上下文槽位
    system_slot = ContextSlot(
        "system_prompt",
        [SystemMessage(content=system_content)],
        priority=0,
        is_fixed=True
    )
    
    # 计算剩余可用 Token
    system_tokens = estimate_token_count(system_content)
    remaining_tokens = config["max_context_tokens"] - system_tokens - config["error_trace_tokens"]
    
    # 动态调整保留轮数
    keep_turns = calculate_dynamic_window(
        total_tokens=estimate_messages_tokens(messages),
        max_tokens=config["max_context_tokens"],
        current_turns=config["coder_keep_turns"]
    )
    
    # 压缩旧消息
    keep_count = keep_turns * 3
    if len(messages) > keep_count:
        old_messages = messages[:-keep_count]
        recent_messages = messages[-keep_count:]
        
        # 使用 LLM 摘要（如果可用）
        summary = summarize_conversation(old_messages, llm=llm)
        summary_slot = ContextSlot(
            "history_summary",
            [SystemMessage(content=f"【历史对话摘要】\n{summary}", name="ContextManager")],
            priority=3
        ) if summary else ContextSlot("history_summary", [], priority=3)
    else:
        recent_messages = messages
        summary_slot = ContextSlot("history_summary", [], priority=3)
    
    # 移除孤立的 ToolMessage（避免 API 400 错误）
    recent_messages = filter_orphan_tool_messages(recent_messages)
    # 压缩工具消息中的长文本
    recent_messages = compress_tool_messages(recent_messages)
    history_slot = ContextSlot("recent_history", recent_messages, priority=2)
    
    # 使用优先级队列构建
    slots = [system_slot, summary_slot, history_slot]
    context_messages = build_context_with_priority(slots, config["max_context_tokens"])
    
    return context_messages


def build_planner_context(
    state: dict,
    workspace_map: str = "",
    max_tokens: int = None,
    llm=None
) -> List[BaseMessage]:
    """
    为 Planner Agent 构建优化上下文。
    
    Planner 需要：
    - 系统提示 (角色定义 + 输出规范)
    - 用户需求
    - 仓库地图
    - 如果进入 Debug 循环，还需要历史计划摘要
    """
    config = DEFAULT_CONFIG.copy()
    if max_tokens:
        config["max_context_tokens"] = max_tokens
    
    messages = list(state.get("messages", []))
    current_plan = state.get("current_plan", "")
    error_trace = state.get("error_trace", "")
    retry_count = state.get("retry_count", 0)
    
    # 提取用户需求
    original_request = extract_original_request(messages)
    
    # 构建系统提示
    system_content = f"""你是一个资深的软件架构师 (Planner)。
你的任务是理解用户的需求，制定分步的代码开发/修改计划，并圈定需要涉及的本地文件。

【用户需求】
{original_request}

【当前工作区概览】
以下是当前项目的文件结构以及关键的函数/类摘要：
{workspace_map}
"""
    
    if retry_count > 0 and error_trace:
        system_content += f"""

【重要：当前处于 Debug 修复模式】
之前的代码修改已重试 {retry_count} 次，但仍遇到以下错误：
{error_trace[:1000]}

之前的计划是：
{current_plan[:500] if current_plan else '无'}

请分析问题原因，可能需要调整计划方向。
"""
    
    system_content += """

【关键能力：探索工作区】
如果上述摘要不足以让你做出决定，你可以使用 `list_directory` 工具查看目录结构，或使用 `read_file` 工具查看某个文件的具体完整内容。
当然，有时候也会出现用户给你的任务是在一个空白的工作区内开始的，这是被允许的。如果当前工作目录为空的话，你直接按照用户需求制定计划就可以了，不需要探索工作区。

【输出规范：交付计划】
当你完成了探索，确定了最终的执行计划后，请停止调用工具，以清晰的自然语言格式输出计划，包含分步说明和涉及的文件路径。
在计划末尾用【目标文件】标记列出所有涉及的文件路径，每行一个。
"""
    
    # 构建上下文槽位
    system_slot = ContextSlot(
        "system_prompt",
        [SystemMessage(content=system_content)],
        priority=0,
        is_fixed=True
    )
    
    # Planner 只需要最近的几条消息
    keep_turns = calculate_dynamic_window(
        total_tokens=estimate_messages_tokens(messages),
        max_tokens=config["max_context_tokens"],
        current_turns=config["planner_keep_turns"]
    )
    keep_count = keep_turns * 3
    recent = messages[-keep_count:] if len(messages) > keep_count else messages
    history_slot = ContextSlot("recent_history", recent, priority=2)
    
    slots = [system_slot, history_slot]
    context_messages = build_context_with_priority(slots, config["max_context_tokens"])
    
    return context_messages


def build_reviewer_context(
    state: dict,
    workspace_map: str = "",
    max_tokens: int = None,
    llm=None
) -> List[BaseMessage]:
    """
    为 Reviewer Agent 构建优化上下文。
    
    Reviewer 需要：
    - 原始执行计划
    - 报错信息
    - 刚才的代码修改记录
    - 不需要完整的历史对话
    """
    config = DEFAULT_CONFIG.copy()
    if max_tokens:
        config["max_context_tokens"] = max_tokens
    
    plan = state.get("current_plan", "")
    error_trace = state.get("error_trace", "")
    messages = list(state.get("messages", []))
    
    # 提取最近的修改记录
    change_log = build_edit_summary(messages, max_entries=3)
    
    system_content = f"""你是一个资深的代码审查员 (Reviewer)。
刚才 Coder 按照计划编写了代码，但在沙盒运行中报错了。

【原始执行计划】
{plan}

【当前工作区概览】
{workspace_map}

【Coder 刚才执行的修改操作】
{change_log}

【沙盒报错信息】
{error_trace}

你的任务是：
1. 分析报错的根本原因。
2. 给 Coder 提供明确、具体的修改建议（比如指出哪一行逻辑错了，应该怎么改）。
注意：你只用输出自然语言的分析和建议，绝对不要输出完整的代码，代码由 Coder 来写。
"""
    
    # 构建上下文槽位
    system_slot = ContextSlot(
        "system_prompt",
        [SystemMessage(content=system_content)],
        priority=0,
        is_fixed=True
    )
    
    # Reviewer 只需要最近的消息
    keep_turns = calculate_dynamic_window(
        total_tokens=estimate_messages_tokens(messages),
        max_tokens=config["max_context_tokens"],
        current_turns=config["reviewer_keep_turns"]
    )
    keep_count = keep_turns * 3
    recent = messages[-keep_count:] if len(messages) > keep_count else messages
    history_slot = ContextSlot("recent_history", recent, priority=2)
    
    slots = [system_slot, history_slot]
    context_messages = build_context_with_priority(slots, config["max_context_tokens"])
    
    return context_messages


# ==========================================
# 记忆摘要更新 (v2.0: 增强版)
# ==========================================

def update_memory_summary(
    current_summary: Optional[dict],
    new_messages: List[BaseMessage]
) -> dict:
    """
    增量更新记忆摘要，避免从头计算。
    
    Returns:
        更新后的 MemorySummary 字典
    """
    if current_summary is None:
        current_summary = {
            "original_request": "",
            "completed_steps": [],
            "key_decisions": [],
            "file_operations": []
        }
    
    # 提取原始需求 (只提取一次)
    if not current_summary.get("original_request"):
        current_summary["original_request"] = extract_original_request(new_messages)
    
    # 提取新的文件操作
    for msg in new_messages:
        if isinstance(msg, ToolMessage):
            if msg.name in ("edit_file", "write_file"):
                op_summary = f"{msg.name}: {msg.content[:150]}"
                if op_summary not in current_summary.get("file_operations", []):
                    current_summary.setdefault("file_operations", []).append(op_summary)
    
    # 更新文件签名缓存
    new_signatures = extract_file_signatures(new_messages)
    current_summary.setdefault("file_signatures", {}).update(new_signatures)
    
    return current_summary