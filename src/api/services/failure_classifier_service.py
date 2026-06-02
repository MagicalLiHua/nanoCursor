"""Classify run failures into structured categories with evidence and suggested actions.

R4: Every failed run gets at least one FailureRecord persisted as failures.json.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.infra import config as config_module


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


class FailureClass(str, Enum):
    ENVIRONMENT_ERROR = "environment_error"
    COMMAND_ERROR = "command_error"
    TEST_FAILURE = "test_failure"
    TOOL_POLICY_BLOCKED = "tool_policy_blocked"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_TIMEOUT = "approval_timeout"
    WORKSPACE_ERROR = "workspace_error"
    MODEL_ERROR = "model_error"
    PATCH_ERROR = "patch_error"
    UNKNOWN_ERROR = "unknown_error"


class SuggestedAction(BaseModel):
    action_id: str = ""
    label: str
    mode: str = "manual"           # manual | auto | confirm
    description: str = ""


class FailureRecord(BaseModel):
    failure_id: str
    thread_id: str
    failure_class: FailureClass
    title: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    related_files: list[str] = Field(default_factory=list)
    suggested_actions: list[SuggestedAction] = Field(default_factory=list)
    can_auto_retry: bool = False
    created_at: str = ""


# ---------------------------------------------------------------------------
# Pattern-based classification (kept compatible with existing callers)
# ---------------------------------------------------------------------------

RULES: list[tuple[str, str, list[str], str]] = [
    (
        "syntax_error",
        "high",
        [
            r"SyntaxError", r"IndentationError", r"invalid syntax",
            r"unexpected EOF", r"unexpected token", r"Unexpected token",
            r"ModuleNotFoundError", r"ImportError", r"cannot import",
        ],
        "代码存在语法或导入错误。",
    ),
    (
        "test_failure",
        "high",
        [
            r"FAILED", r"AssertionError", r"assert \w+ == \w+", r"assert \w+ in \w+",
            r"pytest.*failed", r"tests? failed", r"test_.*FAILED", r"E\s+\w+Error",
        ],
        "测试用例返回失败。",
    ),
    (
        "permission_denied",
        "high",
        [
            r"Permission denied", r"EACCES", r"EPERM",
            r"operation not permitted", r"Access denied",
        ],
        "权限不足，无法执行操作。",
    ),
    (
        "path_escape",
        "high",
        [
            r"escapes workspace", r"路径越界", r"path.*escape", r"path.*traversal",
            r"outside workspace", r"工作区.*外",
        ],
        "路径越界，尝试访问工作区外文件。",
    ),
    (
        "tool_timeout",
        "medium",
        [
            r"timeout", r"TimeoutError", r"timed out", r"超时", r"请求.*超时",
        ],
        "工具调用超时。",
    ),
    (
        "llm_interrupted",
        "medium",
        [
            r"APIError", r"APIConnectionError", r"RateLimitError", r"rate limit",
            r"connection.*error", r"LLM.*error", r"服务.*不可用", r"模型.*不可用",
            r"API.*error", r"NetworkError",
        ],
        "LLM 或 API 连接异常。",
    ),
]


# ---- helpers ----

def _workspace(workspace_dir: str | None = None) -> Path:
    return Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()


def _run_dir(thread_id: str, workspace_dir: str | None = None) -> Path:
    safe_id = thread_id.replace("/", "_").replace("\\", "_")
    return _workspace(workspace_dir) / ".nanocursor" / "runs" / safe_id


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# ---- legacy classifier (kept for backward compat) ----


def classify_failure(text: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Classify an error text into a failure category. (Legacy API kept for callers.)"""
    text_lower = (text or "").lower()
    context = context or {}

    for category, confidence, patterns, summary in RULES:
        for pattern in patterns:
            if text_lower and re.search(pattern, text_lower, re.IGNORECASE):
                return {
                    "category": category,
                    "confidence": confidence,
                    "summary": summary,
                    "evidence": {
                        "text_snippet": text[:300] if text else "",
                        "matched_pattern": pattern,
                    },
                }

    if context.get("stage_id"):
        return {
            "category": "unknown",
            "confidence": "low",
            "summary": f"阶段 {context.get('stage_id')} 执行失败，原因未归类。",
            "evidence": {"text_snippet": text[:300] if text else ""},
        }

    return {
        "category": "unknown",
        "confidence": "low",
        "summary": "失败原因未能自动归类。",
        "evidence": {"text_snippet": text[:300] if text else ""},
    }


# ---- R4: FailureClass-based classification ----


def classify_failure_typed(error_text: str, context: dict[str, Any] | None = None) -> FailureClass:
    """Classify an error into a FailureClass enum value."""
    text_lower = (error_text or "").lower()
    context = context or {}

    if not text_lower:
        return FailureClass.UNKNOWN_ERROR

    # environment errors
    if any(p in text_lower for p in (
        "module not found", "importerror", "modulenotfounderror",
        "no module named", "command not found",
        "api_key", "api key", "api-key", "apikey",
        "not configured", "environment",
    )):
        return FailureClass.ENVIRONMENT_ERROR

    # test failures
    if any(p in text_lower for p in (
        "assertionerror", "assert ", "test failed", "tests failed",
        "E       ", "FAILED", "===", "failures=",
    )):
        return FailureClass.TEST_FAILURE

    # command errors
    if any(p in text_lower for p in (
        "exit code", "returned non-zero", "subprocess",
        "command.*error", "error: command",
    )):
        return FailureClass.COMMAND_ERROR

    # model/LLM errors
    if any(p in text_lower for p in (
        "apierror", "rate limit", "ratelimit", "connection error",
        "llm.*error", "api.*error", "networkerror",
        "timeout", "model.*error",
    )):
        return FailureClass.MODEL_ERROR

    # workspace/path errors
    if any(p in text_lower for p in (
        "no such file", "permission denied",
        "escapes workspace", "路径越界",
        "workspace.*not.*found", "工作区",
    )):
        return FailureClass.WORKSPACE_ERROR

    # patch errors
    if any(p in text_lower for p in (
        "edit.*fail", "patch.*fail", "conflict",
        "unified diff", "hunk.*fail",
    )):
        return FailureClass.PATCH_ERROR

    # tool policy / approval
    if any(p in text_lower for p in (
        "blocked", "policy", "approval.*reject",
        "approval.*timeout", "requires approval",
    )):
        return FailureClass.TOOL_POLICY_BLOCKED

    return FailureClass.UNKNOWN_ERROR


def _failure_title(fc: FailureClass) -> str:
    titles = {
        FailureClass.ENVIRONMENT_ERROR: "环境配置错误",
        FailureClass.COMMAND_ERROR: "命令执行失败",
        FailureClass.TEST_FAILURE: "测试验证失败",
        FailureClass.TOOL_POLICY_BLOCKED: "策略拦截",
        FailureClass.APPROVAL_REJECTED: "审批被拒绝",
        FailureClass.APPROVAL_TIMEOUT: "审批超时",
        FailureClass.WORKSPACE_ERROR: "工作区错误",
        FailureClass.MODEL_ERROR: "模型/API 错误",
        FailureClass.PATCH_ERROR: "文件修改错误",
        FailureClass.UNKNOWN_ERROR: "未分类错误",
    }
    return titles.get(fc, "未知错误")


def classify_run_failures(thread_id: str, workspace_dir: str | None = None) -> list[FailureRecord]:
    """Scan a run's events, tool calls, and quality gate for failures.

    Produces one or more FailureRecords with classification, evidence, and suggestions.
    """
    from src.api.services.event_store import get_event_store
    store = get_event_store()
    ws_str = str(_workspace(workspace_dir))
    session = store.get_session(thread_id, ws_str)
    events = store.list_events(thread_id, ws_str)
    known_paths = _known_workspace_paths(Path(ws_str))
    records: list[FailureRecord] = []

    # 1. Classify error events
    error_events = [e for e in events if e.type == "error"]
    for e in error_events:
        content = str(e.content or e.title or "")
        fc = classify_failure_typed(content)
        payload = e.payload if isinstance(e.payload, dict) else {}
        related_files = _extract_related_files(
            " ".join([content, str(e.title or ""), _jsonish(payload)]),
            known_paths,
        )
        records.append(FailureRecord(
            failure_id=f"fail_{uuid.uuid4().hex[:12]}",
            thread_id=thread_id,
            failure_class=fc,
            title=_failure_title(fc),
            evidence={
                "event_id": getattr(e, "id", ""),
                "event_title": e.title,
                "event_content": content[:500],
                "error_detail": payload.get("detail", payload.get("error", ""))[:500],
                "related_files": related_files,
            },
            related_files=related_files,
            suggested_actions=_suggest_actions_for(fc, content),
            can_auto_retry=_can_auto_retry(fc),
            created_at=datetime.now(timezone.utc).isoformat(),
        ))

    # 2. Check failed tool calls from ledger
    try:
        from src.api.services.run_ledger_service import get_run_tools
        tools = get_run_tools(thread_id, ws_str)
        for tc in tools:
            if tc.status == "failed":
                fc = classify_failure_typed(tc.output_tail)
                related_files = _extract_related_files(
                    " ".join([str(tc.tool_name), str(tc.output_tail)]),
                    known_paths,
                )
                records.append(FailureRecord(
                    failure_id=f"fail_{uuid.uuid4().hex[:12]}",
                    thread_id=thread_id,
                    failure_class=fc,
                    title=f"工具 '{tc.tool_name}' 执行失败",
                    evidence={
                        "call_id": tc.call_id,
                        "tool": tc.tool_name,
                        "output": tc.output_tail[:500],
                        "related_files": related_files,
                    },
                    related_files=related_files,
                    suggested_actions=_suggest_actions_for(fc, tc.output_tail),
                    can_auto_retry=_can_auto_retry(fc),
                    created_at=datetime.now(timezone.utc).isoformat(),
                ))
    except Exception:
        pass

    # 3. Check approval records for timeouts/rejections
    try:
        approvals_dir = _run_dir(thread_id, ws_str) / "approvals"
        if approvals_dir.is_dir():
            from src.api.services.approval_service import get_pending_approvals
            get_pending_approvals(thread_id, ws_str)
            for approval_file in sorted(approvals_dir.glob("*.json")):
                try:
                    ap = json.loads(approval_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue

                status = str(ap.get("status", ""))
                reason = str(ap.get("reason", ap.get("comment", "")))
                reason_lower = reason.lower()
                if status != "rejected":
                    continue

                is_timeout = "超时" in reason or "timeout" in reason_lower or ap.get("expired") is True
                fc = FailureClass.APPROVAL_TIMEOUT if is_timeout else FailureClass.APPROVAL_REJECTED
                related_files = _extract_related_files(
                    _jsonish(ap),
                    known_paths,
                )
                evidence = dict(ap)
                evidence["related_files"] = related_files
                records.append(FailureRecord(
                    failure_id=f"fail_{uuid.uuid4().hex[:12]}",
                    thread_id=thread_id,
                    failure_class=fc,
                    title=f"{_failure_title(fc)}: {ap.get('tool', '')}",
                    evidence=evidence,
                    related_files=related_files,
                    suggested_actions=_suggest_actions_for(fc, reason),
                    can_auto_retry=False,
                    created_at=datetime.now(timezone.utc).isoformat(),
                ))
    except Exception:
        pass

    # 4. If no errors were found but run failed, create unknown failure
    if not records and session and session.get("status") == "failed":
        records.append(FailureRecord(
            failure_id=f"fail_{uuid.uuid4().hex[:12]}",
            thread_id=thread_id,
            failure_class=FailureClass.UNKNOWN_ERROR,
            title="运行失败（无具体错误事件）",
            evidence={"session_status": session.get("status"), "related_files": []},
            related_files=[],
            suggested_actions=_suggest_actions_for(FailureClass.UNKNOWN_ERROR, ""),
            can_auto_retry=True,
            created_at=datetime.now(timezone.utc).isoformat(),
        ))

    return records


def _known_workspace_paths(workspace: Path) -> set[str]:
    paths: set[str] = set()
    if not workspace.exists():
        return paths
    ignored_dirs = {".git", ".nanocursor", ".venv", "node_modules", "__pycache__", "dist", "build"}
    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(workspace).as_posix()
        except ValueError:
            continue
        parts = set(rel.split("/"))
        if ignored_dirs & parts:
            continue
        paths.add(rel)
    return paths


def _extract_related_files(text: str, known_paths: set[str]) -> list[str]:
    mentions: list[str] = []
    lowered = text.lower()
    for path in sorted(known_paths):
        if _path_token_present(path.lower(), lowered):
            mentions.append(path)

    basename_index: dict[str, list[str]] = {}
    for path in known_paths:
        basename_index.setdefault(Path(path).name.lower(), []).append(path)

    raw_matches = re.findall(
        r"[\w./\\-]+\.(?:py|js|jsx|ts|tsx|css|md|json|toml|yaml|yml|txt|html|vue)",
        text,
    )
    for raw in raw_matches:
        candidate = raw.strip(".,;:()[]{}'\"`").replace("\\", "/").lstrip("./")
        if candidate in known_paths:
            mentions.append(candidate)
            continue
        matches = basename_index.get(Path(candidate).name.lower(), [])
        if len(matches) == 1:
            mentions.append(matches[0])

    return _unique(mentions)[:12]


def _path_token_present(path: str, text: str) -> bool:
    return re.search(r"(?<![\w./\\-])" + re.escape(path) + r"(?![\w./\\-])", text) is not None


def _unique(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def _jsonish(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)


def _suggest_actions_for(fc: FailureClass, evidence_text: str) -> list[SuggestedAction]:
    actions: list[SuggestedAction] = []

    if fc == FailureClass.ENVIRONMENT_ERROR:
        actions.append(SuggestedAction(
            label="检查依赖安装", mode="manual",
            description="运行 pip install -r requirements.txt 或 npm install 安装缺失依赖。",
        ))
        actions.append(SuggestedAction(
            label="检查环境变量", mode="manual",
            description="确认 .env 文件包含所有必需的 API key 和配置项。",
        ))
    elif fc == FailureClass.COMMAND_ERROR:
        actions.append(SuggestedAction(
            label="查看命令输出", mode="manual",
            description="检查命令的 stdout/stderr 输出，确认错误原因。",
        ))
        actions.append(SuggestedAction(
            label="手动运行命令", mode="manual",
            description="在终端中手动运行失败的命令，确认环境是否正常。",
        ))
    elif fc == FailureClass.TEST_FAILURE:
        actions.append(SuggestedAction(
            label="创建修复运行", mode="confirm",
            description="基于失败的测试信息，创建一个新的 remediation run 来修复代码。",
        ))
        actions.append(SuggestedAction(
            label="查看测试详情", mode="manual",
            description="查看完整测试输出，定位失败的断言。",
        ))
    elif fc == FailureClass.MODEL_ERROR:
        actions.append(SuggestedAction(
            label="检查 API key", mode="manual",
            description="确认 LLM 提供商的 API key 有效且未过期。",
        ))
        actions.append(SuggestedAction(
            label="重试运行", mode="confirm",
            description="LLM 错误通常是临时的，可以重试。",
        ))
    elif fc == FailureClass.WORKSPACE_ERROR:
        actions.append(SuggestedAction(
            label="检查工作区路径", mode="manual",
            description="确认工作区目录存在且有读写权限。",
        ))
    elif fc == FailureClass.PATCH_ERROR:
        actions.append(SuggestedAction(
            label="回滚到 checkpoint", mode="confirm",
            description="从 checkpoint 恢复文件到修改前的状态。",
        ))
    elif fc == FailureClass.TOOL_POLICY_BLOCKED:
        actions.append(SuggestedAction(
            label="修改策略设置", mode="manual",
            description="在 workspace settings 中调整 tool policy 允许此操作。",
        ))
    elif fc == FailureClass.APPROVAL_REJECTED:
        actions.append(SuggestedAction(
            label="重新运行并批准", mode="manual",
            description="用户拒绝了审批，可以重新运行并批准。",
        ))
    elif fc == FailureClass.APPROVAL_TIMEOUT:
        actions.append(SuggestedAction(
            label="重新提交审批", mode="manual",
            description="审批超时自动拒绝，可以重新提交。",
        ))

    if fc != FailureClass.APPROVAL_REJECTED:
        actions.append(SuggestedAction(
            label="重试整个运行", mode="confirm",
            description="使用原始需求重新启动运行。",
        ))

    return actions


def _can_auto_retry(fc: FailureClass) -> bool:
    return fc in (
        FailureClass.MODEL_ERROR,
        FailureClass.COMMAND_ERROR,
        FailureClass.UNKNOWN_ERROR,
    )


# ---- Persistence ----


def save_failures(thread_id: str, workspace_dir: str | None = None) -> list[FailureRecord]:
    """Classify and persist failures for a run. Returns the list of records."""
    records = classify_run_failures(thread_id, workspace_dir)
    rd = _run_dir(thread_id, workspace_dir)
    _write_json_atomic(rd / "failures.json", [r.model_dump() for r in records])
    if records:
        try:
            from src.api.services.run_state_service import sync_failures_to_task_board

            sync_failures_to_task_board(thread_id, str(_workspace(workspace_dir)), records)
        except Exception:
            pass
    return records


def load_failures(thread_id: str, workspace_dir: str | None = None) -> list[FailureRecord]:
    """Load previously saved failure records."""
    rd = _run_dir(thread_id, workspace_dir)
    path = rd / "failures.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [FailureRecord(**item) for item in data]
    except (json.JSONDecodeError, OSError, TypeError):
        return []
