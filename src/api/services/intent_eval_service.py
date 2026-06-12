"""Intent routing evals for Intent Router V3."""

from __future__ import annotations

import json
import time
from typing import Any

from src.api.services.eval_service import _evals_root, _workspace
from src.api.services.intent_router import classify_user_intent

INTENT_CORE_EVALS: list[dict[str, Any]] = [
    {
        "id": "greeting_direct_answer",
        "prompt": "你好",
        "expected_route": "direct_answer",
        "expected_complexity": "simple",
        "expected_execution_route": "lead_direct_reply",
        "must_require_write": False,
        "forbidden_agents": ["Coder", "Tester"],
        "notes": "问候不应生成任务卡。",
    },
    {
        "id": "identity_direct_answer",
        "prompt": "你是什么模型",
        "expected_route": "direct_answer",
        "expected_complexity": "simple",
        "expected_execution_route": "lead_direct_reply",
        "must_require_write": False,
        "forbidden_agents": ["Coder", "Tester"],
    },
    {
        "id": "general_explanation_direct_answer",
        "prompt": "解释一下快速排序为什么平均复杂度是 nlogn",
        "expected_route": "direct_answer",
        "expected_complexity": "simple",
        "expected_execution_route": "lead_direct_reply",
        "must_require_write": False,
    },
    {
        "id": "workspace_structure_read_only",
        "prompt": "帮我看看这个项目结构",
        "expected_route": "read_only",
        "expected_execution_route": "agenthub_delivery",
        "must_require_read": True,
        "must_require_write": False,
        "forbidden_agents": ["Coder"],
    },
    {
        "id": "readme_typo_small_edit",
        "prompt": "帮我改 README 的错别字",
        "expected_route": "small_edit",
        "expected_complexity": "small_code",
        "expected_execution_route": "agenthub_delivery",
        "must_require_read": True,
        "must_require_write": True,
        "must_require_approval": False,
    },
    {
        "id": "python_script_feature_delivery",
        "prompt": "帮我用 Python 写常见排序算法并比较性能",
        "expected_route": "feature_delivery",
        "expected_complexity": "small_code",
        "expected_execution_route": "agenthub_delivery",
        "must_require_read": True,
        "must_require_write": True,
        "must_require_shell": True,
        "must_require_approval": False,
    },
    {
        "id": "bug_traceback_debug_fix",
        "prompt": "运行时报错 Traceback: ModuleNotFoundError，请帮我修复",
        "expected_route": "debug_fix",
        "expected_execution_route": "agenthub_delivery",
        "must_require_read": True,
        "must_require_write": True,
        "must_require_shell": True,
    },
    {
        "id": "pytest_test_only",
        "prompt": "帮我运行 pytest 验证一下",
        "expected_route": "test_only",
        "expected_execution_route": "agenthub_delivery",
        "must_require_read": True,
        "must_require_write": False,
        "must_require_shell": True,
    },
    {
        "id": "diff_review_only",
        "prompt": "帮我审查一下当前 diff 有没有风险",
        "expected_route": "review_only",
        "expected_execution_route": "agenthub_delivery",
        "must_require_read": True,
        "must_require_write": False,
    },
    {
        "id": "delete_files_risky_operation",
        "prompt": "帮我删除 node_modules",
        "expected_route": "risky_operation",
        "expected_complexity": "high_risk",
        "expected_execution_route": "agenthub_delivery",
        "must_require_write": True,
        "must_require_shell": True,
        "must_require_approval": True,
    },
    {
        "id": "install_dependency_risky_operation",
        "prompt": "帮我安装依赖并运行 npm install",
        "expected_route": "risky_operation",
        "expected_complexity": "high_risk",
        "expected_execution_route": "agenthub_delivery",
        "must_require_write": True,
        "must_require_shell": True,
        "must_require_approval": True,
    },
    {
        "id": "ambiguous_improve_clarification",
        "prompt": "帮我优化一下",
        "expected_route": "clarification_needed",
        "expected_complexity": "simple",
        "expected_execution_route": "lead_direct_reply",
        "must_require_write": False,
        "must_have_missing_information": True,
    },
    {
        "id": "conversation_followup_uses_memory",
        "prompt": "继续",
        "conversation_summary": "上一轮用户要求实现 Python 排序算法性能比较脚本，已经进入代码任务并准备写文件。",
        "expected_route": "feature_delivery",
        "expected_complexity": "small_code",
        "expected_execution_route": "agenthub_delivery",
        "must_require_read": True,
        "must_require_write": True,
        "must_require_approval": False,
        "notes": "短 follow-up 必须参考 conversation memory，不能按普通闲聊处理。",
    },
]


def _generated_intent_eval_cases() -> list[dict[str, Any]]:
    """Return broad deterministic intent eval cases grouped by user scenario."""

    cases: list[dict[str, Any]] = []

    def add(
        group: str,
        index: int,
        prompt: str,
        route: str,
        *,
        execution_route: str | None = None,
        read: bool | None = None,
        write: bool | None = None,
        shell: bool | None = None,
        approval: bool | None = None,
        conversation_summary: str = "",
        missing: bool = False,
        forbidden_agents: list[str] | None = None,
        risk_level: str | None = None,
    ) -> None:
        case: dict[str, Any] = {
            "id": f"{group}_{index:03d}",
            "group": group,
            "prompt": prompt,
            "expected_route": route,
            "expected_execution_route": execution_route
            or ("lead_direct_reply" if route in {"direct_answer", "clarification_needed"} else "agenthub_delivery"),
        }
        if conversation_summary:
            case["conversation_summary"] = conversation_summary
        if read is not None:
            case["must_require_read"] = read
        if write is not None:
            case["must_require_write"] = write
        if shell is not None:
            case["must_require_shell"] = shell
        if approval is not None:
            case["must_require_approval"] = approval
        if missing:
            case["must_have_missing_information"] = True
        if forbidden_agents:
            case["forbidden_agents"] = forbidden_agents
        if risk_level:
            case["expected_risk_level"] = risk_level
        cases.append(case)

    direct_prompts = [
        "你好，今天能帮我做什么",
        "哈喽",
        "你是谁",
        "你能做什么",
        "请介绍一下 nanoCursor 的能力",
        "hello",
        "hi",
        "你是什么模型",
        "关于你，简单说一下",
        "嗨，先聊两句",
    ]
    for idx, prompt in enumerate(direct_prompts, 1):
        add("greeting_identity", idx, prompt, "direct_answer", read=False, write=False, forbidden_agents=["Coder"])

    explanation_prompts = [
        "解释一下 debounce 和 throttle 的区别",
        "为什么快速排序平均复杂度是 nlogn",
        "讲讲 Python GIL 是什么",
        "说明一下 REST API 和 RPC 的区别",
        "总结一下软件质量保障的价值",
        "怎么看多 Agent 系统的上下文管理",
        "解释一下依赖注入模式",
        "为什么搜索索引能加速查询",
        "讲讲 Docker 镜像和容器区别",
        "什么是 SSE 事件流",
        "解释一下最小化设计原则",
        "总结一下团队协作应该关注什么",
        "为什么异步代码会阻塞事件循环",
        "explain what a retry policy is",
        "summarize how retry backoff works",
        "python和java谁更好",
        "Python 和 Java 哪个更适合初学者",
        "你觉得 React 和 Vue 怎么选",
        "帮我比较一下 Python 和 Java 的优缺点",
    ]
    for idx, prompt in enumerate(explanation_prompts, 1):
        add("general_explanation", idx, prompt, "direct_answer", read=False, write=False)

    read_only_prompts = [
        "帮我看看这个项目结构",
        "看一下当前目录都有什么内容",
        "检查一下这个仓库的目录结构",
        "分析一下这个仓库结构下面有哪些内容",
        "帮我看看项目说明写了什么",
        "看看项目设置都有哪些",
        "帮我看看这个项目入口在哪里",
        "帮我看一看目录层级结构",
        "inspect the repository structure",
        "scan this project and summarize folders",
        "帮我看看最近改动情况",
        "分析一下项目里有哪些服务入口",
        "帮我看看这个目录下都有些什么",
        "检查一下当前工作区状态",
        "看一下 docs 目录主要写了什么",
        "你帮我看看这个路径下都有一些什么文件",
    ]
    for idx, prompt in enumerate(read_only_prompts, 1):
        add("workspace_read_only", idx, prompt, "read_only", read=True, write=False, forbidden_agents=["Coder"])

    small_edit_prompts = [
        "帮我改 README 的错别字",
        "帮我修改 README 的文案",
        "帮我修复 README 注释里的 typo",
        "帮我给 README 补一行说明",
        "帮我把 README 标题改一下",
        "帮我格式化一下配置文件",
        "帮我 rename README 里的旧项目名",
        "帮我改 README 里的一处文案",
        "帮我补充函数注释",
        "帮我改 README 的拼写问题",
        "fix a typo in README",
        "帮我微调 README 的描述",
        "帮我改 package.json 里的脚本名",
        "帮我给配置加一行注释",
        "帮我修复 README markdown 表格格式",
    ]
    for idx, prompt in enumerate(small_edit_prompts, 1):
        add("small_edit", idx, prompt, "small_edit", read=True, write=True, approval=False)

    feature_prompts = [
        "帮我用 Python 写常见排序算法并比较性能",
        "完整实现一个登录模块并补充测试",
        "帮我开发一个端到端的数据导入代码模块",
        "实现一个多文件配置加载模块并验证",
        "创建一个完整的 CLI 工具并补测试",
        "帮我构建一个 FastAPI 接口和测试",
        "新增一个用户偏好模块并完成验证",
        "帮我开发一个项目索引系统并写测试",
        "完整实现缓存模块并跑 benchmark",
        "帮我用 TypeScript 实现一个组件并补测试",
        "实现一个文件搜索模块并比较性能",
        "构建一个小型任务队列模块并验证",
        "帮我完成一个导出报告功能和测试",
        "新增一个上下文压缩模块并验证",
        "实现一个偏好策略模块并补测试",
        "帮我开发一个 SSE 事件流模块",
        "完整实现一个恢复机制并补测试",
        "创建一个 benchmark runner 并运行验证",
        "帮我写一个排序算法性能对比脚本",
        "实现一个项目健康检查模块并测试",
    ]
    for idx, prompt in enumerate(feature_prompts, 1):
        add("feature_delivery", idx, prompt, "feature_delivery", read=True, write=True, approval=False)

    debug_prompts = [
        "运行时报错 Traceback，请帮我修复",
        "接口返回 500，帮我定位并修复",
        "前端出现 404 错误，请帮我修",
        "pytest 失败了，帮我修复测试",
        "ModuleNotFoundError 报错，请帮我处理",
        "应用启动崩溃了，帮我修",
        "TypeError 异常需要修复",
        "构建失败，请帮我定位问题并修复",
        "这个 bug 会导致保存失败，帮我修复",
        "测试用例失败，帮我修代码",
        "fix this traceback error",
        "npm test 失败了，请帮我修复",
        "接口异常返回空数据，帮我修复",
        "导入模块时报错 exception，帮我修",
        "页面点击后崩溃，请帮我修复",
    ]
    for idx, prompt in enumerate(debug_prompts, 1):
        add("debug_fix", idx, prompt, "debug_fix", read=True, write=True, shell=True)

    test_prompts = [
        "帮我运行 pytest 验证一下",
        "运行 npm test 看看结果",
        "帮我执行 lint 检查",
        "跑一下 benchmark",
        "帮我验证当前测试是否通过",
        "run pytest for this project",
        "check the test suite",
        "帮我运行类型检查",
        "帮我运行 lint check 验证一下",
        "帮我跑一下性能测试",
    ]
    for idx, prompt in enumerate(test_prompts, 1):
        add("test_only", idx, prompt, "test_only", read=True, write=False, shell=True, forbidden_agents=["Coder"])

    review_prompts = [
        "帮我审查一下当前 diff 有没有风险",
        "复核一下这次改动有没有回归",
        "review 当前代码质量",
        "帮我做一次质量风险评审",
        "审查一下这个 diff patch 有没有问题",
        "帮我 audit 当前变更",
        "评审一下代码风格和质量",
        "帮我看一下 diff 是否有回归",
        "审查一下测试覆盖是否足够",
        "复核交付报告有没有遗漏",
        "你帮我看看这个路径下都有一些什么文件 有没有什么涉及到算法的代码 代码写的对不对",
        "帮我检查一下这里的代码写得对不对",
        "帮我看看这个文件夹有没有算法代码，代码有没有问题",
    ]
    for idx, prompt in enumerate(review_prompts, 1):
        add("review_only", idx, prompt, "review_only", read=True, write=False, forbidden_agents=["Coder"])

    risky_prompts = [
        "帮我删除 node_modules",
        "帮我安装依赖并运行 npm install",
        "执行 git push 推送到远程",
        "帮我处理数据库 schema 迁移",
        "删除所有旧日志文件",
        "帮我改认证权限模块并上线",
        "运行 rm -rf 清理目录",
        "帮我 git push 推送代码",
        "帮我修改 secret token 配置",
        "执行生产部署脚本",
        "批量删除临时文件",
        "帮我做数据库回滚",
        "帮我安装依赖并运行 npm install --force",
        "帮我改鉴权策略并迁移数据",
        "git push current branch to GitHub",
    ]
    for idx, prompt in enumerate(risky_prompts, 1):
        add("risky_operation", idx, prompt, "risky_operation", read=True, write=True, shell=True, approval=True, risk_level="high")

    followup_cases = [
        ("继续", "上一轮用户要求实现 Python 排序算法性能比较脚本，已经进入代码任务并准备写文件。", "feature_delivery", True, True, None),
        ("接着", "上一轮正在运行 pytest 验证登录模块，测试结果还没整理。", "test_only", True, False, True),
        ("继续做", "上一轮出现 Traceback 报错，正在修复 bug。", "debug_fix", True, True, True),
        ("下一步", "上一轮正在审查 diff 风险，尚未完成复核。", "review_only", True, False, False),
        ("按刚才继续", "上一轮只读查看项目结构，准备继续分析目录。", "read_only", True, False, False),
        ("继续实现", "上一轮用户要求实现导入模块，已经规划代码文件。", "feature_delivery", True, True, None),
        ("接着验证", "上一轮正在运行 benchmark 验证性能。", "test_only", True, False, True),
        ("继续修", "上一轮测试失败并出现 error，需要修复。", "debug_fix", True, True, True),
        ("按上面做", "上一轮用户要求实现小工具并准备写文件。", "feature_delivery", True, True, None),
        ("keep going", "上一轮用户要求实现 Python 文件，已经进入代码任务。", "feature_delivery", True, True, None),
        ("next step", "上一轮正在 review 当前 diff 和风险。", "review_only", True, False, False),
        ("照着刚才", "上一轮正在查看 README 和项目结构，只读分析。", "read_only", True, False, False),
    ]
    for idx, (prompt, summary, route, read, write, shell) in enumerate(followup_cases, 1):
        add("conversation_followup", idx, prompt, route, read=read, write=write, shell=shell, conversation_summary=summary)

    no_write_cases = [
        ("解释一下怎么写 README，不要改文件", "direct_answer", False),
        ("给我登录模块实现方案，不要改代码", "read_only", True),
        ("只分析这个项目结构，不要修改文件", "read_only", True),
        ("帮我看看 README 该怎么写，不要改文件", "read_only", True),
        ("说明一下如何修这个 bug，不要动文件", "read_only", True),
        ("给我一个测试方案，不要创建文件", "test_only", True),
        ("只读检查当前项目，不要写文件", "read_only", True),
        ("帮我看一下配置，不要修改", "read_only", True),
        ("不要改代码，只解释这个错误可能原因", "read_only", True),
        ("read only inspect this project", "read_only", True),
        ("do not modify files, explain the plan", "direct_answer", False),
        ("仅分析 README，不要改", "read_only", True),
    ]
    for idx, (prompt, route, read) in enumerate(no_write_cases, 1):
        add("explicit_no_write", idx, prompt, route, read=read, write=False, approval=False, forbidden_agents=["Coder"])

    ambiguous_prompts = [
        "帮我优化一下",
        "改一下",
        "弄一下",
        "搞一下",
        "完善一下",
        "处理一下",
        "make it better",
        "improve it",
        "fix it",
        "帮我处理一下",
    ]
    for idx, prompt in enumerate(ambiguous_prompts, 1):
        add("ambiguous_clarification", idx, prompt, "clarification_needed", read=False, write=False, missing=True)

    return cases


INTENT_CORE_EVALS.extend(_generated_intent_eval_cases())


def list_intent_eval_cases() -> list[dict[str, Any]]:
    """Return the core intent-routing eval catalog."""
    return [dict(item) for item in INTENT_CORE_EVALS]


def run_intent_eval_suite(
    case_ids: list[str] | None = None,
    *,
    workspace_dir: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Run the selected intent eval cases and optionally persist the result."""
    catalog = {case["id"]: case for case in INTENT_CORE_EVALS}
    selected_ids = case_ids or [case["id"] for case in INTENT_CORE_EVALS]
    results: list[dict[str, Any]] = []
    for case_id in selected_ids:
        case = catalog.get(case_id)
        if not case:
            results.append({"id": case_id, "overall": "error", "error": "intent eval case not found"})
            continue
        results.append(run_intent_eval_case(case))

    passed = sum(1 for item in results if item.get("overall") == "passed")
    failed = len(results) - passed
    failures = [item for item in results if item.get("overall") != "passed"]
    summary = {
        "suite": "intent_core",
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "pass_rate": round(passed / max(len(results), 1), 3),
        "metrics": _intent_eval_metrics(results),
        "failed_case_ids": [str(item.get("id") or "") for item in failures],
        "failures": failures,
        "results": results,
        "completed_at": time.time(),
    }
    if persist:
        summary["eval_run_id"] = _persist_intent_eval_result(summary, workspace_dir)
    return summary


def run_intent_eval_case(case: dict[str, Any]) -> dict[str, Any]:
    """Run one intent eval case."""
    decision = classify_user_intent(
        str(case.get("prompt") or ""),
        conversation_summary=str(case.get("conversation_summary") or ""),
    )
    checks = _score_intent_decision(case, decision)
    overall = "passed" if all(check["status"] == "passed" for check in checks) else "failed"
    return {
        "id": case.get("id"),
        "group": case.get("group", "core"),
        "case": {key: value for key, value in case.items() if key != "notes"},
        "prompt": case.get("prompt"),
        "overall": overall,
        "decision": decision,
        "checks": checks,
    }


def _score_intent_decision(case: dict[str, Any], decision: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    _expect(checks, "route", decision.get("route") == case.get("expected_route"), decision.get("route"), case.get("expected_route"))
    if case.get("expected_complexity"):
        _expect(
            checks,
            "complexity",
            decision.get("complexity") == case.get("expected_complexity"),
            decision.get("complexity"),
            case.get("expected_complexity"),
        )
    if case.get("expected_execution_route"):
        _expect(
            checks,
            "execution_route",
            decision.get("execution_route") == case.get("expected_execution_route"),
            decision.get("execution_route"),
            case.get("expected_execution_route"),
        )
    for key, field in [
        ("must_require_read", "requires_workspace_read"),
        ("must_require_write", "requires_workspace_write"),
        ("must_require_shell", "requires_shell"),
        ("must_require_approval", "requires_approval"),
    ]:
        if key in case:
            _expect(checks, field, bool(decision.get(field)) is bool(case.get(key)), decision.get(field), case.get(key))
    if case.get("must_have_missing_information"):
        _expect(checks, "missing_information", bool(decision.get("missing_information")), decision.get("missing_information"), "non-empty")
    if case.get("expected_risk_level"):
        _expect(
            checks,
            "risk_level",
            decision.get("risk_level") == case.get("expected_risk_level"),
            decision.get("risk_level"),
            case.get("expected_risk_level"),
        )
    forbidden_agents = {str(agent).lower() for agent in case.get("forbidden_agents", [])}
    if forbidden_agents:
        agents = {str(agent).lower() for agent in decision.get("suggested_agents", [])}
        _expect(
            checks,
            "forbidden_agents",
            not bool(agents & forbidden_agents),
            sorted(agents),
            f"no {sorted(forbidden_agents)}",
        )
    _expect(checks, "v3_context_requirements", isinstance(decision.get("context_requirements"), dict), type(decision.get("context_requirements")).__name__, "dict")
    _expect(checks, "v3_tool_permissions", isinstance(decision.get("tool_permissions"), dict), type(decision.get("tool_permissions")).__name__, "dict")
    _expect(checks, "v3_agent_specs", isinstance(decision.get("suggested_agent_specs"), list), type(decision.get("suggested_agent_specs")).__name__, "list")
    return checks


def _intent_eval_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute grouped metrics that make router regressions easier to inspect."""

    group_stats: dict[str, dict[str, Any]] = {}
    high_risk_total = 0
    high_risk_passed = 0
    no_write_total = 0
    no_write_passed = 0
    direct_total = 0
    direct_passed = 0
    semantic_used_count = 0
    deterministic_hint_counts: dict[str, int] = {}
    for item in results:
        case_group = str(item.get("decision", {}).get("raw_decision", {}).get("eval_group") or item.get("group") or "")
        case = item.get("case") if isinstance(item.get("case"), dict) else {}
        group = str(case.get("group") or case_group or "core")
        stat = group_stats.setdefault(group, {"total": 0, "passed": 0, "failed": 0, "pass_rate": 0.0})
        stat["total"] += 1
        if item.get("overall") == "passed":
            stat["passed"] += 1
        else:
            stat["failed"] += 1

        decision = item.get("decision") if isinstance(item.get("decision"), dict) else {}
        raw_decision = decision.get("raw_decision") if isinstance(decision.get("raw_decision"), dict) else {}
        router_trace = raw_decision.get("router_trace") if isinstance(raw_decision.get("router_trace"), dict) else {}
        if router_trace.get("semantic_used") is True:
            semantic_used_count += 1
        for hint in router_trace.get("deterministic_hints") or []:
            hint_key = str(hint)
            deterministic_hint_counts[hint_key] = deterministic_hint_counts.get(hint_key, 0) + 1
        if case.get("expected_route") == "risky_operation":
            high_risk_total += 1
            if decision.get("route") == "risky_operation" and decision.get("requires_approval") is True:
                high_risk_passed += 1
        if case.get("must_require_write") is False:
            no_write_total += 1
            if decision.get("requires_workspace_write") is False:
                no_write_passed += 1
        if case.get("expected_route") == "direct_answer":
            direct_total += 1
            if decision.get("route") == "direct_answer" and decision.get("execution_route") == "lead_direct_reply":
                direct_passed += 1
    for stat in group_stats.values():
        stat["pass_rate"] = round(stat["passed"] / max(stat["total"], 1), 3)
    return {
        "groups": group_stats,
        "high_risk_recall": round(high_risk_passed / max(high_risk_total, 1), 3),
        "no_write_compliance": round(no_write_passed / max(no_write_total, 1), 3),
        "direct_answer_precision_proxy": round(direct_passed / max(direct_total, 1), 3),
        "semantic_used_count": semantic_used_count,
        "deterministic_hint_counts": deterministic_hint_counts,
    }


def _expect(checks: list[dict[str, Any]], check_id: str, ok: bool, actual: Any, expected: Any) -> None:
    checks.append(
        {
            "id": check_id,
            "status": "passed" if ok else "failed",
            "actual": actual,
            "expected": expected,
        }
    )


def _persist_intent_eval_result(summary: dict[str, Any], workspace_dir: str | None) -> str:
    workspace = _workspace(workspace_dir)
    eval_run_id = f"intent-core-{int(time.time() * 1000)}"
    result_dir = _evals_root(workspace) / "intent" / eval_run_id
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "result.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return eval_run_id


def get_intent_eval_run(eval_run_id: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """Read a persisted intent eval run."""
    safe_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in eval_run_id).strip("-")
    result_path = _evals_root(_workspace(workspace_dir)) / "intent" / safe_id / "result.json"
    if not result_path.exists():
        raise ValueError(f"Intent eval run 不存在: {eval_run_id}")
    return json.loads(result_path.read_text(encoding="utf-8"))
