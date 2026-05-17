"""Build a pre-run execution blueprint for AgentHub."""

from __future__ import annotations

from typing import Any

from src.api.services.capability_service import recommend_capabilities


DEFAULT_STAGES = [
    {
        "id": "understand",
        "title": "理解需求与项目上下文",
        "owner": "Planner",
        "description": "读取需求、识别验收点，并结合项目索引判断影响范围。",
        "capabilities": ["tool.project_index"],
    },
    {
        "id": "plan",
        "title": "生成执行计划",
        "owner": "Lead",
        "description": "确认任务阶段、负责人、能力包和风险控制点。",
        "capabilities": ["tool.memory"],
    },
    {
        "id": "implement",
        "title": "实现代码变更",
        "owner": "Coder",
        "description": "按计划修改文件，并保持 Diff 可审查。",
        "capabilities": ["tool.file_ops", "tool.project_index"],
    },
    {
        "id": "verify",
        "title": "验证与复核",
        "owner": "Tester",
        "description": "检查需求覆盖、测试结果、恢复点和交付风险。",
        "capabilities": ["skill.delivery-review", "tool.recovery"],
    },
]


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword.lower() in text for keyword in keywords)


def _extra_stages(prompt: str) -> list[dict[str, Any]]:
    text = prompt.lower()
    stages: list[dict[str, Any]] = []
    if _contains_any(text, ["前端", "界面", "页面", "ui", "样式", "好看", "美化", "布局", "交互"]):
        stages.append(
            {
                "id": "design_review",
                "title": "体验与界面复核",
                "owner": "Designer",
                "description": "检查信息层级、视觉密度、折叠交互和浅色系一致性。",
                "capabilities": ["skill.frontend-polish", "mcp.figma"],
            }
        )
    if _contains_any(text, ["github", "issue", "pr", "pull request", "ci", "代码审查"]):
        stages.append(
            {
                "id": "collaboration_review",
                "title": "协作流程复核",
                "owner": "Reviewer",
                "description": "预留 Issue / PR / CI 检查位，便于后续接入 GitHub MCP。",
                "capabilities": ["mcp.github", "skill.delivery-review"],
            }
        )
    if _contains_any(text, ["文档", "readme", "接口", "api", "说明", "规范"]):
        stages.append(
            {
                "id": "docs_trace",
                "title": "文档与需求追踪",
                "owner": "Planner",
                "description": "把需求、接口说明和验收标准整理成可追踪证据。",
                "capabilities": ["mcp.docs", "tool.project_index"],
            }
        )
    return stages


def _risks(prompt: str, recommendation: dict[str, Any]) -> list[dict[str, str]]:
    text = prompt.lower()
    risks = []
    if recommendation.get("summary", {}).get("planned_count", 0):
        risks.append(
            {
                "level": "medium",
                "title": "存在待接入能力",
                "detail": "部分 MCP 能力当前是规划状态，执行时会先以本地工具和结构化提示兜底。",
            }
        )
    if _contains_any(text, ["部署", "发布", "上线", "docker", "环境"]):
        risks.append(
            {
                "level": "medium",
                "title": "部署链路需额外验证",
                "detail": "发布相关任务需要确认构建命令、环境变量和回滚路径。",
            }
        )
    if _contains_any(text, ["删除", "回滚", "重置", "覆盖"]):
        risks.append(
            {
                "level": "high",
                "title": "涉及破坏性变更",
                "detail": "执行前应确认备份和恢复点，必要时要求用户二次确认。",
            }
        )
    if not risks:
        risks.append(
            {
                "level": "low",
                "title": "常规交付风险",
                "detail": "按计划执行即可，重点关注 Diff 审查、测试验证和需求覆盖。",
            }
        )
    return risks


def build_run_blueprint(prompt: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """Return a human-confirmable blueprint before starting a run."""
    recommendation = recommend_capabilities(prompt, workspace_dir)
    stages = [*DEFAULT_STAGES, *_extra_stages(prompt)]
    return {
        "prompt": prompt,
        "title": "AgentHub 执行蓝图",
        "summary": {
            "stage_count": len(stages),
            "agent_count": recommendation["summary"]["agent_count"],
            "capability_count": recommendation["summary"]["capability_count"],
            "risk_count": len(_risks(prompt, recommendation)),
        },
        "agents": recommendation["agents"],
        "capabilities": recommendation["capabilities"],
        "stages": stages,
        "risks": _risks(prompt, recommendation),
        "reasons": recommendation["reasons"],
    }
