from __future__ import annotations

from src.agent.context_pack import ContextPack
from src.api.services import run_state_service
from src.api.services.context_compaction_settings_service import save_context_compaction_settings
from src.api.services.event_store import get_event_store
from src.api.services.model_context_registry_service import ModelContextSpec


def _large_context_pack() -> ContextPack:
    return ContextPack(
        task_summary="请继续完成复杂代码任务，并保留当前验收标准。",
        selected_files=[
            {
                "path": f"src/module_{index}.py",
                "relevance_score": 80,
                "mode": "excerpt",
                "reasons": ["very relevant file"] * 20,
                "excerpt": "def compute():\n    return 'large context'\n" * 30,
            }
            for index in range(24)
        ],
        file_outlines=[
            {
                "path": f"src/module_{index}.py",
                "language": "python",
                "symbols": [{"type": "function", "name": f"fn_{inner}", "lineno": inner} for inner in range(40)],
            }
            for index in range(24)
        ],
        selection_reasons=["large historical selection reason " * 30 for _ in range(20)],
        current_plan=[
            {"id": "inspect", "title": "确认当前任务", "description": "保留当前任务边界。"},
            {"id": "implement", "title": "实现修改", "description": "只修改必要文件。"},
        ],
        tool_policy={"mode": "enforced", "approval_required_levels": ["risky_write"]},
    )


def test_prepare_context_pack_ledger_auto_compacts_hard_pressure(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "auto-context-compact"
    pack = _large_context_pack()
    monkeypatch.setattr(
        run_state_service,
        "get_current_model_context_spec",
        lambda _workspace: ModelContextSpec(
            provider="test",
            model="tiny-window",
            context_window=2_000,
            max_output_tokens=200,
            source="test",
        ),
    )

    state = run_state_service.prepare_context_pack_ledger(
        pack,
        thread_id,
        str(workspace),
        conversation_id="conv-1",
        purpose="test",
        auto_compact=True,
    )

    assert state["context_compaction"]["compacted"] is True
    assert state["context_ledger_before_compaction"]["status"] in {"hard_compact", "emergency"}
    assert state["context_ledger"]["input_tokens"] < state["context_ledger_before_compaction"]["input_tokens"]
    assert "自动上下文压缩摘要" in pack.conversation_summary
    assert pack.context_debug["context_compaction"]["source_section_ids"]

    events = get_event_store().list_events(thread_id, str(workspace))
    assert [event.type for event in events if event.type.startswith("context_compaction_")] == [
        "context_compaction_started",
        "context_compaction_finished",
    ]


def test_prepare_context_pack_ledger_uses_workspace_summary_mode(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "auto-context-compact-llm"
    pack = _large_context_pack()
    save_context_compaction_settings({"summary_mode": "llm"}, workspace)
    modes: list[str] = []
    original_summary_compact = run_state_service.summary_compact_ledger

    def capture_summary_mode(ledger, *, decision=None, mode="deterministic", summarizer=None):
        modes.append(mode)
        result = original_summary_compact(ledger, decision=decision, mode="deterministic", summarizer=summarizer)
        return result.model_copy(update={"mode": mode, "used_llm": mode == "llm"})

    monkeypatch.setattr(run_state_service, "summary_compact_ledger", capture_summary_mode)
    monkeypatch.setattr(
        run_state_service,
        "get_current_model_context_spec",
        lambda _workspace: ModelContextSpec(
            provider="test",
            model="tiny-window",
            context_window=2_000,
            max_output_tokens=200,
            source="test",
        ),
    )

    state = run_state_service.prepare_context_pack_ledger(
        pack,
        thread_id,
        str(workspace),
        conversation_id="conv-1",
        purpose="test",
        auto_compact=True,
    )

    assert modes == ["llm"]
    assert state["context_compaction"]["mode"] == "llm"
    events = get_event_store().list_events(thread_id, str(workspace))
    finished = next(event for event in events if event.type == "context_compaction_finished")
    assert finished.payload["summary_mode"] == "llm"
    assert finished.payload["used_llm"] is True


def test_prepare_context_pack_ledger_skips_low_pressure(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "skip-context-compact"
    pack = ContextPack(task_summary="你好", current_plan=[{"title": "直接回答"}])
    monkeypatch.setattr(
        run_state_service,
        "get_current_model_context_spec",
        lambda _workspace: ModelContextSpec(
            provider="test",
            model="large-window",
            context_window=100_000,
            max_output_tokens=1_000,
            source="test",
        ),
    )

    state = run_state_service.prepare_context_pack_ledger(
        pack,
        thread_id,
        str(workspace),
        conversation_id="conv-1",
        purpose="test",
        auto_compact=True,
    )

    assert "context_ledger" in state
    assert "context_compaction" not in state
    assert get_event_store().list_events(thread_id, str(workspace)) == []
