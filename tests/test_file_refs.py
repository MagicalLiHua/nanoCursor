from io import BytesIO

import pytest

from nanocursor.file_refs import (
    MAX_AT_REF_BYTES,
    FileReference,
    current_file_ref,
    expand_at_refs,
    format_file_ref,
    scan_files_for_at,
)


@pytest.mark.parametrize("text,prefix", [
    ("@", ""),
    ("看看@", ""),
    ("查看：@", ""),
    ("看（@src/", "src/"),
    ("查看 @文档/说明.md", "文档/说明.md"),
    ('查看 @"my file', "my file"),
    ('查看 @"my file.txt"', "my file.txt"),
])
def test_current_reference_supports_empty_directory_and_quoted_prefix(text, prefix):
    ref = current_file_ref(text, len(text))
    assert ref is not None
    assert ref.prefix == prefix
    assert text[ref.start] == "@"
    assert ref.end == len(text)


@pytest.mark.parametrize("text", [
    "owner@README.md", "first.last+tag@README.md", "user-name@README.md",
    "repo/org@main", r"\@README.md", "mail owner@README.md", "@README.md ",
])
def test_email_identifier_escape_and_text_after_token_are_not_current_refs(text):
    assert current_file_ref(text, len(text)) is None


@pytest.mark.parametrize("text,path_start,path_end,prefix", [
    ("请看 @src/main.py，随后继续", "@src/ma", "，", "src/ma"),
    ('请看 @"目录/my file.py"，随后继续', '@"目录/my fi', "，", "目录/my fi"),
])
def test_cursor_in_middle_returns_entire_replacement_range(text, path_start, path_end, prefix):
    cursor = text.index(path_start) + len(path_start)
    assert current_file_ref(text, cursor) == FileReference(
        text.index("@"), text.index(path_end), prefix)


def test_multiple_refs_select_by_cursor_not_last_at_sign():
    text = "看 @first.py 和 @second.py"
    first = current_file_ref(text, text.index(" 和"))
    second = current_file_ref(text, len(text))
    assert first.prefix == "first.py"
    assert second.prefix == "second.py"
    assert current_file_ref(text, 0) is None
    assert current_file_ref(text, -1) is None
    assert current_file_ref(text, len(text) + 1) is None


@pytest.mark.parametrize("path,formatted", [
    ("README.md", "@README.md"),
    ("目录/", "@目录/"),
    ("my file.txt", '@"my file.txt"'),
    ("目录 (1)/说明.md", '@"目录 (1)/说明.md"'),
    ('say "hello".md', '@"say \\"hello\\".md"'),
    (r"literal\name.md", '@"literal\\\\name.md"'),
])
def test_format_round_trips_to_reference(path, formatted):
    assert format_file_ref(path) == formatted
    assert current_file_ref(formatted, len(formatted)).prefix == path


def test_expansion_supports_unicode_spaces_parentheses_and_email_boundary(tmp_path):
    for path in ["README.md", "目录 (1)/文档 说明.md"]:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"body of {path}", encoding="utf-8")
    source = '看看@README.md，参考 @"目录 (1)/文档 说明.md"。邮箱 owner@README.md'
    expanded = expand_at_refs(source, str(tmp_path))
    assert "看看[File: README.md]" in expanded
    assert "body of README.md" in expanded
    assert "body of 目录 (1)/文档 说明.md" in expanded
    assert expanded.endswith("邮箱 owner@README.md")


def test_expansion_preserves_missing_incomplete_and_directory_refs(tmp_path):
    (tmp_path / "folder").mkdir()
    source = '@missing.md @folder/ @"unfinished path'
    assert expand_at_refs(source, str(tmp_path)) == source


def test_expansion_does_not_recursively_expand_file_contents(tmp_path):
    (tmp_path / "one.txt").write_text("@two.txt", encoding="utf-8")
    (tmp_path / "two.txt").write_text("should not attach", encoding="utf-8")
    expanded = expand_at_refs("@one.txt", str(tmp_path))
    assert "@two.txt" in expanded
    assert "should not attach" not in expanded


def test_truncation_is_bytes_and_does_not_split_utf8(tmp_path):
    (tmp_path / "large.txt").write_text("中" * MAX_AT_REF_BYTES, encoding="utf-8")
    expanded = expand_at_refs("@large.txt", str(tmp_path))
    content = expanded.split("```\n", 1)[1].split("\n```", 1)[0]
    assert len(content.encode("utf-8")) <= MAX_AT_REF_BYTES
    assert content == "中" * (MAX_AT_REF_BYTES // 3)
    assert "内容已截断" in expanded
    assert "10 KiB" in expanded
    assert "�" not in expanded


@pytest.mark.parametrize("size", [0, MAX_AT_REF_BYTES - 1, MAX_AT_REF_BYTES])
def test_complete_text_at_or_below_byte_limit_has_no_truncation_notice(tmp_path, size):
    (tmp_path / "text.txt").write_text("a" * size, encoding="utf-8")
    expanded = expand_at_refs("@text.txt", str(tmp_path))
    assert f"```\n{'a' * size}\n```" in expanded
    assert "截断" not in expanded


@pytest.mark.parametrize("data", [b"a\x00b", b"\xff\xfe\x99", b"\x1b[31m binary control"])
def test_binary_or_non_utf8_files_are_not_attached(tmp_path, data):
    (tmp_path / "binary.dat").write_bytes(data)
    expanded = expand_at_refs("@binary.dat", str(tmp_path))
    assert "文件未附加" in expanded
    assert "UTF-8 文本" in expanded
    assert "```" not in expanded


def test_reader_is_bounded_and_closes_file(monkeypatch):
    class Source(BytesIO):
        def read(self, size=-1):
            assert size == MAX_AT_REF_BYTES + 1
            return super().read(size)
    source = Source(b"x" * (MAX_AT_REF_BYTES * 2))
    monkeypatch.setattr("nanocursor.file_refs.os.path.isfile", lambda path: True)
    monkeypatch.setattr("nanocursor.file_refs.open", lambda *args, **kwargs: source, raising=False)
    assert "截断" in expand_at_refs("@text.txt", "/unused")
    assert source.closed


def test_read_failure_keeps_reference_and_explains_why(monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("synthetic permission failure")
    monkeypatch.setattr("nanocursor.file_refs.os.path.isfile", lambda path: True)
    monkeypatch.setattr("nanocursor.file_refs.open", denied, raising=False)
    result = expand_at_refs("查看 @locked.txt", "/unused")
    assert result == "查看 @locked.txt [文件未附加：无法读取]"


def test_scan_lists_one_level_and_keeps_existing_hidden_file_policy(tmp_path):
    (tmp_path / "文档 (1)").mkdir()
    (tmp_path / "文档 (1)" / "说明 文档.md").write_text("test", encoding="utf-8")
    (tmp_path / "README.md").write_text("test", encoding="utf-8")
    (tmp_path / ".hidden").write_text("hidden", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    assert scan_files_for_at("", str(tmp_path)) == ["README.md", "文档 (1)/"]
    assert scan_files_for_at("文档 (1)/", str(tmp_path)) == ["文档 (1)/说明 文档.md"]
    assert scan_files_for_at("read", str(tmp_path)) == ["README.md"]
    assert scan_files_for_at("", str(tmp_path), limit=1) == ["README.md"]
    assert scan_files_for_at("", str(tmp_path), limit=0) == []
    assert scan_files_for_at("missing/", str(tmp_path)) == []
    assert scan_files_for_at(".", str(tmp_path)) == []


def test_existing_absolute_parent_relative_and_symlink_paths_still_work(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("explicit external attachment", encoding="utf-8")
    (workspace / "linked.txt").symlink_to(outside)
    for ref in ["@../outside.txt", format_file_ref(str(outside)), "@linked.txt"]:
        assert "explicit external attachment" in expand_at_refs(ref, str(workspace))
    assert "../outside.txt" in scan_files_for_at("../out", str(workspace))
