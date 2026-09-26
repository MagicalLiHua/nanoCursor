"""Install an artifact into an isolated uv tool environment and exercise its CLI.

Uses only a loopback model fixture, never a real API credential. For an sdist,
install an extracted source tree and move it afterwards to catch editable leaks.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def setup_in_terminal(binary: Path, environment: dict, directory: Path, endpoint: str) -> None:
    import pty
    import select
    import signal

    pid, master = pty.fork()
    if pid == 0:
        os.chdir(directory)
        os.execve(str(binary), [str(binary), "setup"], environment)
    transcript = b""
    reaped = False
    try:
        for marker, answer in [(b"Provider [1]:", "4"), (b"Profile name [custom]:", "fixture"),
                               (b"Base URL:", endpoint), (b"Protocol (", ""),
                               (b"Model ID (", "fixture"), (b"Credential method", "1"),
                               (b"API key (hidden):", "fixture-key"), (b"Test connection now?", "n"),
                               (b"Save and use as default?", "y")]:
            pending = b""
            deadline = time.monotonic() + 15
            while marker not in pending:
                if time.monotonic() > deadline:
                    raise AssertionError(f"Setup did not reach {marker!r}")
                if select.select([master], [], [], 1)[0]:
                    pending += os.read(master, 65536)
            transcript += pending
            os.write(master, (answer + "\n").encode())
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    transcript += os.read(master, 65536)
                except OSError:
                    pass
            child, status = os.waitpid(pid, os.WNOHANG)
            if child:
                reaped = True
                assert os.waitstatus_to_exitcode(status) == 0, transcript.decode(errors="replace")
                assert b"fixture-key" not in transcript, "Secret input was echoed"
                return
        raise AssertionError("Setup did not exit")
    finally:
        os.close(master)
        if not reaped:
            os.kill(pid, signal.SIGTERM)
            os.waitpid(pid, 0)


class ModelHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert self.path == "/v1/chat/completions"
        assert self.headers.get("Authorization") == "Bearer fixture-key"
        assert body["model"] == "fixture"
        if not body.get("stream"):
            payload = json.dumps({"choices": [{"message": {"content": "OK"}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        messages = body["messages"]
        results = [m for m in messages if m["role"] == "tool"]
        if not results:
            delta = {"tool_calls": [{"index": 0, "id": "read-one", "type": "function",
                     "function": {"name": "ReadFile", "arguments": json.dumps({"file_path": "input.txt"})}}]}
        elif len(results) == 1:
            assert "install-smoke" in str(results[0]["content"]), results[0]
            delta = {"tool_calls": [{"index": 0, "id": "write-one", "type": "function",
                     "function": {"name": "WriteFile", "arguments": json.dumps({"file_path": "output.txt", "content": "install-smoke-ok\n"})}}]}
        else:
            assert "Successfully wrote" in str(results[-1]["content"]), results[-1]
            delta = {"content": "INSTALL_SMOKE_OK"}
        def chunk(delta, finish=None):
            return {"id": "fixture", "object": "chat.completion.chunk", "created": 1, "model": "fixture",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        payload = "".join("data: " + json.dumps(item) + "\n\n" for item in [chunk(delta), chunk({}, "tool_calls" if "tool_calls" in delta else "stop")])
        payload += "data: [DONE]\n\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload.encode())))
        self.end_headers()
        self.wfile.write(payload.encode())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--python", default="3.12")
    parser.add_argument("--unconstrained", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    artifact = args.artifact.resolve()
    uv = shutil.which("uv")
    if not uv:
        parser.error("uv is required")
    with tempfile.TemporaryDirectory(prefix="nanocursor-install-") as folder:
        temp = Path(folder).resolve()
        env = {k: v for k, v in os.environ.items() if not k.endswith("API_KEY") and k not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "NANOCURSOR_REMOTE_MEMORY_DIR"}}
        env.update(UV_TOOL_DIR=str(temp / "tools"), UV_TOOL_BIN_DIR=str(temp / "bin"),
                   UV_CACHE_DIR=str(temp / "cache"), NANOCURSOR_HOME=str(temp / "user"))
        env["PATH"] = str(temp / "bin") + os.pathsep + env.get("PATH", "")
        package = temp / artifact.name
        shutil.copy2(artifact, package)
        source = None
        if package.name.endswith(".tar.gz"):
            with tarfile.open(package) as archive:
                for member in archive.getmembers():
                    destination = (temp / member.name).resolve()
                    assert destination.is_relative_to(temp) and (member.isfile() or member.isdir())
                archive.extractall(temp, filter="data")
            source = next(p for p in temp.glob("nanocursor-*") if p.is_dir())
            package = source
        command = [uv, "tool", "install", "--no-config", "--python", args.python,
                   "--build-constraints", str(root / "packaging/build-constraints.txt")]
        if not args.unconstrained:
            command += ["--constraints", str(root / "packaging/runtime-constraints.txt")]
        subprocess.run([*command, str(package)], cwd=temp, env=env, check=True)
        if source:
            source.rename(temp / "source-no-longer-at-install-path")
        else:
            package.unlink()
        binary = temp / "bin" / ("nanocursor.exe" if os.name == "nt" else "nanocursor")
        python = temp / "tools/nanocursor" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        def run(arguments, cwd=temp, *, success=True):
            result = subprocess.run([str(binary), *arguments], cwd=cwd, env=env, capture_output=True, text=True, timeout=30)
            assert (result.returncode == 0) == success, (arguments, result.stdout, result.stderr)
            return result
        run(["--help"])
        run(["--version"])
        assert not (temp / ".nanocursor").exists() and not (temp / "user").exists()
        missing = run(["doctor", "--json"], success=False)
        assert not json.loads(missing.stdout)["ok"]
        # Exercise the installed setup service and resource loader, outside source.
        code = '''
import importlib.resources
import pathlib
import nanocursor
assert "tools/nanocursor" in str(pathlib.Path(nanocursor.__file__).resolve())
assert importlib.resources.files("nanocursor").joinpath("styles.tcss").is_file()
from nanocursor.agents.loader import AgentLoader
assert AgentLoader(".").load_all()
'''
        server = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = f"http://127.0.0.1:{server.server_port}/v1"
            subprocess.run([str(python), "-c", code.replace("ENDPOINT", endpoint)], cwd=temp, env=env, check=True, timeout=30)
            env.update(DEEPSEEK_API_KEY="fixture-key", DEEPSEEK_BASE_URL=endpoint, DEEPSEEK_MODEL="fixture")
            environment_report = json.loads(run(["doctor", "--network", "--json"]).stdout)
            assert environment_report["ok"] and "env:deepseek" in json.dumps(environment_report)
            assert "fixture-key" not in json.dumps(environment_report)
            assert not (temp / "user").exists()
            environment_project = temp / "environment-only project"
            environment_project.mkdir()
            (environment_project / "input.txt").write_text("install-smoke\n")
            environment_run = run(["--mode", "acceptEdits", "-p", "Read input.txt and write output.txt"], cwd=environment_project)
            assert "INSTALL_SMOKE_OK" in environment_run.stdout
            assert (environment_project / "output.txt").read_text() == "install-smoke-ok\n"
            assert not (temp / "user/config.yaml").exists() and not (temp / "user/credentials.json").exists()
            for variable in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"):
                del env[variable]
            setup_in_terminal(binary, env, temp, endpoint)
            report = json.loads(run(["doctor", "--network", "--json"]).stdout)
            assert report["ok"], report
            assert "fixture-key" not in json.dumps(report)
            # Shell startup files must not be needed to resolve a saved key.
            for shell, flags in (("bash", ["--noprofile", "--norc"]), ("zsh", ["-f"])):
                executable = shutil.which(shell)
                if executable:
                    shell_result = subprocess.run([executable, *flags, "-c", "nanocursor doctor --json"],
                                                  cwd=temp, env=env, capture_output=True, text=True, timeout=30)
                    assert shell_result.returncode == 0, shell_result.stderr
                    assert json.loads(shell_result.stdout)["ok"]
            for index, name in enumerate(("project A", "中文 B")):
                project = temp / name
                project.mkdir()
                (project / "input.txt").write_text("install-smoke\n")
                target = ["--cwd", str(project)] if index else []
                result = run([*target, "--mode", "acceptEdits", "-p", "Read input.txt and write output.txt"],
                             cwd=temp if index else project)
                assert "INSTALL_SMOKE_OK" in result.stdout, result.stdout
                assert (project / "output.txt").read_text() == "install-smoke-ok\n"
            assert not (temp / "output.txt").exists()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        print(f"Installation smoke passed: {artifact.name}; independent command, environment-only startup, setup, doctor, workspaces, read/write tools")


if __name__ == "__main__":
    main()
