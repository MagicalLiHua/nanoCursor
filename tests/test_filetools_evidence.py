from src.tools.filetools_evidence import build_file_tool_evidence


def test_python_write_file_evidence():
    evidence = build_file_tool_evidence(
        "write_file",
        {"path": "README.md"},
        "Updated README.md (12 bytes)",
    )
    assert evidence is not None
    assert evidence["backend"] == "python"
    assert evidence["operation"] == "write"
    assert evidence["path"] == "README.md"
    assert evidence["changed"] is True
    assert evidence["overwritten"] is True


def test_go_edit_file_evidence_extracts_diff_and_backup():
    output = """成功修改 src/demo.py。使用策略: [行号范围匹配 (Line Range)] (原文件已备份到 src_demo.py.bak.20260607_111528.931994000)
Edit Receipt:
- path: src/demo.py
- strategy: 行号范围匹配 (Line Range)
```diff
--- a/src/demo.py
+++ b/src/demo.py
-old
+new
```"""
    evidence = build_file_tool_evidence("edit_file", {"path": "src/demo.py"}, output)
    assert evidence is not None
    assert evidence["backend"] == "go"
    assert evidence["operation"] == "edit"
    assert evidence["backup_path"] == "src_demo.py.bak.20260607_111528.931994000"
    assert evidence["diff"] is not None
    assert "+new" in evidence["diff"]


def test_error_evidence_records_error_message():
    evidence = build_file_tool_evidence(
        "edit_file",
        {"path": "missing.txt"},
        "Error: File not found: missing.txt",
    )
    assert evidence is not None
    assert evidence["changed"] is False
    assert evidence["error"] == "File not found: missing.txt"


def test_non_file_tool_returns_none():
    assert build_file_tool_evidence("bash", {"command": "ls"}, "ok") is None

