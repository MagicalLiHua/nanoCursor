import pytest

from nanocursor.conversation import Message, ToolUseBlock
from nanocursor.memory.session import Session, SessionManager, make_compact_boundary


def broken_session(tmp_path):
    manager = SessionManager(str(tmp_path))
    session = manager.create()
    session.append(Message("user", "First request"))
    session.append(Message("assistant", "First answer"))
    session.append(Message("assistant", "Pending tool", tool_uses=[ToolUseBlock("pending", "ReadFile", {"file_path": "x"})]))
    session.close()
    path = tmp_path / ".nanocursor/sessions" / f"{session.session_id}.jsonl"
    return manager, session.session_id, path


def test_recovered_history_survives_append_and_second_resume(tmp_path):
    manager, session_id, path = broken_session(tmp_path)
    original = path.read_bytes()
    result = manager.resume(session_id)
    assert [m.content for m in result.messages] == ["First request", "First answer"]
    assert path.read_bytes().startswith(original)
    assert not result.session.load_approval_context().complete
    result.session.append(Message("user", "New request"))
    result.session.append(Message("assistant", "New answer"))
    result.session.close()
    repaired = path.read_bytes()
    second = manager.resume(session_id)
    assert [m.content for m in second.messages] == ["First request", "First answer", "New request", "New answer"]
    assert second.session.meta.message_count == 4
    second.session.close()
    assert path.read_bytes() == repaired
    assert path.read_text().count('"type": "history_boundary"') == 1


@pytest.mark.parametrize("tail", [b'{"type":', b'\xff\xfe', b'\n{broken json}\n'])
def test_corrupt_tail_gets_a_separate_boundary_without_rewriting_bytes(tmp_path, tail):
    manager = SessionManager(str(tmp_path))
    session = manager.create()
    session.append(Message("user", "keep me"))
    session.close()
    path = tmp_path / ".nanocursor/sessions" / f"{session.session_id}.jsonl"
    with path.open("ab") as stream:
        stream.write(tail)
    original = path.read_bytes()
    result = manager.resume(session.session_id)
    assert [m.content for m in result.messages] == ["keep me"]
    assert path.read_bytes().startswith(original)
    result.session.append(Message("assistant", "new"))
    result.session.close()
    second = manager.resume(session.session_id)
    assert [m.content for m in second.messages] == ["keep me", "new"]
    assert not second.session.load_approval_context().complete
    second.session.close()


def test_valid_json_without_last_newline_can_be_extended(tmp_path):
    manager = SessionManager(str(tmp_path))
    session = manager.create()
    session.append(Message("user", "last line"))
    session.close()
    path = tmp_path / ".nanocursor/sessions" / f"{session.session_id}.jsonl"
    path.write_bytes(path.read_bytes().rstrip(b"\n"))
    result = manager.resume(session.session_id)
    result.session.append(Message("assistant", "new line"))
    result.session.close()
    second = manager.resume(session.session_id)
    assert [m.content for m in second.messages] == ["last line", "new line"]
    second.session.close()
    assert "history_boundary" not in path.read_text()


def test_boundary_write_failure_never_returns_a_writable_session(tmp_path, monkeypatch):
    manager, session_id, path = broken_session(tmp_path)
    original = path.read_bytes()
    handles = []

    def fail_reset(self, messages):
        handles.append(self._file)
        raise OSError("disk full")

    monkeypatch.setattr(Session, "reset_history", fail_reset)
    with pytest.raises(OSError, match="disk full"):
        manager.resume(session_id)
    assert handles and handles[0].closed
    assert path.read_bytes() == original


def test_older_compact_boundary_remains_authoritative(tmp_path):
    manager, session_id, path = broken_session(tmp_path)
    session = manager.resume(session_id).session
    session.append_record(make_compact_boundary("summary", [Message("user", "retained tail")]))
    session.append(Message("assistant", "new answer"))
    session.close()
    before = path.read_bytes()
    result = manager.resume(session_id)
    assert "summary" in result.messages[0].content
    assert [m.content for m in result.messages[1:]] == ["retained tail", "new answer"]
    assert path.read_bytes() == before
    result.session.close()


def test_meta_failure_after_boundary_is_recoverable(tmp_path, monkeypatch):
    from nanocursor.memory.session import SessionMeta

    manager, session_id, path = broken_session(tmp_path)
    original_save = SessionMeta.save

    def fail_save(self, path):
        raise OSError("metadata unavailable")

    monkeypatch.setattr(SessionMeta, "save", fail_save)
    with pytest.raises(OSError, match="metadata unavailable"):
        manager.resume(session_id)
    monkeypatch.setattr(SessionMeta, "save", original_save)
    result = manager.resume(session_id)
    assert [m.content for m in result.messages] == ["First request", "First answer"]
    assert result.session.meta.message_count == 2
    assert path.read_text().count('"type": "history_boundary"') == 1
    result.session.close()


@pytest.mark.parametrize("kind", ["message", "compact", "reset"])
def test_metadata_error_identifies_committed_jsonl_and_resume_repairs_it(tmp_path, monkeypatch, kind):
    from nanocursor.memory.session import SessionMeta, SessionMetadataError

    manager = SessionManager(str(tmp_path))
    session = manager.create()
    original_save = SessionMeta.save
    metadata_failure = OSError("metadata full")

    def fail_save(self, path):
        raise metadata_failure

    monkeypatch.setattr(SessionMeta, "save", fail_save)
    with pytest.raises(SessionMetadataError, match="Session records were saved") as caught:
        if kind == "message":
            session.append(Message("user", "committed message"))
        elif kind == "compact":
            session.append_record(make_compact_boundary("committed summary", [Message("user", "retained")]))
        else:
            session.reset_history([Message("user", "committed rewind")])
    assert caught.value.__cause__ is metadata_failure
    if kind in {"message", "reset"}:
        assert session.meta.message_count == 1
    session.close()
    monkeypatch.setattr(SessionMeta, "save", original_save)
    result = manager.resume(session.session_id)
    try:
        if kind == "message":
            assert result.messages == [Message("user", "committed message")]
        elif kind == "compact":
            assert "committed summary" in result.messages[0].content
            assert result.messages[1:] == [Message("user", "retained")]
        else:
            assert result.messages == [Message("user", "committed rewind")]
        assert result.session.meta.message_count == len(result.messages)
    finally:
        result.session.close()


@pytest.mark.parametrize("kind", ["message", "record"])
@pytest.mark.parametrize("phase", ["write", "flush"])
def test_jsonl_write_or_flush_failure_is_not_reported_as_committed_metadata_error(tmp_path, kind, phase):
    from datetime import datetime, timezone
    from io import StringIO
    from nanocursor.memory.session import RecordType, SessionMeta, SessionMetadataError, SessionRecord

    class BrokenFile(StringIO):
        def write(self, content):
            if phase == "write":
                raise OSError("JSONL write failed")
            return super().write(content)

        def flush(self):
            if phase == "flush":
                raise OSError("JSONL flush failed")
            return super().flush()

    file = BrokenFile()
    session = Session("fixture", file, SessionMeta("fixture"), tmp_path)
    with pytest.raises(OSError, match=f"JSONL {phase} failed") as caught:
        if kind == "message":
            session.append(Message("user", "not acknowledged"))
        else:
            session.append_record(SessionRecord(RecordType.USER, "not acknowledged", datetime.now(timezone.utc)))
    assert not isinstance(caught.value, SessionMetadataError)
    assert session.meta.message_count == 0
    assert not (tmp_path / "fixture.meta").exists()
    file.close()
