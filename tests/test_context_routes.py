from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.server import app


def test_context_routes_model_override_preview_and_compact(tmp_path):
    from src.api.run_state import get_workspace, set_active_workspace

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    old_workspace = get_workspace()
    set_active_workspace(str(workspace))
    client = TestClient(app)
    try:
        current = client.get("/api/context/model/current")
        assert current.status_code == 200
        assert current.json()["context_window"] > 0

        override = client.put(
            "/api/context/model/override",
            json={
                "provider": "custom",
                "model": "tiny",
                "context_window": 1_000,
                "max_output_tokens": 100,
            },
        )
        assert override.status_code == 200
        assert override.json()["source"] == "override"

        settings = client.get("/api/context/compaction/settings")
        assert settings.status_code == 200
        assert settings.json()["summary_mode"] == "deterministic"

        updated_settings = client.put("/api/context/compaction/settings", json={"summary_mode": "llm"})
        assert updated_settings.status_code == 200
        assert updated_settings.json()["summary_mode"] == "llm"

        preview = client.post(
            "/api/context/ledger/preview",
            json={
                "provider": "custom",
                "model": "tiny",
                "conversation_id": "conv-1",
                "run_id": "run-1",
                "persist": True,
                "sections": [
                    {
                        "id": "current",
                        "label": "Current",
                        "category": "current",
                        "tokens": 90,
                        "compactible": False,
                        "priority": 100,
                    },
                    {
                        "id": "tool_results",
                        "label": "Tools",
                        "category": "tool",
                        "tokens": 760,
                        "compactible": True,
                        "priority": 20,
                    },
                ],
            },
        )
        assert preview.status_code == 200
        assert preview.json()["status"] in {"hard_compact", "emergency"}

        run_ledger = client.get("/api/context/runs/run-1/ledger")
        assert run_ledger.status_code == 200
        assert run_ledger.json()["run_id"] == "run-1"

        compact = client.post(
            "/api/context/runs/run-1/compact",
            json={"level": "hard", "reason": "test", "strategy": "summary", "summary_mode": "deterministic"},
        )
        assert compact.status_code == 200
        assert compact.json()["strategy"] == "summary"
        assert compact.json()["after_tokens"] < compact.json()["before_tokens"]
    finally:
        set_active_workspace(old_workspace)
