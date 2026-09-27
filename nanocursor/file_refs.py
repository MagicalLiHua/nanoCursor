"""Parse and expand explicit file references used by the terminal composer.

Paths are resolved relative to the active workspace, with existing absolute
and parent-relative path support. This is an attachment reader, not an Agent
tool execution or a replacement for its permission checks.
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass
import os
import re


MAX_AT_REF_BYTES = 10 * 1024
_SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".nanocursor", "build", ".gradle"}
_PLAIN_PATH_RE = re.compile(r"[\w./\-]+", re.UNICODE)
# A preceding ASCII address/identifier character makes this an email or other
# token, not a file reference. Chinese prose and punctuation may precede @.
_AT_START_RE = re.compile(r"(?<![A-Za-z0-9_./%+~\\\-])@")


@dataclass(frozen=True)
class FileReference:
    start: int
    end: int
    prefix: str


@dataclass(frozen=True)
class _ParsedReference:
    start: int
    end: int
    path_start: int
    path_end: int
    quoted: bool
    complete: bool


def _decode_quoted(value: str) -> str:
    # Only quote/backslash escaping is special; a literal \n in a filename must
    # not turn into a newline through JSON or shell parsing.
    return re.sub(r'\\(["\\])', r'\1', value)


def _references(text: str):
    consumed = 0
    for match in _AT_START_RE.finditer(text):
        start = match.start()
        if start < consumed:
            continue
        path_start = start + 1
        if text[path_start:path_start + 1] == '"':
            path_start += 1
            index = path_start
            closed = False
            while index < len(text) and text[index] not in "\r\n":
                if text[index] == '"':
                    closed = True
                    break
                if text[index] == "\\" and text[index + 1:index + 2] in ('"', "\\"):
                    index += 2
                else:
                    index += 1
            end = index + 1 if closed else index
            yield _ParsedReference(start, end, path_start, index, True, closed)
        else:
            path_match = _PLAIN_PATH_RE.match(text, path_start)
            end = path_match.end() if path_match else path_start
            yield _ParsedReference(start, end, path_start, end, False, end > path_start)
        consumed = end


def current_file_ref(text: str, cursor_offset: int) -> FileReference | None:
    """Return the reference containing the cursor, including its full range."""
    if not 0 <= cursor_offset <= len(text):
        return None
    for ref in _references(text):
        if ref.start < cursor_offset <= ref.end:
            prefix = text[ref.path_start:max(ref.path_start, min(cursor_offset, ref.path_end))]
            if ref.quoted:
                prefix = _decode_quoted(prefix)
            return FileReference(ref.start, ref.end, prefix)
    return None


def format_file_ref(path: str) -> str:
    """Quote paths that cannot be represented by a plain @path token."""
    if _PLAIN_PATH_RE.fullmatch(path):
        return "@" + path
    escaped = path.replace("\\", "\\\\").replace('"', '\\"')
    return '@"' + escaped + '"'


def scan_files_for_at(prefix: str, work_dir: str, limit: int = 10) -> list[str]:
    """List matching entries in one directory; never recursively scan a tree."""
    if limit <= 0:
        return []
    directory = os.path.dirname(prefix)
    base = os.path.join(work_dir, directory) if directory else work_dir
    name_prefix = os.path.basename(prefix).lower()
    matches = []
    try:
        for entry in sorted(os.listdir(base)):
            if entry in _SKIP_DIRS or entry.startswith("."):
                continue
            if not entry.lower().startswith(name_prefix):
                continue
            path = os.path.join(directory, entry) if directory else entry
            if os.path.isdir(os.path.join(base, entry)):
                path += "/"
            matches.append(path)
            if len(matches) >= limit:
                break
    except (OSError, ValueError):
        pass
    return matches


def _expand_reference(raw: str, path: str, work_dir: str) -> str:
    full_path = os.path.join(work_dir, path)
    if not os.path.isfile(full_path):
        return raw
    try:
        with open(full_path, "rb") as source:
            data = source.read(MAX_AT_REF_BYTES + 1)
    except OSError:
        return f"{raw} [文件未附加：无法读取]"
    truncated = len(data) > MAX_AT_REF_BYTES
    data = data[:MAX_AT_REF_BYTES]
    if any(byte < 32 and byte not in (9, 10, 12, 13) for byte in data):
        return f"{raw} [文件未附加：不是可读取的 UTF-8 文本]"
    try:
        # A byte cap can split a valid UTF-8 code point. On truncated content,
        # leave only that incomplete final code point out of the attachment.
        decoder = codecs.getincrementaldecoder("utf-8")()
        content = decoder.decode(data, final=not truncated)
    except UnicodeDecodeError:
        return f"{raw} [文件未附加：不是可读取的 UTF-8 文本]"
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    result = f"[File: {path}]\n```\n{content}\n```"
    if truncated:
        result += "\n[内容已截断：仅附加前 10 KiB，请使用 ReadFile 查看后续内容。]"
    return result


def expand_at_refs(text: str, work_dir: str) -> str:
    """Attach bounded text for complete references, preserving other input."""
    parts = []
    position = 0
    for ref in _references(text):
        if not ref.complete:
            continue
        raw = text[ref.start:ref.end]
        path = text[ref.path_start:ref.path_end]
        if ref.quoted:
            path = _decode_quoted(path)
        parts.append(text[position:ref.start])
        parts.append(_expand_reference(raw, path, work_dir))
        position = ref.end
    parts.append(text[position:])
    return "".join(parts)
