import asyncio
import shlex
import sys

import pytest

from nanocursor.agent import ToolResultEvent
from nanocursor.tools.bash import Bash, Params as BashParams, MAX_CAPTURE_BYTES
from nanocursor.tools.grep import Grep, Params as GrepParams
from nanocursor.tools.read_file import ReadFile, Params as ReadParams
from nanocursor.tools.base import MAX_OUTPUT_CHARS, ToolCallComplete
from test_execution_boundaries import ScriptClient, agent, drive, registry_for


def command(code):
    return shlex.join([sys.executable, "-c", code])


@pytest.mark.asyncio
async def test_bash_drains_large_output_and_preserves_head_tail(tmp_path):
    completed = tmp_path / "completed"
    result = await asyncio.wait_for(Bash().execute(BashParams(command=command(
        "import sys,pathlib; "
        f"sys.stdout.write('HEAD' + 'x' * {MAX_CAPTURE_BYTES * 3} + 'TAIL'); sys.stdout.flush(); "
        f"pathlib.Path({str(completed)!r}).write_text('done')"
    ))), 5)
    assert not result.is_error and completed.read_text() == "done"
    assert "\n\nHEAD" in result.output and result.output.endswith("TAIL")
    assert result.output.startswith("[Output truncated")
    assert len(result.output) < MAX_CAPTURE_BYTES + 200


@pytest.mark.asyncio
async def test_bash_small_utf8_output_is_exact():
    text = "中文 🙂\n" * 1000
    result = await Bash().execute(BashParams(command=command(f"import sys; sys.stdout.write({text!r})")))
    assert not result.is_error and result.output == text


@pytest.mark.asyncio
async def test_bash_output_limit_keeps_exit_status():
    result = await Bash().execute(BashParams(command=command(
        f"import sys; print('x' * {MAX_CAPTURE_BYTES * 2}); sys.exit(7)"
    )))
    assert "Output truncated" in result.output and "Exit code 7" in result.output


@pytest.mark.asyncio
async def test_bash_large_output_still_times_out():
    result = await asyncio.wait_for(Bash().execute(BashParams(command=command(
        f"import sys,time; print('x' * {MAX_CAPTURE_BYTES * 2}, flush=True); time.sleep(30)"
    ), timeout=1)), 4)
    assert result.is_error and "timed out" in result.output


@pytest.mark.asyncio
async def test_cancel_continuous_output_drains_pipe_and_reaps_process(tmp_path):
    from test_execution_boundaries import _process_is_running
    ready = tmp_path / "pid"
    task = asyncio.create_task(Bash().execute(BashParams(command=command(
        f"import os,pathlib; pathlib.Path({str(ready)!r}).write_text(str(os.getpid()))\n"
        "while True: os.write(1, b'x' * 65536)"
    ))))
    try:
        for _ in range(100):
            if ready.exists():
                break
            await asyncio.sleep(0.01)
        assert ready.exists()
        await asyncio.sleep(0.05)
        pid = int(ready.read_text())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 4)
        assert not _process_is_running(pid)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [100000, MAX_CAPTURE_BYTES * 2])
async def test_ui_receives_preview_not_full_output(tmp_path, size):
    bash = Bash()
    client = ScriptClient([ToolCallComplete("large", "Bash", {"command": command(f"print('x' * {size})")})])
    a = agent(tmp_path, client, registry_for(bash))
    conv, events = await drive(a)
    output = next(e.output for e in events if isinstance(e, ToolResultEvent))
    assert "persisted-output" in output and len(output) < MAX_OUTPUT_CHARS
    assert output == next(tr.content for msg in conv.history for tr in msg.tool_results)
    if size > MAX_CAPTURE_BYTES:
        assert "Output truncated" in output


@pytest.mark.asyncio
async def test_read_file_rejects_oversized_content_before_full_read(tmp_path, monkeypatch):
    monkeypatch.setattr("nanocursor.tools.file_io.MAX_FILE_BYTES", 32)
    path = tmp_path / "large.txt"
    path.write_text("x" * 100)
    result = await ReadFile().execute(ReadParams(file_path=str(path), limit=1))
    assert result.is_error and "read limit" in result.output


@pytest.mark.asyncio
async def test_grep_limits_output_and_reports_skipped_files(tmp_path, monkeypatch):
    path = tmp_path / "large.txt"
    path.write_text("match\n" * 10000)
    result = await Grep().execute(GrepParams(path=str(tmp_path), pattern="match"))
    assert "truncated" in result.output and len(result.output) < MAX_OUTPUT_CHARS + 100
    monkeypatch.setattr("nanocursor.tools.file_io.MAX_FILE_BYTES", 32)
    result = await Grep().execute(GrepParams(path=str(tmp_path), pattern="match"))
    assert "Skipped 1 file" in result.output
