"""Build the nanoCursor capability catalog."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from src.api.services.intent_router import classify_user_intent
from src.api.services.mcp_status_service import get_mcp_server_status
from src.api.services.mcp_tool_catalog_service import classify_mcp_tool


BUILTIN_CAPABILITIES = [
    {
        "id": "tool.file_ops",
        "name": "文件读写",
        "kind": "tool",
        "status": "ready",
        "description": "读取、编辑、写入项目文件，并把变更沉淀到 Diff 与交付物。",
        "tags": ["write_file", "edit_file", "diff"],
        "agents": ["Coder", "Reviewer"],
    },
    {
        "id": "tool.project_index",
        "name": "项目索引",
        "kind": "tool",
        "status": "ready",
        "description": "按符号、依赖、入口点理解代码库，减少盲目搜索。",
        "tags": ["search_codebase", "project_context"],
        "agents": ["Planner", "Coder"],
    },
    {
        "id": "tool.memory",
        "name": "偏好记忆",
        "kind": "tool",
        "status": "ready",
        "description": "记录用户风格、技术栈和历史反馈，让 nanoCursor 越用越懂项目。",
        "tags": ["add_memory", "recall_memories"],
        "agents": ["Lead", "Planner"],
    },
    {
        "id": "tool.recovery",
        "name": "安全恢复",
        "kind": "tool",
        "status": "ready",
        "description": "汇总备份、快照、风险，并支持受控文件回滚。",
        "tags": ["snapshot", "rollback", "risk"],
        "agents": ["Lead", "Tester"],
    },
]

MCP_TEMPLATES = [
    {
        "id": "mcp.filesystem",
        "name": "本地文件系统 MCP",
        "kind": "mcp",
        "status": "planned",
        "description": "把当前项目目录作为受控文件系统上下文，适合跨工具读取文件和目录。",
        "tags": ["filesystem", "local", "project"],
        "agents": ["Planner", "Coder"],
        "setup_source": ".nanocursor/mcp.json",
        "setup_hint": "可使用内置预设一键写入当前工作区的 filesystem MCP 配置。",
    },
    {
        "id": "mcp.memory",
        "name": "记忆图谱 MCP",
        "kind": "mcp",
        "status": "planned",
        "description": "提供轻量知识图谱记忆，适合沉淀项目事实、偏好和长期上下文。",
        "tags": ["memory", "knowledge", "graph"],
        "agents": ["Lead", "Planner"],
        "setup_source": ".nanocursor/mcp.json",
        "setup_hint": "可使用内置预设写入 @modelcontextprotocol/server-memory 配置。",
    },
    {
        "id": "mcp.sequential-thinking",
        "name": "Sequential Thinking MCP",
        "kind": "mcp",
        "status": "planned",
        "description": "为复杂问题提供结构化推理工具，适合规划、排错和方案比较。",
        "tags": ["reasoning", "planning", "debug"],
        "agents": ["Planner", "Reviewer"],
        "setup_source": ".nanocursor/mcp.json",
        "setup_hint": "可使用内置预设写入 sequential-thinking MCP 配置。",
    },
    {
        "id": "mcp.github",
        "name": "GitHub MCP",
        "kind": "mcp",
        "status": "planned",
        "description": "接入 Issue、PR、代码审查和 CI 状态，形成真实研发协作闭环。",
        "tags": ["issues", "pull_requests", "ci"],
        "agents": ["Lead", "Reviewer"],
        "setup_source": ".mcp.json / .cursor/mcp.json / .nanocursor/mcp.json",
        "setup_hint": "可使用内置预设写入官方 GitHub MCP 配置；需要 Docker 和 GITHUB_PERSONAL_ACCESS_TOKEN。",
    },
    {
        "id": "mcp.figma",
        "name": "Figma MCP",
        "kind": "mcp",
        "status": "planned",
        "description": "读取设计稿和组件规范，辅助 Designer / Coder 保持 UI 一致性。",
        "tags": ["design", "components", "handoff"],
        "agents": ["Designer", "Coder"],
        "setup_source": ".mcp.json / .cursor/mcp.json / .nanocursor/mcp.json",
        "setup_hint": "配置 Figma MCP server 后，Designer 和 Coder 可复用设计上下文。",
    },
    {
        "id": "mcp.docs",
        "name": "文档知识库 MCP",
        "kind": "mcp",
        "status": "planned",
        "description": "连接项目文档、接口说明和规范库，支持需求追踪与答疑。",
        "tags": ["docs", "knowledge", "rag"],
        "agents": ["Planner", "Tester"],
        "setup_source": ".mcp.json / .cursor/mcp.json / .nanocursor/mcp.json",
        "setup_hint": "可使用内置预设把 docs/ 或当前项目目录作为文档知识库。",
    },
]

SKILL_TEMPLATES = [
    {
        "id": "skill.frontend-polish",
        "name": "前端体验打磨 Skill",
        "kind": "skill",
        "status": "ready",
        "description": "沉淀浅色系、低拥挤、可折叠、按钮连续性的 UI 偏好。",
        "tags": ["ui", "layout", "interaction", "前端", "界面", "页面", "视觉", "样式", "交互", "按钮", "输入框", "美化"],
        "agents": ["Designer", "Coder"],
        "use_cases": ["浅色系工作台美化", "拥挤界面降噪", "折叠与响应式交互"],
        "inputs": ["用户 UI 偏好", "当前页面结构", "截图反馈"],
        "outputs": ["视觉改进建议", "前端样式补丁", "交互验收清单"],
        "risks": ["可能影响已有布局密度，需要保留可扫描性。"],
    },
    {
        "id": "skill.delivery-review",
        "name": "交付复核 Skill",
        "kind": "skill",
        "status": "ready",
        "description": "从需求覆盖、质量门禁、Diff 风险和恢复点复核一次交付。",
        "tags": ["review", "quality", "traceability", "复核", "验收", "质量", "风险", "交付"],
        "agents": ["Reviewer", "Tester"],
        "use_cases": ["交付前验收", "风险复盘", "展示用例质量检查"],
        "inputs": ["任务清单", "Diff 摘要", "测试结果", "交付报告"],
        "outputs": ["覆盖率判断", "风险列表", "下一步修复建议"],
        "risks": ["依赖输入证据完整度，缺少测试结果时只能给出部分结论。"],
    },
]

RECOMMENDATION_RULES = [
    {
        "id": "frontend",
        "keywords": ["前端", "界面", "页面", "ui", "样式", "好看", "美化", "布局", "交互", "响应式"],
        "agents": ["Designer", "Coder", "Reviewer"],
        "capabilities": ["skill.frontend-polish", "tool.file_ops", "tool.project_index", "mcp.figma"],
        "reason": "需求涉及界面和交互体验，适合启用前端打磨 Skill，并让 Designer 与 Coder 协同。",
    },
    {
        "id": "quality",
        "keywords": ["测试", "验证", "质量", "复核", "审查", "风险", "diff", "review", "bug", "修复", "报错", "异常", "回归"],
        "agents": ["Tester", "Reviewer", "Coder"],
        "capabilities": ["skill.delivery-review", "tool.project_index", "tool.recovery"],
        "reason": "需求涉及质量或缺陷修复，需要测试、复核和可恢复保障。",
    },
    {
        "id": "github",
        "keywords": ["github", "issue", "pr", "pull request", "ci", "仓库", "代码审查"],
        "agents": ["Lead", "Reviewer"],
        "capabilities": ["mcp.github", "skill.delivery-review"],
        "reason": "需求涉及研发协作平台，后续可接 GitHub MCP 查看 Issue、PR 和 CI。",
    },
    {
        "id": "docs",
        "keywords": ["文档", "readme", "接口", "api", "知识库", "说明", "规范", "需求"],
        "agents": ["Planner", "Tester"],
        "capabilities": ["mcp.docs", "tool.project_index", "skill.delivery-review"],
        "reason": "需求涉及文档和规范，需要 Planner 做结构化理解，并用知识库能力补充上下文。",
    },
    {
        "id": "memory",
        "keywords": ["偏好", "记住", "风格", "习惯", "长期", "记忆"],
        "agents": ["Lead", "Planner"],
        "capabilities": ["tool.memory", "skill.frontend-polish"],
        "reason": "需求涉及个人偏好或长期记忆，适合启用偏好记忆能力。",
    },
    {
        "id": "deploy",
        "keywords": ["部署", "发布", "上线", "devops", "docker", "环境", "构建"],
        "agents": ["DevOps", "Tester", "Lead"],
        "capabilities": ["tool.recovery", "mcp.github", "skill.delivery-review"],
        "reason": "需求涉及发布交付，需要恢复点、质量复核和后续 CI/CD 能力。",
    },
]


def _workspace(workspace_dir: str | None = None) -> Path:
    return Path(workspace_dir or ".").resolve()


def _status_rank(status: str) -> int:
    return {"ready": 0, "configured": 1, "planned": 2}.get(status, 3)


def _read_mcp_config(workspace: Path) -> list[dict[str, Any]]:
    candidates = [
        workspace / ".mcp.json",
        workspace / ".cursor" / "mcp.json",
        workspace / ".nanocursor" / "mcp.json",
    ]
    capabilities: list[dict[str, Any]] = []
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        servers = data.get("mcpServers") or data.get("servers") or {}
        if not isinstance(servers, dict):
            continue
        for name, raw in servers.items():
            command = raw.get("command", "") if isinstance(raw, dict) else ""
            capabilities.append(
                {
                    "id": f"mcp.{name}",
                    "name": f"{name} MCP",
                    "kind": "mcp",
                    "status": "configured",
                    "description": f"已在 {path.name} 中配置，可作为 nanoCursor 外部工具能力。",
                    "tags": [tag for tag in ["mcp", command] if tag],
                    "agents": ["Lead", "Coder"],
                    "source": str(path.relative_to(workspace)) if path.is_relative_to(workspace) else str(path),
                    "setup_source": str(path.relative_to(workspace)) if path.is_relative_to(workspace) else str(path),
                    "setup_hint": f"已读取 {name} server 配置，命令：{command or '未声明'}。",
                }
            )
    return capabilities


def _read_workspace_skills(workspace: Path) -> list[dict[str, Any]]:
    capabilities: list[dict[str, Any]] = []
    for root in [workspace / "skills", workspace / ".nanocursor" / "skills"]:
        if not root.exists():
            continue
        for skill_file in sorted(root.glob("*/SKILL.md")):
            skill_name = skill_file.parent.name
            try:
                first_line = skill_file.read_text(encoding="utf-8").splitlines()[0].strip("# ").strip()
            except OSError:
                first_line = skill_name
            capabilities.append(
                {
                    "id": f"skill.{skill_name}",
                    "name": first_line or skill_name,
                    "kind": "skill",
                    "status": "configured",
                    "description": "工作区内的可复用 Skill，可用于约束 Agent 的专门工作流。",
                    "tags": ["skill", skill_name],
                    "agents": ["Lead", "Coder"],
                    "source": str(skill_file.relative_to(workspace)),
                    "use_cases": ["项目专属工作流", "重复任务标准化", "团队经验沉淀"],
                    "inputs": ["用户需求", "工作区上下文", "Skill 指令"],
                    "outputs": ["约束后的执行步骤", "专门化交付结果"],
                    "risks": ["Skill 内容来自工作区，执行前仍需要结合当前任务判断适用性。"],
                }
            )
    return capabilities


def _skill_slug(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip().lower()).strip("-")
    if not slug:
        raise ValueError("Skill 名称不能为空。")
    return slug[:60]


def import_workspace_skill(
    name: str,
    description: str = "",
    content: str = "",
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Create or update a workspace-local Skill under .nanocursor/skills."""
    workspace = _workspace(workspace_dir)
    slug = _skill_slug(name)
    skill_dir = workspace / ".nanocursor" / "skills" / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    body = content.strip() or description.strip() or f"{name.strip()} 的工作区自定义 Skill。"
    if not body.startswith("#"):
        body = f"# {name.strip()}\n\n{body}"
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(body + "\n", encoding="utf-8")
    return {
        "id": f"skill.{slug}",
        "name": name.strip(),
        "kind": "skill",
        "status": "configured",
        "description": description.strip() or "工作区导入的自定义 Skill。",
        "source": str(skill_file.relative_to(workspace)),
    }


def build_capability_hub(workspace_dir: str | None = None) -> dict[str, Any]:
    """Return nanoCursor capabilities grouped for the frontend."""
    workspace = _workspace(workspace_dir)
    mcp_by_id = {item["id"]: dict(item) for item in MCP_TEMPLATES}
    for configured in _read_mcp_config(workspace):
        mcp_by_id[configured["id"]] = configured
    capabilities = [
        *BUILTIN_CAPABILITIES,
        *mcp_by_id.values(),
        *SKILL_TEMPLATES,
        *_read_workspace_skills(workspace),
    ]
    capabilities = sorted(capabilities, key=lambda item: (_status_rank(item["status"]), item["kind"], item["name"]))
    summary = {
        "total": len(capabilities),
        "ready": sum(1 for item in capabilities if item["status"] == "ready"),
        "configured": sum(1 for item in capabilities if item["status"] == "configured"),
        "planned": sum(1 for item in capabilities if item["status"] == "planned"),
    }
    groups = []
    labels = {"tool": "内置工具", "mcp": "MCP 连接器", "skill": "Skills"}
    for kind in ["tool", "mcp", "skill"]:
        items = [item for item in capabilities if item["kind"] == kind]
        groups.append({"id": kind, "label": labels[kind], "items": items})
    return {
        "workspace_dir": str(workspace),
        "summary": summary,
        "groups": groups,
        "capabilities": capabilities,
    }


def build_mcp_execution_plan(
    capability_ids: list[str],
    capabilities: list[dict[str, Any]] | None = None,
    workspace_dir: str | None = None,
) -> list[dict[str, Any]]:
    """Build a compact MCP execution plan for recommended/planned capabilities."""
    if not capability_ids:
        return []

    workspace = _workspace(workspace_dir)
    capability_by_id = {item["id"]: item for item in (capabilities or build_capability_hub(str(workspace))["capabilities"])}
    mcp_ids = [capability_id for capability_id in capability_ids if str(capability_id).startswith("mcp.")]
    plans: list[dict[str, Any]] = []

    for capability_id in mcp_ids:
        capability = capability_by_id.get(capability_id, {"id": capability_id, "name": capability_id, "status": "planned"})
        runtime_status = get_mcp_server_status(capability_id, str(workspace))
        tools_cache = runtime_status.get("tools_cache") if isinstance(runtime_status.get("tools_cache"), dict) else {}
        cached_tools = tools_cache.get("tools") if isinstance(tools_cache.get("tools"), list) else []
        circuit_open_until = float(runtime_status.get("circuit_open_until") or 0)
        circuit_remaining = max(0, int(circuit_open_until - time.time()))
        configured = capability.get("status") == "configured"
        enabled = capability.get("enabled", True) is not False and runtime_status.get("enabled", True) is not False
        usable = configured and enabled and circuit_remaining == 0
        status = "circuit_open" if circuit_remaining else runtime_status.get("status") or capability.get("status") or "unknown"

        if usable and cached_tools:
            reason = f"{capability.get('name', capability_id)} 已配置，缓存中有 {len(cached_tools)} 个工具，可在审批后通过 mcp_call 使用。"
        elif usable:
            reason = f"{capability.get('name', capability_id)} 已配置，但尚未缓存工具列表；执行前建议先刷新 tools/list。"
        elif circuit_remaining:
            reason = f"{capability.get('name', capability_id)} 当前熔断中，约 {circuit_remaining} 秒后可重试。"
        elif not configured:
            reason = f"{capability.get('name', capability_id)} 尚未配置，当前只作为规划提示。"
        else:
            reason = f"{capability.get('name', capability_id)} 当前不可用，请检查配置或状态。"

        plans.append({
            "server_id": capability_id,
            "name": capability.get("name", capability_id),
            "status": status,
            "configured": configured,
            "enabled": enabled,
            "usable": usable,
            "tool_count": len(cached_tools),
            "tools": [
                classify_mcp_tool(capability_id, tool)
                for tool in cached_tools[:8]
                if isinstance(tool, dict)
            ],
            "approval_required_count": sum(
                1 for tool in cached_tools if isinstance(tool, dict) and classify_mcp_tool(capability_id, tool)["requires_approval"]
            ),
            "cache": {
                "cached_at": tools_cache.get("cached_at"),
                "config_hash": tools_cache.get("config_hash", ""),
            } if tools_cache else {},
            "failure_count": int(runtime_status.get("failure_count") or 0),
            "last_error": runtime_status.get("last_error", ""),
            "circuit_remaining_seconds": circuit_remaining,
            "reason": reason,
        })

    return plans


def recommend_capabilities(prompt: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """Recommend agents and capabilities for a user request."""
    hub = build_capability_hub(workspace_dir)
    by_id = {item["id"]: item for item in hub["capabilities"]}
    prompt_text = (prompt or "").lower()
    intent = classify_user_intent(prompt)
    matched = [
        rule
        for rule in RECOMMENDATION_RULES
        if any(keyword.lower() in prompt_text for keyword in rule["keywords"])
    ]
    if intent.get("execution_route") == "lead_direct_reply" and not matched:
        matched = [
            {
                "id": "lead_direct",
                "agents": ["Lead"],
                "capabilities": [],
                "reason": intent["rationale"],
            }
        ]
    elif not matched and intent["requires_workspace_write"]:
        matched = [
            {
                "id": "code_task",
                "agents": ["Lead", "Coder"],
                "capabilities": ["tool.project_index", "tool.file_ops", "skill.delivery-review"],
                "reason": intent["rationale"],
            }
        ]
    if not matched:
        matched = [
            {
                "id": "default",
                "agents": ["Lead", "Planner", "Coder", "Tester"],
                "capabilities": ["tool.project_index", "tool.file_ops", "skill.delivery-review"],
                "reason": "默认按完整软件交付流程推荐：先理解项目，再实现变更，最后复核质量。",
            }
        ]

    agent_names: list[str] = []
    capability_ids: list[str] = []
    reasons: list[str] = []
    for rule in matched:
        for agent in rule["agents"]:
            if agent not in agent_names:
                agent_names.append(agent)
        for capability_id in rule["capabilities"]:
            if capability_id not in capability_ids:
                capability_ids.append(capability_id)
        reasons.append(rule["reason"])

    capabilities = []
    for capability_id in capability_ids:
        item = by_id.get(capability_id)
        if item:
            capabilities.append(item)
        else:
            capabilities.append(
                {
                    "id": capability_id,
                    "name": capability_id,
                    "kind": capability_id.split(".", 1)[0],
                    "status": "planned",
                    "description": "推荐的扩展能力，当前尚未配置。",
                    "tags": [],
                    "agents": [],
                }
            )

    ready_count = sum(1 for item in capabilities if item.get("status") in {"ready", "configured"})
    mcp_plan = build_mcp_execution_plan(capability_ids, capabilities, workspace_dir)
    return {
        "prompt": prompt,
        "intent": intent,
        "summary": {
            "agent_count": len(agent_names),
            "capability_count": len(capabilities),
            "ready_count": ready_count,
            "planned_count": len(capabilities) - ready_count,
            "mcp_count": len(mcp_plan),
            "usable_mcp_count": sum(1 for item in mcp_plan if item.get("usable")),
        },
        "agents": agent_names,
        "capabilities": capabilities,
        "mcp_plan": mcp_plan,
        "reasons": reasons[:3],
    }
