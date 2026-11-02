"""System-Git version control for the four Astral memory files.

The repository lives at <shared_root>/.git and tracks only the four content
files listed by store.PROTECTED_FILES. Everything else is excluded through
.git/info/exclude for repositories created by this plugin. All git calls use
argv (no shell), a fixed identity, no hooks, no signing, no network and a
timeout. The module is pure standard library so it can be tested without an
AstrBot runtime.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__:
    from . import store as store_mod
    from . import templates as templates_mod
else:  # pragma: no cover - direct import for tests
    import store as store_mod
    import templates as templates_mod

logger = logging.getLogger("astrbot")

TRACKED_FILES: tuple[str, ...] = tuple(store_mod.PROTECTED_FILES)
GIT_TIMEOUT_SECONDS = 20.0
MAX_FILE_BYTES = 10 * 1024 * 1024
VERSIONS_LIMIT = 10
VERSIONS_LIMIT_MAX = 20
MARKER_FILE = "astral-version"
MARKER_VERSION = 1
HASH_RE = re.compile(r"^[0-9a-fA-F]{4,40}$")
AUTHOR_NAME = "Astral"
AUTHOR_EMAIL = "astral@localhost"
GIT_MISSING_REASON = (
    "没找到 git。装好 Git 并确认在 PATH 里，或在插件配置 "
    "version_control.git_path 填写 git 可执行文件路径。"
)
EXCLUDE_MARKER = "# Astral: only the four memory files are tracked.\n"
EXCLUDE_TEXT = (
    EXCLUDE_MARKER
    + "/*\n"
    + "".join(f"!/{name}\n" for name in TRACKED_FILES)
)

_LOCK = threading.RLock()


class VersioningError(Exception):
    """A user-visible versioning failure."""


def _now_text() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _sanitize_message(text: str) -> str:
    value = " ".join(str(text).split())
    return value[:200]


def _tail(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    joined = " / ".join(lines)
    return joined[:300] if joined else "未知错误"


def _tail_bytes(data: bytes) -> str:
    return _tail(data.decode("utf-8", errors="replace"))


def _creationflags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _env() -> dict[str, str]:
    env = os.environ.copy()
    for key in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_NAMESPACE",
    ):
        env.pop(key, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_PAGER"] = "cat"
    env["PAGER"] = "cat"
    return env


def _config_args(root: Path) -> list[str]:
    root_text = Path(root).resolve().as_posix()
    hooks = (Path(root) / ".astral" / ".git-no-hooks").as_posix()
    return [
        "-c",
        f"core.hooksPath={hooks}",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.autocrlf=false",
        "-c",
        "core.safecrlf=false",
        "-c",
        "core.quotepath=false",
        "-c",
        "core.longpaths=true",
        "-c",
        "color.ui=false",
        "-c",
        "gc.auto=0",
        "-c",
        "commit.gpgsign=false",
        "-c",
        "tag.gpgsign=false",
        "-c",
        "i18n.commitEncoding=utf-8",
        "-c",
        "i18n.logOutputEncoding=utf-8",
        "-c",
        f"safe.directory={root_text}",
        "-c",
        "init.defaultBranch=main",
        "-c",
        f"user.name={AUTHOR_NAME}",
        "-c",
        f"user.email={AUTHOR_EMAIL}",
    ]


def _run(
    root: Path,
    args: list[str],
    *,
    git: str,
    timeout: float = GIT_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    command = [git, *_config_args(root), *args]
    try:
        return subprocess.run(
            command,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            env=_env(),
            creationflags=_creationflags(),
        )
    except FileNotFoundError as exc:
        raise VersioningError(GIT_MISSING_REASON) from exc
    except subprocess.TimeoutExpired as exc:
        raise VersioningError(
            f"Git 超时（{timeout:g} 秒）；可能有其他 Git 进程占着仓库。"
        ) from exc
    except OSError as exc:
        raise VersioningError(f"无法运行 git：{exc.__class__.__name__}") from exc


def _run_bytes(
    root: Path,
    args: list[str],
    *,
    git: str,
    timeout: float = GIT_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[bytes]:
    command = [git, *_config_args(root), *args]
    try:
        return subprocess.run(
            command,
            cwd=str(root),
            capture_output=True,
            text=False,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            env=_env(),
            creationflags=_creationflags(),
        )
    except FileNotFoundError as exc:
        raise VersioningError(GIT_MISSING_REASON) from exc
    except subprocess.TimeoutExpired as exc:
        raise VersioningError(
            f"Git 超时（{timeout:g} 秒）；可能有其他 Git 进程占着仓库。"
        ) from exc
    except OSError as exc:
        raise VersioningError(f"无法运行 git：{exc.__class__.__name__}") from exc


def _check(cp: subprocess.CompletedProcess[str], action: str) -> None:
    if cp.returncode == 0:
        return
    detail = _tail(cp.stderr or cp.stdout)
    lowered = detail.lower()
    if "index.lock" in lowered or "another git process" in lowered:
        raise VersioningError(f"{action}失败：Git 仓库正被占用（index.lock），请稍后再试。")
    if "dubious ownership" in lowered:
        raise VersioningError(f"{action}失败：Git 认为仓库归属可疑，请检查共享根权限。")
    raise VersioningError(f"{action}失败：{detail}")


def _check_bytes(cp: subprocess.CompletedProcess[bytes], action: str) -> None:
    if cp.returncode == 0:
        return
    _check(
        subprocess.CompletedProcess(
            args=cp.args,
            returncode=cp.returncode,
            stdout=cp.stdout.decode("utf-8", errors="replace"),
            stderr=cp.stderr.decode("utf-8", errors="replace"),
        ),
        action,
    )


def find_git(configured: str = "") -> str | None:
    """Return the git executable, honouring an explicit path or directory."""
    text = (configured or "").strip().strip('"')
    if text:
        candidate = Path(text).expanduser()
        if candidate.is_dir():
            for relative in ("git.exe", "git", "cmd/git.exe", "bin/git.exe"):
                child = candidate / relative
                if child.is_file():
                    return str(child)
        if candidate.is_file():
            return str(candidate)
        found = shutil.which(text)
        if found:
            return found
        return None
    found = shutil.which("git")
    if found:
        return found
    if os.name == "nt":
        bases = []
        for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            value = os.environ.get(key)
            if value:
                bases.append(Path(value))
        for base in bases:
            for relative in (
                "Git/cmd/git.exe",
                "Git/bin/git.exe",
                "Programs/Git/cmd/git.exe",
                "Programs/Git/bin/git.exe",
            ):
                candidate = base / relative
                if candidate.is_file():
                    return str(candidate)
    else:
        for candidate in (
            "/usr/bin/git",
            "/usr/local/bin/git",
            "/opt/homebrew/bin/git",
        ):
            if Path(candidate).is_file():
                return candidate
    return None


def _repo_valid(root: Path, git: str) -> tuple[bool, str | None]:
    cp = _run(root, ["rev-parse", "--is-inside-work-tree"], git=git)
    if cp.returncode != 0 or cp.stdout.strip() != "true":
        return False, "共享根下的 .git 不是有效的 Git 仓库"
    cp = _run(root, ["rev-parse", "--show-toplevel"], git=git)
    if cp.returncode != 0:
        return False, "无法读取 Git 仓库根目录"
    top_text = cp.stdout.strip()
    if not top_text:
        return False, "无法读取 Git 仓库根目录"
    try:
        if os.path.samefile(Path(top_text), root):
            return True, None
    except OSError:
        pass
    left = os.path.normcase(os.path.normpath(str(Path(top_text))))
    right = os.path.normcase(os.path.normpath(str(root)))
    if left == right:
        return True, None
    return False, f"共享根下的 .git 指向了别处（{top_text}）"


def _head_short(root: Path, git: str) -> str | None:
    cp = _run(root, ["rev-parse", "--short", "HEAD"], git=git)
    if cp.returncode != 0:
        return None
    return cp.stdout.strip() or None


def _head_full(root: Path, git: str) -> str | None:
    cp = _run(root, ["rev-parse", "HEAD"], git=git)
    if cp.returncode != 0:
        return None
    return cp.stdout.strip() or None


def _short_hash(root: Path, git: str, commit: str) -> str:
    cp = _run(root, ["rev-parse", "--short", commit], git=git)
    if cp.returncode == 0 and cp.stdout.strip():
        return cp.stdout.strip()
    return commit[:7]


def _ls_files(root: Path, git: str) -> list[str]:
    cp = _run(root, ["ls-files", "-z", "--", *TRACKED_FILES], git=git)
    _check(cp, "读取记忆文件索引")
    return [item for item in cp.stdout.split("\0") if item]


def _is_junction(path: Path) -> bool:
    checker = getattr(path, "is_junction", None)
    if not callable(checker):
        return False
    try:
        return bool(checker())
    except OSError:
        return False


def _worktree_files(root: Path) -> list[str]:
    existing: list[str] = []
    for name in TRACKED_FILES:
        path = root / name
        if path.is_symlink() or _is_junction(path):
            raise VersioningError(f"{name} 是符号链接，拒绝提交/回退。")
        try:
            if not path.exists():
                continue
            if not path.is_file():
                raise VersioningError(f"{name} 不是普通文件，拒绝提交/回退。")
            size = path.stat().st_size
        except OSError as exc:
            raise VersioningError(f"无法读取 {name}：{exc.__class__.__name__}") from exc
        if size > MAX_FILE_BYTES:
            raise VersioningError(
                f"{name} 超过 {MAX_FILE_BYTES // (1024 * 1024)} MB，拒绝提交/回退。"
            )
        existing.append(name)
    return existing


def _changed_paths(root: Path, git: str, existing: list[str]) -> list[str]:
    cp = _run(root, ["rev-parse", "--verify", "--quiet", "HEAD"], git=git)
    if cp.returncode != 0:
        return [name for name in TRACKED_FILES if name in existing]
    cp = _run(
        root,
        ["diff", "--name-only", "-z", "HEAD", "--", *TRACKED_FILES],
        git=git,
    )
    _check(cp, "读取记忆改动")
    names = {item for item in cp.stdout.split("\0") if item}
    return [name for name in TRACKED_FILES if name in names]


def _commit_files(root: Path, git: str) -> list[str]:
    cp = _run(root, ["show", "--pretty=format:", "--name-only", "HEAD"], git=git)
    if cp.returncode != 0:
        return []
    names = {line.strip() for line in cp.stdout.splitlines() if line.strip()}
    return [name for name in TRACKED_FILES if name in names]


def _commit_locked(root: Path, git: str, message: str) -> dict[str, Any]:
    existing = _worktree_files(root)
    tracked = _ls_files(root, git)
    new_files = [name for name in existing if name not in tracked]
    if new_files:
        cp = _run(root, ["add", "--force", "--", *new_files], git=git)
        _check(cp, "暂存记忆文件")
    changed = _changed_paths(root, git, existing)
    if not changed:
        return {"status": "no_changes", "hash": _head_short(root, git)}
    cp = _run(
        root,
        ["commit", "--only", "--no-verify", "-m", message, "--", *changed],
        git=git,
    )
    if cp.returncode != 0:
        combined = f"{cp.stdout}\n{cp.stderr}".lower()
        if "nothing to commit" in combined:
            return {"status": "no_changes", "hash": _head_short(root, git)}
        _check(cp, "提交记忆")
    return {
        "status": "committed",
        "hash": _head_short(root, git),
        "full": _head_full(root, git),
        "files": _commit_files(root, git),
        "message": message,
    }


def _ensure_metadata(root: Path, git_dir: Path) -> None:
    try:
        info = git_dir / "info"
        info.mkdir(parents=True, exist_ok=True)
        exclude = info / "exclude"
        try:
            current = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        except OSError:
            current = ""
        if EXCLUDE_MARKER not in current:
            prefix = current
            if prefix and not prefix.endswith("\n"):
                prefix += "\n"
            templates_mod.atomic_write_text(exclude, prefix + EXCLUDE_TEXT)
        marker = git_dir / MARKER_FILE
        if not marker.exists():
            templates_mod.atomic_write_text(
                marker,
                json.dumps({"version": MARKER_VERSION}, ensure_ascii=False) + "\n",
            )
    except OSError as exc:
        logger.warning("astral: version metadata write failed (%s)", exc)


def ensure_repo(root: Path, *, git_path: str = "") -> dict[str, Any]:
    """Initialise the memory repository and create a baseline when needed."""
    with _LOCK:
        root = Path(root).resolve()
        if not root.is_dir():
            return {
                "ok": False,
                "available": True,
                "initialized": False,
                "reason": "共享根不可用",
            }
        git = find_git(git_path)
        if git is None:
            return {
                "ok": False,
                "available": False,
                "initialized": False,
                "git": None,
                "reason": GIT_MISSING_REASON,
            }
        git_dir = root / ".git"
        created = False
        owned = False
        if git_dir.exists():
            valid, reason = _repo_valid(root, git)
            if not valid:
                return {
                    "ok": False,
                    "available": True,
                    "initialized": False,
                    "git": git,
                    "reason": reason,
                }
            owned = git_dir.is_dir() and (git_dir / MARKER_FILE).is_file()
        else:
            try:
                cp = _run(root, ["init", "--quiet"], git=git)
                _check(cp, "初始化 Git 仓库")
            except VersioningError as exc:
                return {
                    "ok": False,
                    "available": True,
                    "initialized": False,
                    "git": git,
                    "reason": str(exc),
                }
            created = True
            owned = True
        if owned:
            _ensure_metadata(root, git_dir)
        head = _head_short(root, git)
        baseline = None
        warning = None
        if owned and head is None:
            try:
                result = _commit_locked(root, git, "初始快照 " + _now_text())
            except VersioningError as exc:
                warning = str(exc)
                logger.warning("astral: version baseline failed: %s", exc)
            else:
                if result.get("status") == "committed":
                    baseline = result.get("hash")
            head = _head_short(root, git)
        return {
            "ok": True,
            "available": True,
            "initialized": True,
            "owned": owned,
            "created": created,
            "git": git,
            "root": str(root),
            "head": head,
            "baseline": baseline,
            "reason": warning,
        }


def commit(root: Path, *, git_path: str = "") -> dict[str, Any]:
    """Commit the current four memory files."""
    with _LOCK:
        repo = ensure_repo(root, git_path=git_path)
        if not repo.get("ok"):
            raise VersioningError(str(repo.get("reason") or "版本仓库不可用"))
        resolved = Path(root).resolve()
        return _commit_locked(
            resolved,
            str(repo["git"]),
            "记忆快照 " + _now_text(),
        )


def _parse_versions(text: str) -> list[dict[str, Any]]:
    versions: list[dict[str, Any]] = []
    for chunk in text.split("\x1e"):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        lines = chunk.splitlines()
        if not lines:
            continue
        fields = lines[0].split("\x1f", 3)
        if len(fields) < 4:
            continue
        full, short, timestamp, message = fields
        try:
            ts = int(timestamp)
        except ValueError:
            ts = 0
        files = [
            line.strip()
            for line in lines[1:]
            if line.strip() in TRACKED_FILES
        ]
        versions.append(
            {
                "hash": short,
                "full": full,
                "timestamp": ts,
                "message": message,
                "files": files,
            }
        )
    return versions


def list_versions(
    root: Path,
    *,
    git_path: str = "",
    limit: int = VERSIONS_LIMIT,
) -> dict[str, Any]:
    """Return recent memory checkpoints, newest first."""
    with _LOCK:
        root = Path(root).resolve()
        if not root.is_dir():
            return {"status": "unavailable", "reason": "共享根不可用"}
        git = find_git(git_path)
        if git is None:
            return {"status": "unavailable", "reason": GIT_MISSING_REASON}
        if not (root / ".git").exists():
            return {"status": "empty", "versions": []}
        valid, reason = _repo_valid(root, git)
        if not valid:
            return {"status": "unavailable", "reason": reason}
        if _head_short(root, git) is None:
            return {"status": "empty", "versions": []}
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = VERSIONS_LIMIT
        limit = max(1, min(limit, VERSIONS_LIMIT_MAX))
        try:
            cp = _run(
                root,
                [
                    "log",
                    f"-n{limit}",
                    "--date=unix",
                    "--format=%x1e%H%x1f%h%x1f%ct%x1f%s",
                    "--name-only",
                    "--",
                    *TRACKED_FILES,
                ],
                git=git,
            )
            _check(cp, "读取版本列表")
        except VersioningError as exc:
            return {"status": "unavailable", "reason": str(exc)}
        versions = _parse_versions(cp.stdout)
        if not versions:
            return {"status": "empty", "versions": []}
        return {"status": "ok", "versions": versions}


def _resolve_commit(root: Path, git: str, target: str) -> str:
    if not HASH_RE.match(target):
        raise VersioningError(
            "版本号看起来不对；先发 /astral recover 看列表，再复制短哈希。"
        )
    cp = _run(
        root,
        ["rev-parse", "--verify", "--quiet", f"{target}^{{commit}}"],
        git=git,
    )
    if cp.returncode != 0:
        detail = _tail(cp.stderr)
        if "ambiguous" in detail.lower():
            raise VersioningError("短哈希不唯一，请多给几位。")
        raise VersioningError(f"找不到版本：{target}")
    full = cp.stdout.strip()
    if not full:
        raise VersioningError(f"找不到版本：{target}")
    return full


def _require_checkpoint(root: Path, git: str, full: str) -> None:
    cp = _run(root, ["rev-list", "--parents", "-n", "1", full], git=git)
    _check(cp, "读取目标版本")
    fields = cp.stdout.split()
    if len(fields) > 2:
        raise VersioningError("不能回退到合并提交。")
    cp = _run(root, ["merge-base", "--is-ancestor", full, "HEAD"], git=git)
    if cp.returncode == 1:
        raise VersioningError("只能回退到当前历史里的版本。")
    if cp.returncode != 0:
        raise VersioningError(f"检查版本历史失败：{_tail(cp.stderr)}")
    cp = _run(
        root,
        [
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            "--root",
            full,
            "--",
            *TRACKED_FILES,
        ],
        git=git,
    )
    _check(cp, "读取目标版本的改动")
    names = [line.strip() for line in cp.stdout.splitlines() if line.strip()]
    if not any(name in TRACKED_FILES for name in names):
        raise VersioningError("这个版本没有改过记忆文件，不是记忆检查点。")


def _target_entries(root: Path, git: str, commit: str) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for name in TRACKED_FILES:
        cp = _run(root, ["ls-tree", "-z", commit, "--", name], git=git)
        _check(cp, "读取目标版本")
        for record in cp.stdout.split("\0"):
            if not record:
                continue
            meta, _, path = record.partition("\t")
            if path != name:
                continue
            parts = meta.split(" ")
            if len(parts) < 3:
                continue
            mode, kind, object_id = parts[0], parts[1], parts[2]
            if kind != "blob" or mode not in ("100644", "100755"):
                raise VersioningError(f"目标版本里的 {name} 不是普通文件，拒绝回退。")
            cp_size = _run(root, ["cat-file", "-s", f"{commit}:{name}"], git=git)
            _check(cp_size, "读取目标文件大小")
            try:
                size = int(cp_size.stdout.strip())
            except ValueError:
                size = 0
            if size > MAX_FILE_BYTES:
                raise VersioningError(
                    f"目标版本里的 {name} 超过 "
                    f"{MAX_FILE_BYTES // (1024 * 1024)} MB，拒绝回退。"
                )
            entries[name] = {"mode": mode, "object": object_id, "size": size}
    return entries


def _restore_from_commit(
    root: Path,
    git: str,
    commit: str,
    entries: dict[str, dict[str, Any]],
    *,
    best_effort: bool = False,
) -> bool:
    ok = True
    for name in TRACKED_FILES:
        path = root / name
        try:
            entry = entries.get(name)
            if entry is None:
                if path.is_symlink() or _is_junction(path):
                    path.unlink()
                elif path.exists():
                    if not path.is_file():
                        raise VersioningError(f"{name} 不是普通文件，拒绝回退。")
                    path.unlink()
                continue
            cp = _run_bytes(root, ["cat-file", "blob", f"{commit}:{name}"], git=git)
            if cp.returncode != 0:
                raise VersioningError(f"读取目标文件失败：{_tail_bytes(cp.stderr)}")
            data = cp.stdout
            if len(data) > MAX_FILE_BYTES:
                raise VersioningError(f"目标版本里的 {name} 超过大小上限，拒绝回退。")
            try:
                current = (
                    path.read_bytes()
                    if path.is_file() and not path.is_symlink() and not _is_junction(path)
                    else None
                )
            except OSError as exc:
                raise VersioningError(f"读取 {name} 失败：{exc.__class__.__name__}") from exc
            if current == data:
                continue
            templates_mod.atomic_write_bytes(path, data)
        except (VersioningError, OSError) as exc:
            if best_effort:
                logger.warning(
                    "astral: best-effort restore failed for %s (%s)", name, exc
                )
                ok = False
                continue
            raise VersioningError(str(exc)) from exc
    return ok


def recover(root: Path, *, target: str, git_path: str = "") -> dict[str, Any]:
    """Restore the four memory files to a checkpoint, append-only."""
    with _LOCK:
        root = Path(root).resolve()
        if not root.is_dir():
            raise VersioningError("共享根不可用")
        git = find_git(git_path)
        if git is None:
            raise VersioningError(GIT_MISSING_REASON)
        if not (root / ".git").exists():
            raise VersioningError("还没有记忆版本；先发 /astral commit 保存一次。")
        valid, reason = _repo_valid(root, git)
        if not valid:
            raise VersioningError(str(reason))
        if _head_short(root, git) is None:
            raise VersioningError("还没有记忆版本；先发 /astral commit 保存一次。")
        _worktree_files(root)
        target_text = (target or "").strip()
        full = _resolve_commit(root, git, target_text)
        _require_checkpoint(root, git, full)
        entries = _target_entries(root, git, full)
        before = _head_short(root, git)
        saved = _commit_locked(root, git, "回退前自动保存 " + _now_text())
        if saved.get("status") == "committed":
            before = saved.get("hash")
        short = _short_hash(root, git, full)
        try:
            _restore_from_commit(root, git, full, entries)
        except VersioningError as exc:
            try:
                head_entries = _target_entries(root, git, "HEAD")
                _restore_from_commit(
                    root,
                    git,
                    "HEAD",
                    head_entries,
                    best_effort=True,
                )
            except Exception:
                logger.exception(
                    "astral: version rollback after restore failure failed"
                )
            raise VersioningError(f"{exc}（当前状态已保存为 {before}）") from exc
        try:
            result = _commit_locked(root, git, "回退到 " + short)
        except VersioningError as exc:
            return {
                "status": "recovered_uncommitted",
                "target": short,
                "before": before,
                "files": [],
                "error": str(exc),
            }
        if result.get("status") == "no_changes":
            return {
                "status": "no_changes",
                "target": short,
                "hash": _head_short(root, git),
            }
        if result.get("status") != "committed":
            return {
                "status": "recovered_uncommitted",
                "target": short,
                "before": before,
                "files": [],
                "error": "提交回退版本失败",
            }
        return {
            "status": "recovered",
            "target": short,
            "before": before,
            "hash": result.get("hash"),
            "files": result.get("files") or [],
        }


def _count_checkpoints(root: Path, git: str) -> int:
    cp = _run(
        root,
        ["rev-list", "--count", "HEAD", "--", *TRACKED_FILES],
        git=git,
    )
    if cp.returncode != 0:
        return 0
    try:
        return int(cp.stdout.strip())
    except ValueError:
        return 0


def _last_commit(root: Path, git: str) -> dict[str, Any] | None:
    cp = _run(
        root,
        ["log", "-1", "--date=unix", "--format=%h%x1f%ct%x1f%s"],
        git=git,
    )
    if cp.returncode != 0:
        return None
    fields = cp.stdout.strip().split("\x1f", 2)
    if len(fields) < 3:
        return None
    try:
        timestamp = int(fields[1])
    except ValueError:
        timestamp = 0
    return {"hash": fields[0], "timestamp": timestamp, "message": fields[2]}


def _dirty(root: Path, git: str) -> bool:
    cp = _run(
        root,
        ["status", "--porcelain", "--", *TRACKED_FILES],
        git=git,
    )
    return bool(cp.stdout.strip()) if cp.returncode == 0 else False


def summary(
    root: Path,
    *,
    git_path: str = "",
    enabled: bool = True,
) -> dict[str, Any]:
    """Return the read-only versioning state used by the plugin state payload."""
    with _LOCK:
        if not enabled:
            return {"enabled": False, "available": None, "initialized": False}
        root = Path(root)
        if not root.is_dir():
            return {
                "enabled": True,
                "available": False,
                "initialized": False,
                "reason": "共享根不可用",
            }
        git = find_git(git_path)
        if git is None:
            return {
                "enabled": True,
                "available": False,
                "initialized": False,
                "git": None,
                "reason": GIT_MISSING_REASON,
            }
        if not (root / ".git").exists():
            return {
                "enabled": True,
                "available": True,
                "initialized": False,
                "git": git,
                "head": None,
                "count": 0,
                "dirty": False,
                "last": None,
                "reason": None,
            }
        try:
            valid, reason = _repo_valid(root, git)
            if not valid:
                return {
                    "enabled": True,
                    "available": True,
                    "initialized": False,
                    "git": git,
                    "reason": reason,
                }
            head = _head_short(root, git)
            if head is None:
                return {
                    "enabled": True,
                    "available": True,
                    "initialized": True,
                    "git": git,
                    "head": None,
                    "count": 0,
                    "dirty": _dirty(root, git),
                    "last": None,
                    "reason": None,
                }
            return {
                "enabled": True,
                "available": True,
                "initialized": True,
                "git": git,
                "head": head,
                "count": _count_checkpoints(root, git),
                "dirty": _dirty(root, git),
                "last": _last_commit(root, git),
                "reason": None,
            }
        except VersioningError as exc:
            return {
                "enabled": True,
                "available": False,
                "initialized": False,
                "git": git,
                "reason": str(exc),
            }
