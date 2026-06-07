from src.tools.bash import run_bash


def test_run_bash_pytest_supports_src_layout_without_install(tmp_path):
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

    result = run_bash("python -m pytest tests/ -q", tmp_path)

    assert "1 passed" in result


def test_run_bash_cd_pytest_supports_src_layout_without_install(tmp_path):
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

    result = run_bash(f"cd {tmp_path} && python -m pytest tests/ -q 2>&1", tmp_path)

    assert "1 passed" in result
