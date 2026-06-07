from __future__ import annotations


def _workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")
    return workspace


def test_memory_selection_is_explainable_scoped_and_budgeted(tmp_path):
    from src.api.services.memory_governance_service import create_memory_record
    from src.api.services.memory_selection_service import select_memories

    workspace = _workspace(tmp_path)
    (workspace / "AGENTS.md").write_text(
        "# Project rules\nUse `python -m pytest -q` for backend verification.\n",
        encoding="utf-8",
    )
    create_memory_record(
        str(workspace),
        scope="conversation",
        conversation_id="conv-a",
        kind="decision",
        content="Continue fixing the backend verification command.",
        source="user",
        importance=8,
    )
    create_memory_record(
        str(workspace),
        scope="conversation",
        conversation_id="conv-b",
        kind="decision",
        content="Unrelated private conversation decision.",
        source="user",
        importance=10,
    )

    result = select_memories(
        str(workspace),
        prompt="What backend verification command should I use?",
        conversation_id="conv-a",
        selected_files=["app.py"],
        budget_tokens=220,
        persist_audit=False,
    )

    assert result["selected"]
    assert result["budget"]["used_tokens_estimate"] <= 220
    assert all(item["reasons"] for item in result["selected"])
    assert any(item["source"] == "rule_file" for item in result["selected"])
    assert all(item.get("conversation_id") != "conv-b" for item in result["selected"])
    assert any(item["reason"] == "conversation scope mismatch" for item in result["omitted"])


def test_stale_or_disabled_memory_is_not_selected(tmp_path):
    from src.api.services.memory_governance_service import create_memory_record, update_memory_record
    from src.api.services.memory_selection_service import select_memories

    workspace = _workspace(tmp_path)
    stale = create_memory_record(
        str(workspace),
        scope="file",
        file_path="app.py",
        kind="project_fact",
        content="app.py returns the old value.",
        source="user",
    )
    disabled = create_memory_record(
        str(workspace),
        scope="workspace",
        kind="workflow_note",
        content="Use the old verification command.",
        source="user",
    )
    update_memory_record(str(workspace), disabled["id"], status="disabled")
    (workspace / "app.py").write_text("def run():\n    return 'new'\n", encoding="utf-8")

    result = select_memories(
        str(workspace),
        prompt="Check app.py and verification command",
        selected_files=["app.py"],
        persist_audit=False,
    )

    selected_ids = {item["id"] for item in result["selected"]}
    assert stale["id"] not in selected_ids
    assert disabled["id"] not in selected_ids
    omitted_ids = {item["id"] for item in result["omitted"]}
    assert {stale["id"], disabled["id"]} <= omitted_ids


def test_context_pack_consumes_governed_memory_selection(tmp_path):
    from src.api.services.context_service import build_context_pack
    from src.api.services.memory_governance_service import create_memory_record

    workspace = _workspace(tmp_path)
    created = create_memory_record(
        str(workspace),
        scope="workspace",
        kind="workflow_note",
        content="Backend verification uses pytest against app.py.",
        source="user",
        confidence=0.95,
        importance=9,
    )

    pack = build_context_pack(
        prompt="Verify the backend app.py change with pytest",
        workspace_dir=str(workspace),
        execution_plan={"strategy": "bug_fix"},
    )
    data = pack.to_dict()

    assert any(item["id"] == created["id"] for item in data["selected_memories"])
    assert data["memory_budget"]["selected_count"] >= 1
    assert data["context_debug"]["memory_inputs"]["selected_memory_count"] >= 1
    assert "受控记忆" in pack.to_text()


def test_context_pack_records_skill_selection_and_omissions(tmp_path):
    from src.api.services.context_service import build_context_pack
    from src.api.services.skill_registry_service import import_skill, set_skill_enabled

    workspace = _workspace(tmp_path)
    import_skill(
        "Python Dev",
        "# Python Dev\n\nUse pytest and small changes.",
        str(workspace),
        skill_json={
            "id": "python-dev",
            "triggers": ["python", "pytest"],
            "agent_roles": ["coder", "tester"],
            "tool_permissions": ["read_only", "safe_write", "shell_safe"],
        },
    )
    import_skill(
        "API Review",
        "# API Review\n\nReview API contracts.",
        str(workspace),
        skill_json={"id": "api-review", "triggers": ["api"]},
    )
    set_skill_enabled("skill.api-review", False, str(workspace))

    pack = build_context_pack(
        prompt="请用 python 补 pytest",
        workspace_dir=str(workspace),
        team=[{"role": "coder"}],
        execution_plan={"strategy": "bug_fix", "capabilities": []},
    )
    data = pack.to_dict()

    assert data["selected_skills"] == ["skill.python-dev"]
    assert data["selected_skill_details"][0]["selection_reasons"]
    assert data["skill_budget"]["selected_count"] == 1
    assert any(item["id"] == "skill.api-review" and item["reason"] == "skill disabled" for item in data["omitted_skills"])
    assert "启用 Skills" in pack.to_text()


def test_context_pack_skips_skills_for_lead_direct_reply(tmp_path):
    from src.api.services.context_service import build_context_pack
    from src.api.services.skill_registry_service import import_skill

    workspace = _workspace(tmp_path)
    import_skill(
        "Python Dev",
        "# Python Dev\n\nUse pytest and small changes.",
        str(workspace),
        skill_json={"id": "python-dev", "triggers": ["python"]},
    )

    pack = build_context_pack(
        prompt="python 是什么？",
        workspace_dir=str(workspace),
        execution_plan={"strategy": "lead_direct_reply", "capabilities": ["skill.python-dev"]},
    )
    data = pack.to_dict()

    assert data["selected_skills"] == []
    assert data["selected_skill_details"] == []
    assert data["skill_budget"]["skipped"] == "lead_direct_reply"
