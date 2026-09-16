from __future__ import annotations

import re
import shlex

_DANGEROUS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"rm\s+-[a-z]*r[a-z]*f[a-z]*\s+/\s*$"), "递归强制删除根目录"),
    (re.compile(r"mkfs\."), "格式化磁盘"),
    (re.compile(r"dd\s+if=.*of=/dev/"), "直接写磁盘设备"),
    (re.compile(r"chmod\s+-R\s+777\s+/"), "递归修改根目录权限"),
    (re.compile(r":\(\)\{\s*:\|:&\s*\};:"), "fork bomb"),
    (re.compile(r"curl\s+.*\|\s*(ba)?sh"), "管道执行远程脚本"),
    (re.compile(r"wget\s+.*\|\s*(ba)?sh"), "管道执行远程脚本"),
    (re.compile(r">\s*/dev/sd"), "覆盖磁盘设备"),
]


def is_safe_command(command: str) -> bool:
    trimmed = command.strip()
    if not trimmed:
        return False
    # This is deliberately a small grammar, not a general shell parser. Commands
    # that read arbitrary paths, expand shell input or run programs require approval.
    if any(ch in command for ch in "\n\r|;&<>$`(){}\\"):
        return False
    try:
        words = shlex.split(trimmed)
    except ValueError:
        return False
    if not words:
        return False
    if words[0] in {"pwd", "whoami", "hostname", "true", "false"}:
        return len(words) == 1
    if words[0] == "echo":
        return not any(ch in trimmed for ch in "*?[]~!")
    if words[0] == "ls":
        return all(word in {".", "-l", "-a", "-la", "-al", "-h", "-lah", "-1"}
                   for word in words[1:])
    if words[:2] == ["git", "status"]:
        return all(word in {"--short", "--porcelain", "--branch", "-s", "-b", "-sb"}
                   for word in words[2:])
    return words in [["node", "-v"], ["python", "--version"], ["python3", "--version"]]


class DangerousCommandDetector:


    def __init__(self, extra_patterns: list[tuple[str, str]] | None = None) -> None:
        self._patterns = list(_DANGEROUS_PATTERNS)
        if extra_patterns:
            for regex_str, reason in extra_patterns:
                self._patterns.append((re.compile(regex_str), reason))


    def detect(self, command: str) -> tuple[bool, str]:
        for pattern, reason in self._patterns:
            if pattern.search(command):
                return True, reason
        return False, ""
