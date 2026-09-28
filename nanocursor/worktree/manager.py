from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import stat
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from nanocursor.storage import state_lock
from nanocursor.worktree.changes import CleanupResult, count_worktree_changes
from nanocursor.worktree.models import RemovalApproval, Worktree, WorktreeSession
from nanocursor.worktree.registry import WorktreeRegistry
from nanocursor.worktree.session import load_worktree_session, save_worktree_session
from nanocursor.worktree.setup import perform_post_creation_setup
from nanocursor.worktree.slug import flatten_slug, validate_slug

GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "GIT_OPTIONAL_LOCKS": "0"}


class WorktreeError(Exception):
    pass


class WorktreeManager:
    def __init__(
        self,
        repo_root: str,
        symlink_directories: list[str] | None = None,
        worktree_dir: str | None = None,
        *,
        registry_dir: Path | None = None,
        removal_guard: Callable[[Worktree], None] | None = None,
        recovery_store=None,
    ) -> None:
        self.repo_root = str(Path(repo_root).resolve())
        self.symlink_directories = symlink_directories or []
        self.worktree_dir = str(Path(worktree_dir or Path(self.repo_root) / ".nanocursor" / "worktrees").resolve())
        self._default_worktree_dir = worktree_dir is None
        self._nanocursor_dir = Path(self.repo_root) / ".nanocursor"
        self._lock = asyncio.Lock()
        self.removal_guard = removal_guard
        self._approvals: dict[str, RemovalApproval] = {}
        self.current_session: WorktreeSession | None = None
        self.active: dict[str, Worktree] = {}
        self.common_dir = self._common_git_dir(self.repo_root)
        self.registry = WorktreeRegistry(self.common_dir or self.repo_root, registry_dir, recovery_store)
        self._reload()

    def _run_git(self, args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=cwd or self.repo_root, capture_output=True,
            text=True, timeout=60, stdin=subprocess.DEVNULL, env={**os.environ, **GIT_ENV},
        )

    def _git_value(self, args: list[str], cwd: str | None = None) -> str:
        result = self._run_git(args, cwd)
        if result.returncode:
            raise WorktreeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout.strip()

    def _common_git_dir(self, cwd: str) -> str:
        try:
            value = self._git_value(["rev-parse", "--git-common-dir"], cwd)
            return str((Path(cwd) / value).resolve())
        except (WorktreeError, OSError, subprocess.SubprocessError):
            return ""

    def _reload(self) -> None:
        active: dict[str, Worktree] = {}
        for wt in self.registry.load():
            if wt.state == "removed":
                continue
            if wt.common_dir != self.common_dir or wt.name in active:
                raise WorktreeError("Worktree registration is inconsistent; preserve it for inspection")
            active[wt.name] = wt
        self.active = active

    def _verify_identity(self, wt: Worktree) -> None:
        path = Path(wt.path)
        if wt.state != "active" or not wt.head_commit or not wt.git_dir_identity:
            raise WorktreeError(f"Worktree {wt.name} requires inspection ({wt.state}); preserved at {wt.path}")
        if str(path.resolve()) != wt.path or path.is_symlink():
            raise WorktreeError("Worktree path identity changed; refusing to reuse it")
        git_dir = self._git_value(["rev-parse", "--absolute-git-dir"], wt.path)
        info = Path(git_dir).stat()
        identity = f"{info.st_dev}:{info.st_ino}"
        branch = self._git_value(["symbolic-ref", "--short", "HEAD"], wt.path)
        if (str(Path(git_dir).resolve()) != wt.git_dir or identity != wt.git_dir_identity
                or self._common_git_dir(wt.path) != wt.common_dir or branch != wt.branch):
            raise WorktreeError("Worktree repository, branch or checkout identity changed; refusing to reuse it")
        if self.registry.store.register_workspace(wt.path).workspace_id != wt.workspace_id:
            raise WorktreeError("Worktree recovery identity changed; refusing to reuse it")
        self._git_value(["cat-file", "-e", f"{wt.head_commit}^{{commit}}"], wt.path)

    def source_changes(self) -> list[str]:
        """List source edits, excluding only nanoCursor's own worktree artifacts."""
        result = self._run_git(["status", "--porcelain=v1", "-z", "--untracked-files=all"])
        if result.returncode:
            raise WorktreeError("Cannot query source status; worktree was not created")
        raw = result.stdout
        records = iter(raw.split("\0"))
        dirty = []
        managed_prefix = os.path.relpath(self.worktree_dir, self.repo_root).replace(os.sep, "/") + "/"
        for record in records:
            if not record:
                continue
            if len(record) < 4 or record[2] != " ":
                raise WorktreeError("Cannot parse source status; worktree was not created")
            path = record[3:]
            if not path.startswith(managed_prefix) and path != ".nanocursor/worktree_session.json":
                dirty.append(record)
            if record[0] in "RC" or record[1] in "RC":
                if not next(records, ""):
                    raise WorktreeError("Incomplete source rename status")
        return dirty

    async def create(self, name: str, base_branch: str = "HEAD", *, accept_committed_base: bool = False) -> Worktree:
        async with self._lock:
            self._check_creation_directory()
            error = validate_slug(name)
            if error:
                raise WorktreeError(error)
            if not self.common_dir:
                raise WorktreeError("Worktree isolation requires a Git repository with a commit")
            with state_lock(self.registry.directory):
                self._reload()
                if name in self.active:
                    raise WorktreeError(f"worktree already exists: {name}; use enter to reuse its registered identity")
                path = Path(self.worktree_dir) / flatten_slug(name)
                if path.exists() or path.is_symlink():
                    raise WorktreeError(f"Worktree path already exists and will not be reused: {path}")
                base = self._git_value(["rev-parse", "--verify", "--end-of-options", f"{base_branch}^{{commit}}"])
                if self.source_changes() and not accept_committed_base:
                    raise WorktreeError(
                        f"Source has uncommitted/untracked changes. New worktree starts from commit {base}, "
                        "without those changes. Cancel to continue in the original directory, or explicitly "
                        "use /worktree create <name> [base] --from-commit."
                    )
                identity = secrets.token_hex(16)
                wt = Worktree(
                    name=name, path=str(path), branch=f"worktree-{flatten_slug(name)}-{identity[:10]}",
                    based_on=base_branch, head_commit=base, creation_id=identity,
                    common_dir=self.common_dir, owner=f"pid:{os.getpid()}", state="creating",
                )
                # Before creating any Git object, record the exact base and unique branch.
                self.registry.save(wt)
                self.active[name] = wt
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    self._git_value(["worktree", "add", "-b", wt.branch, wt.path, base])
                    wt.git_dir = str(Path(self._git_value(["rev-parse", "--absolute-git-dir"], wt.path)).resolve())
                    info = Path(wt.git_dir).stat()
                    wt.git_dir_identity = f"{info.st_dev}:{info.st_ino}"
                    wt.workspace_id = self.registry.store.register_workspace(wt.path).workspace_id
                    perform_post_creation_setup(self.repo_root, wt.path, self.symlink_directories)
                    wt.state = "active"
                    self.registry.save(wt)
                except BaseException as exc:
                    # Even clean Git status can hide ignored results or hook effects.
                    wt.state, wt.error = "creation_incomplete", str(exc)
                    self.registry.save(wt)
                    if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                        raise
                    raise WorktreeError(f"Worktree creation incomplete; retained {wt.path}, branch {wt.branch}: {exc}") from exc
                return wt

    def preview_creation(self, name: str, base_branch: str = "HEAD") -> tuple[str, list[str]]:
        """Read-only validation for startup UI, before committing an execution intent."""
        self._check_creation_directory()
        if error := validate_slug(name):
            raise WorktreeError(error)
        if not self.common_dir:
            raise WorktreeError("Worktree isolation requires a Git repository with a commit")
        self._reload()
        path = Path(self.worktree_dir) / flatten_slug(name)
        if name in self.active or path.exists() or path.is_symlink():
            raise WorktreeError(f"Worktree name/path already exists; preserved: {path}")
        base = self._git_value(["rev-parse", "--verify", "--end-of-options", f"{base_branch}^{{commit}}"])
        return base, self.source_changes()

    def _check_creation_directory(self) -> None:
        if self._default_worktree_dir:
            for path in (self._nanocursor_dir, self._nanocursor_dir / "worktrees"):
                if path.is_symlink():
                    raise WorktreeError(f"Managed worktree directories must not be symbolic links: {path}")

    async def enter(self, name: str) -> WorktreeSession:
        wt = self.active.get(name)
        if wt is None:
            raise WorktreeError(f"worktree not found: {name}")
        self._verify_identity(wt)
        if self.current_session and self.current_session.worktree_name != name:
            raise WorktreeError("Exit the current worktree before entering another")
        session = WorktreeSession(
            original_cwd=self.repo_root, worktree_path=wt.path, worktree_name=name,
            original_branch=self._get_current_branch(), original_head_commit=wt.head_commit,
            workspace_id=wt.workspace_id,
        )
        save_worktree_session(self._nanocursor_dir, session)
        self.current_session = session
        return session

    def _check_removal_guard(self, wt: Worktree) -> None:
        tables = self.registry.store.rows(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='checkpoint_records'"
        )
        if tables and self.registry.store.rows(
            "SELECT checkpoint_id FROM checkpoint_records WHERE workspace_id=? AND pinned=1 LIMIT 1",
            (wt.workspace_id,),
        ):
            raise WorktreeError("A pinned checkpoint protects this worktree; unpin it explicitly before deletion")
        if self.removal_guard is None:
            raise WorktreeError("Deletion requires recovery/owner checks; keep the worktree until these checks are available")
        self.removal_guard(wt)

    def _fingerprint(self, wt: Worktree) -> str:
        """Bind confirmation to actual bytes, modes, ignored files, branch and index."""
        self._verify_identity(wt)
        digest = hashlib.sha256()
        digest.update(json.dumps([wt.workspace_id, wt.path, wt.branch, wt.head_commit]).encode())
        digest.update(self._git_value(["rev-parse", "HEAD"], wt.path).encode())
        index = Path(wt.git_dir) / "index"
        if index.exists():
            digest.update(index.read_bytes())
        root = Path(wt.path)
        for directory, dirs, files in os.walk(root, followlinks=False):
            dirs.sort()
            for name in sorted(dirs + files):
                path = Path(directory) / name
                before = path.lstat()
                digest.update(json.dumps([str(path.relative_to(root)), before.st_mode, before.st_size]).encode())
                if stat.S_ISLNK(before.st_mode):
                    digest.update(os.fsencode(os.readlink(path)))
                elif stat.S_ISREG(before.st_mode):
                    with path.open("rb") as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(chunk)
                elif not stat.S_ISDIR(before.st_mode):
                    raise WorktreeError(f"Cannot safely inspect special file: {path}")
                after = path.lstat()
                if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_mode) != (
                    after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode
                ):
                    raise WorktreeError("Worktree changed during inspection; request a new confirmation")
        return digest.hexdigest()

    def prepare_removal(self, name: str) -> RemovalApproval:
        wt = self.active.get(name)
        if wt is None:
            raise WorktreeError(f"worktree not found: {name}")
        self._check_removal_guard(wt)
        changes = count_worktree_changes(wt.path, wt.head_commit)
        if changes.state == "unknown":
            raise WorktreeError(f"Worktree status unknown; preserved: {changes.error}")
        approval = RemovalApproval(
            token=secrets.token_hex(8), workspace_id=wt.workspace_id,
            fingerprint=self._fingerprint(wt), expires_at=time.monotonic() + 300,
            summary=(f"{wt.path}\nBranch: {wt.branch}\nBase: {wt.head_commit}\n"
                     f"{changes.uncommitted} uncommitted, {changes.new_commits} new commits, "
                     f"{changes.ignored} ignored entries. Removal permanently deletes this checkout and branch."),
        )
        self._approvals[approval.token] = approval
        return approval

    async def exit(self, name: str, action: str = "keep", discard_changes: bool = False,
                   *, confirmation_token: str | None = None) -> None:
        async with self._lock:
            wt = self.active.get(name)
            if wt is None:
                raise WorktreeError(f"worktree not found: {name}")
            if action not in {"keep", "remove"}:
                raise WorktreeError("Worktree action must be keep or remove")
            if action == "remove":
                await self._remove_worktree(name, wt, confirmation_token=confirmation_token)
            if self.current_session and self.current_session.worktree_name == name:
                save_worktree_session(self._nanocursor_dir, None)
                self.current_session = None

    async def _remove_worktree(self, name: str, wt: Worktree, *, confirmation_token: str | None = None) -> None:
        # Model arguments such as discard_changes are intentionally not proof of consent.
        approval = self._approvals.pop(confirmation_token or "", None)
        if approval is None or approval.workspace_id != wt.workspace_id or approval.expires_at < time.monotonic():
            raise WorktreeError("Removal requires a fresh, single-use user confirmation through /worktree exit --remove")
        with state_lock(self.registry.directory):
            self._check_removal_guard(wt)
            if self._fingerprint(wt) != approval.fingerprint:
                raise WorktreeError("Worktree changed since confirmation; preserved. Request a new confirmation")
            head = self._git_value(["rev-parse", "HEAD"], wt.path)
            wt.state = "removing"
            self.registry.save(wt)
            try:
                self._git_value(["worktree", "remove", "--force", wt.path])
            except (WorktreeError, OSError, subprocess.SubprocessError) as exc:
                wt.state = "active" if Path(wt.path).exists() else "removal_incomplete"
                wt.error = str(exc)
                self.registry.save(wt)
                if wt.state == "removal_incomplete" and self.current_session and self.current_session.worktree_name == name:
                    save_worktree_session(self._nanocursor_dir, None)
                    self.current_session = None
                raise WorktreeError(f"Removal failed; registration and branch retained: {exc}") from exc
            wt.state = "removal_incomplete"
            self.registry.save(wt)
            if self.current_session and self.current_session.worktree_name == name:
                save_worktree_session(self._nanocursor_dir, None)
                self.current_session = None
            try:
                trees = self._git_value(["worktree", "list", "--porcelain"])
                if f"branch refs/heads/{wt.branch}\n" in trees + "\n":
                    raise WorktreeError("Branch is checked out elsewhere")
                # Compare-and-delete prevents deleting a ref changed after confirmation.
                self._git_value(["update-ref", "-d", f"refs/heads/{wt.branch}", head])
            except (WorktreeError, OSError, subprocess.SubprocessError) as exc:
                wt.error = str(exc)
                self.registry.save(wt)
                raise WorktreeError(f"Directory removed, but branch {wt.branch} retained; registry records partial removal: {exc}") from exc
            wt.state, wt.error = "removed", ""
            self.registry.save(wt)
            self.registry.store.mark_workspace_removed(wt.workspace_id)
            self.active.pop(name, None)

    async def auto_cleanup(self, name: str, head_commit: str) -> CleanupResult:
        wt = self.active.get(name)
        return CleanupResult(kept=wt is not None, path=wt.path if wt else "", branch=wt.branch if wt else "")

    def list_worktrees(self) -> list[Worktree]:
        return list(self.active.values())

    def get_current_session(self) -> WorktreeSession | None:
        return self.current_session

    def restore_session(self) -> WorktreeSession | None:
        session = load_worktree_session(self._nanocursor_dir)
        if session is None:
            return None
        wt = self.active.get(session.worktree_name)
        if wt is None or not session.workspace_id or wt.workspace_id != session.workspace_id or wt.path != session.worktree_path:
            raise WorktreeError("Saved session has no matching creation record; preserved for inspection")
        self._verify_identity(wt)
        self.current_session = session
        return session

    def _get_current_branch(self) -> str:
        return self._git_value(["rev-parse", "--abbrev-ref", "HEAD"])

    def _get_head_commit(self) -> str:
        return self._git_value(["rev-parse", "HEAD"])

    @staticmethod
    def read_worktree_head_sha(wt_path: str) -> str | None:
        wt = Path(wt_path)
        git_file = wt / ".git"
        if not git_file.exists():
            return None

        try:
            content = git_file.read_text(encoding="utf-8").strip()
            if not content.startswith("gitdir:"):
                return None
            gitdir = Path(content.split(":", 1)[1].strip())
            if not gitdir.is_absolute():
                gitdir = (wt / gitdir).resolve()

            commondir_file = gitdir / "commondir"
            if commondir_file.exists():
                commondir_rel = commondir_file.read_text(encoding="utf-8").strip()
                commondir = (gitdir / commondir_rel).resolve()
            else:
                commondir = gitdir

            head_file = gitdir / "HEAD"
            if not head_file.exists():
                return None
            head_content = head_file.read_text(encoding="utf-8").strip()

            if head_content.startswith("ref:"):
                ref_path = head_content.split(":", 1)[1].strip()
                ref_file = gitdir / ref_path
                if not ref_file.exists():
                    ref_file = commondir / ref_path
                if ref_file.exists():
                    return ref_file.read_text(encoding="utf-8").strip()
                packed_refs = commondir / "packed-refs"
                if packed_refs.exists():
                    for line in packed_refs.read_text(encoding="utf-8").splitlines():
                        if line.strip() and not line.startswith("#"):
                            parts = line.split()
                            if len(parts) == 2 and parts[1] == ref_path:
                                return parts[0]
                return None
            return head_content
        except OSError:
            return None
