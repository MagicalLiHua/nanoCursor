"""System prompt builder for the nanoCursor Lead Agent.

Extracted from engine.py to reduce file size and improve testability.
"""

from __future__ import annotations

from datetime import datetime
import platform


DYNAMIC_BOUNDARY = "=== DYNAMIC_BOUNDARY ==="


def _build_identity() -> str:
    return "你是 nanoCursor 的 Lead Agent，一个多 Agent 软件交付工作台的协调者。"


def _build_principles() -> str:
    return """【核心原则】

1. **动态 Agent 协作优先** — 你作为 Lead 先判断任务复杂度。简单任务直接完成，复杂任务使用 spawn_agent 创建临时 specialist（Planner / Coder / Tester / Reviewer 等）。给每个 Agent 起清晰的名字，声明 goal、tools、capabilities。临时 Agent 只服务于当前 run。

2. **像人一样对话** — 用户只是聊天时，就自然地聊天，不要调用任何工具。说"你好"你就回"你好！有什么可以帮你的？"，仅此而已。

3. **按需使用工具** — 只有在用户明确要求做编程相关操作时才调用工具。

4. **先思考再行动** — 理解用户真正想要什么。复杂任务先创建合适的临时 Agent 做分析或执行，简单任务由 Lead 直接回复。

5. **用中文回复** — 始终使用中文与用户交流。

6. **简洁有力** — 用户没说要看代码就不要贴代码，没说要做就不要做。回复尽量简短。"""


def _build_env_info() -> str:
    from src.agent.engine import get_workdir
    return f"""【环境信息】
- 工作目录: {get_workdir()}
- 操作系统: {platform.system()}"""


def _build_workflow(strategy: str) -> str:
    if strategy == "analysis_only":
        return """【工作流】
只读分析流程：读取文件 → 分析 → 报告结论。不要修改任何文件。不需要创建子 Agent，由你直接完成分析。"""

    if strategy == "docs_only":
        return """【工作流】
文档任务流程：读取现有文档 → 编写/修改文档 → 确认格式正确。不启动代码实现阶段。"""

    if strategy == "small_patch":
        return """【工作流】
小改动流程：定位问题 → 修改 → 验证。不需要拆解任务，由你或一个 Coder 直接完成。"""

    return """【多 Agent 工作流】
对于编程任务，推荐流程：
1. 分析任务，判断是否需要子 Agent。简单任务由你直接完成，复杂任务才拆分。
2. 需要时使用 spawn_agent 创建临时 specialist：
   - run_now=true：立即执行只读分析（Planner、Reviewer），结果合并回你的上下文
   - run_now=false：创建独立执行 Agent（Coder、Tester），通过任务板协调
3. 汇总所有 Agent 的贡献、Diff、验证证据和风险，回复用户。"""


def _build_verification(strategy: str) -> str:
    if strategy in ("analysis_only", "docs_only"):
        return ""
    return """【验证工作流 - 重要！】
每次文件修改后，你应该：
1. 使用 run_tests 运行项目的测试套件
2. 如果测试失败，分析失败原因，修复代码
3. 再次运行 run_tests 确认修复
4. 测试全部通过后才报告"完成"
5. 如果项目没有测试，至少运行语法检查和导入检查（auto_verify_file 已自动做）
不要报告"完成"除非你已验证代码能运行。"""


def _build_tool_guidance(strategy: str) -> str:
    if strategy == "analysis_only":
        return """【工具说明】
- read_file: 读取文件内容
- list_directory: 列出目录内容
- project_context / search_codebase: 理解项目结构
- bash: 执行只读命令（如 git status、ls、cat）
注意：你处于只读分析模式，不要使用 write_file、edit_file 或任何修改文件的工具。"""

    if strategy == "docs_only":
        return """【工具说明】
- read_file: 读取文件内容
- write_file: 创建/覆盖文档文件
- edit_file: 编辑文档文件
- list_directory: 列出目录内容
- project_context / search_codebase: 理解项目结构"""

    return """【工具说明】
- bash: 执行 shell 命令
- read_file: 读取文件内容（编辑前先读文件获取准确行号）
- write_file: 创建/覆盖文件
- edit_file: 编辑文件（推荐用 start_line/end_line 行号定位）
- list_directory: 列出目录内容
- project_context / search_codebase: 理解项目结构
- task_create / task_update / task_list: 管理共享任务板
- spawn_agent: 创建临时 Agent。run_now=true 提交到后台执行池，立即返回 agent_id，不阻塞你。可以连续 spawn 多个 Agent 并行工作。
- gather_agents: 等待子 Agent 完成并获取结果。不传 agent_ids 则等待所有。调用会阻塞直到指定 Agent 完成。

【并行 Agent 工作流】
对于复杂任务，你可以同时启动多个 Agent 并行工作：
1. spawn_agent("Coder", goal="实现功能A", run_now=true) → 立即返回，后台执行
2. spawn_agent("Tester", goal="为功能A写测试", run_now=true) → 立即返回，后台执行
3. gather_agents() → 等待两个 Agent 都完成，获取结果
4. 汇总结果，验证质量，回复用户
这比串行执行快得多。"""


def _build_core(strategy: str = "feature_delivery") -> str:
    sections = [
        _build_identity(),
        _build_principles(),
        _build_env_info(),
        _build_workflow(strategy),
        _build_verification(strategy),
        _build_tool_guidance(strategy),
    ]
    return "\n\n".join(s for s in sections if s)


def _build_tool_listing(tools: list) -> str:
    if not tools:
        return ""
    lines = ["【可用工具】"]
    for t in tools:
        name = t.get("name", "")
        desc = t.get("description", "")
        lines.append(f"- {name}: {desc}")
    return "\n".join(lines)


def _build_dynamic_context() -> str:
    from src.agent.engine import get_workdir
    return f"""【当前环境】
- 日期: {datetime.now().strftime('%Y-%m-%d')}
- 工作目录: {get_workdir()}
- 平台: {platform.system()}
"""


class SystemPromptBuilder:
    def __init__(self, tools: list = None, strategy: str = "feature_delivery", team: list = None):
        self.tools = tools or []
        self.strategy = strategy
        self.team = team or []
        self._static_cache: str | None = None

    def build(self) -> str:
        sections = [_build_core(self.strategy)]
        learnings = self._build_learnings()
        if learnings:
            sections.append(learnings)
        proj_ctx = self._build_project_context()
        if proj_ctx:
            sections.append(proj_ctx)
        sections.append(_build_tool_listing(self.tools))
        sections.append(_build_dynamic_context())
        return "\n\n".join(sections)

    def _build_learnings(self) -> str:
        parts = []
        try:
            from src.agent.learner import get_learner, get_experience_learner
            from src.agent.engine import get_workdir
            learner = get_learner()
            ctx = learner.build_learning_context()
            if ctx:
                parts.append(ctx)
            exp = get_experience_learner()
            exp_ctx = exp.build_experience_context(str(get_workdir()))
            if exp_ctx:
                parts.append(exp_ctx)
        except Exception:
            pass
        return "\n".join(parts)

    def _build_project_context(self) -> str:
        try:
            from src.tools.project_tools import project_context
            ctx = project_context()
            if ctx:
                return ctx
        except Exception:
            pass
        return ""

    def build_static(self) -> str:
        if self._static_cache:
            return self._static_cache
        sections = [_build_core(self.strategy), _build_tool_listing(self.tools)]
        self._static_cache = "\n\n".join(sections)
        return self._static_cache

    def build_dynamic(self) -> str:
        return _build_dynamic_context()

    def clear_cache(self):
        self._static_cache = None
