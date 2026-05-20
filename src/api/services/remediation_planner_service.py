"""Remediation planner — generates remediation runs from failure records.

R4: Each failure can produce a remediation plan and an optional retry run
that carries the original_thread_id for traceability.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.infra import config as config_module
from src.api.services.failure_classifier_service import (
    FailureClass,
    FailureRecord,
    load_failures,
)


def _workspace(workspace_dir: str | None = None) -> Path:
    return Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()


def plan_remediation(
    failure_id: str,
    thread_id: str,
    workspace_dir: str | None = None,
) -> dict[str, Any] | None:
    """Build a remediation plan for a specific failure.

    Returns a dict with strategy, prompt_prefix, and ready flag for
    creating a remediation run.
    """
    failures = load_failures(thread_id, workspace_dir)
    target = None
    for f in failures:
        if f.failure_id == failure_id:
            target = f
            break
    if target is None:
        return None

    fc = target.failure_class
    evidence = target.evidence

    strategy_map = {
        FailureClass.TEST_FAILURE: "fix_test_failure",
        FailureClass.ENVIRONMENT_ERROR: "fix_environment",
        FailureClass.COMMAND_ERROR: "fix_command_error",
        FailureClass.MODEL_ERROR: "retry_with_backoff",
        FailureClass.PATCH_ERROR: "rollback_and_retry",
        FailureClass.WORKSPACE_ERROR: "fix_workspace",
        FailureClass.TOOL_POLICY_BLOCKED: "adjust_policy",
        FailureClass.APPROVAL_REJECTED: "wait_for_approval",
        FailureClass.APPROVAL_TIMEOUT: "resubmit_approval",
        FailureClass.UNKNOWN_ERROR: "retry_with_backoff",
    }
    strategy = strategy_map.get(fc, "retry_or_inspect")

    prompt_prefix_map = {
        "fix_test_failure": "以下测试失败，请修复代码使测试通过：",
        "fix_environment": "运行环境缺少依赖，请安装或配置：",
        "fix_command_error": "以下命令执行失败，请修复并重新运行：",
        "retry_with_backoff": "上次运行因 API 错误失败，请重试：",
        "rollback_and_retry": "文件修改出错，请回滚并重试：",
        "fix_workspace": "工作区访问出错，请检查路径和权限：",
        "retry_or_inspect": "上次运行失败，请分析原因并修复：",
    }

    prompt_prefix = prompt_prefix_map.get(strategy, "请修复以下问题：")
    evidence_text = evidence.get("event_content", evidence.get("output", str(evidence)))

    return {
        "failure_id": failure_id,
        "original_thread_id": thread_id,
        "failure_class": fc.value,
        "strategy": strategy,
        "prompt_prefix": prompt_prefix,
        "evidence_text": evidence_text[:1000],
        "auto_retry": target.can_auto_retry,
    }


def create_remediation_run(
    thread_id: str,
    failure_id: str,
    mode: str = "manual",
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Create a remediation (retry) run based on a failure record.

    Returns basic info including a retry_thread_id. The actual run creation
    is delegated to the existing retry_run endpoint logic.
    """
    plan = plan_remediation(failure_id, thread_id, workspace_dir)
    if plan is None:
        return {"created": False, "reason": "failure_id not found"}

    retry_thread_id = f"remediation_{uuid.uuid4().hex[:12]}"

    # Build a remediation prompt
    prompt = f"{plan['prompt_prefix']}\n\n{plan['evidence_text']}"

    return {
        "created": True,
        "retry_thread_id": retry_thread_id,
        "original_thread_id": thread_id,
        "failure_id": failure_id,
        "strategy": plan["strategy"],
        "auto_retry": plan["auto_retry"],
        "prompt": prompt,
        "mode": "remediation",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
