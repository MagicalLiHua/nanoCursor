"""Check shipped resources, version and exclusions without importing the source."""
from __future__ import annotations

import argparse
import hashlib
import tarfile
import tomllib
import zipfile
from pathlib import Path


def check(path: Path, expected: str) -> None:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            metadata = next(n for n in names if n.endswith(".dist-info/METADATA"))
            assert f"Version: {expected}\n" in archive.read(metadata).decode()
            entry = next(n for n in names if n.endswith(".dist-info/entry_points.txt"))
            assert "nanocursor.__main__:main" in archive.read(entry).decode()
    else:
        with tarfile.open(path) as archive:
            members = archive.getmembers()
            assert all(m.isfile() or m.isdir() for m in members), "Unexpected archive links"
            names = ["/".join(m.name.split("/")[1:]) for m in members]
            member = next(m for m in members if m.name.endswith("/pyproject.toml"))
            assert tomllib.loads(archive.extractfile(member).read().decode())["project"]["version"] == expected
    for name in names:
        parts = Path(name).parts
        assert not any(p in {".nanocursor", ".artifacts", ".DS_Store", "__pycache__", ".venv"} for p in parts), name
        assert not name.endswith(("TECHNICAL_DEBT.md", ".pyc", ".log", "credentials.json")), name
        assert not any(p == ".env" or p.startswith(".env.") for p in parts), name
        assert not name.startswith("/") and ".." not in parts, name
    for resource in ["nanocursor/styles.tcss", "nanocursor/agents/builtins/explore.md",
                     "nanocursor/agents/builtins/general-purpose.md", "nanocursor/eval/issue_agent_system_prompt.txt",
                     "nanocursor/eval/approval_cases.jsonl"]:
        assert resource in names, f"Missing resource: {resource}"
    print(f"Verified {path.name}: {len(names)} entries")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    expected = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    packages = sorted(args.directory.glob("*.whl")) + sorted(args.directory.glob("*.tar.gz"))
    assert len(packages) == 2, "Expected exactly one wheel and one sdist"
    for path in packages:
        check(path, expected)
    checksums = "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in packages)
    (args.directory / "SHA256SUMS").write_text(checksums)


if __name__ == "__main__":
    main()
