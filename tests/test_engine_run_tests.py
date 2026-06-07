from src.agent import engine
from src.infra import config as config_module


def test_run_tests_reports_success_count_for_pytest(tmp_path, monkeypatch):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_sample.py").write_text(
        "def test_sample():\n"
        "    assert 1 + 1 == 2\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "WORKSPACE_DIR", str(tmp_path))

    result = engine.run_tests("tests/test_sample.py")

    assert "Command: python -m pytest tests/test_sample.py" in result
    assert "All 1 tests passed" in result


def test_run_tests_supports_src_layout_without_install(tmp_path, monkeypatch):
    src_pkg = tmp_path / "src" / "tiny_pkg"
    src_pkg.mkdir(parents=True)
    (src_pkg / "__init__.py").write_text("def hello():\n    return 'hi'\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_pkg.py").write_text(
        "from tiny_pkg import hello\n\n"
        "def test_hello():\n"
        "    assert hello() == 'hi'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "WORKSPACE_DIR", str(tmp_path))

    result = engine.run_tests()

    assert "Command: python -m pytest tests/ -v --tb=short" in result
    assert "All 1 tests passed" in result


def test_parse_test_summary_handles_success_only_line():
    assert engine._parse_test_summary("=== 3 passed in 0.01s ===") == (
        3,
        0,
        0,
        "=== 3 passed in 0.01s ===",
    )
