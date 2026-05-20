"""D5 Doctor and system API tests."""

from fastapi.testclient import TestClient


def test_system_version():
    from api_server import app
    client = TestClient(app)
    resp = client.get("/api/system/version")
    assert resp.status_code == 200
    assert "version" in resp.json()


def test_system_paths():
    from api_server import app
    client = TestClient(app)
    resp = client.get("/api/system/paths")
    assert resp.status_code == 200
    data = resp.json()
    assert "project_root" in data
    assert "workspace_dir" in data


def test_system_doctor():
    from api_server import app
    client = TestClient(app)
    resp = client.get("/api/system/doctor")
    assert resp.status_code == 200
    data = resp.json()
    assert "ok" in data
    assert "checks" in data
    assert len(data["checks"]) >= 3


def test_system_doctor_checks_have_ids():
    from api_server import app
    client = TestClient(app)
    resp = client.get("/api/system/doctor")
    for check in resp.json()["checks"]:
        assert "id" in check
        assert "status" in check
        assert "message" in check
        assert check["status"] in ("passed", "warning", "fail")


def test_doctor_script_runs():
    import subprocess
    import sys
    from pathlib import Path
    script = Path(__file__).parent.parent / "scripts" / "doctor.py"
    if script.exists():
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode in (0, 1)  # can pass or have warnings
