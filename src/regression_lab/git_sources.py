"""为同一 Git 仓库的版本比较创建只读源码快照。"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory


_SENSITIVE_UNTRACKED_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}
_SENSITIVE_UNTRACKED_NAMES = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}


class GitSourceError(ValueError):
    """Git 来源无法安全冻结时抛出。"""


@dataclass(frozen=True)
class GitSourcePlan:
    repository: Path
    baseline_revision: str
    candidate_revision: str
    candidate_source: str
    candidate_dirty: bool
    tracked_change_count: int
    untracked_change_count: int


@dataclass
class GitSourceSnapshots:
    """持有临时目录，调用方完成 Experiment 后调用 cleanup。"""

    plan: GitSourcePlan
    directory: TemporaryDirectory[str]
    baseline_root: Path
    candidate_root: Path

    def cleanup(self) -> None:
        self.directory.cleanup()


def _git(repository: Path, *arguments: str, input_text: str | None = None) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments], input=input_text,
        text=True, capture_output=True, check=False,
    )
    if completed.returncode:
        message = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise GitSourceError(message)
    return completed.stdout


def _repository_root(value: str | Path) -> Path:
    candidate = Path(value).expanduser().resolve()
    if not candidate.is_dir():
        raise GitSourceError("Agent repository does not exist")
    root = Path(_git(candidate, "rev-parse", "--show-toplevel").strip()).resolve()
    if root != candidate:
        raise GitSourceError("Agent repository path must be the Git repository root")
    return root


def _revision(repository: Path, ref: str, label: str) -> str:
    if not isinstance(ref, str) or not ref.strip():
        raise GitSourceError(f"{label} ref is required")
    try:
        return _git(repository, "rev-parse", "--verify", f"{ref.strip()}^{{commit}}").strip()
    except GitSourceError as exc:
        raise GitSourceError(f"{label} ref cannot be resolved to a commit: {ref}") from exc


def _reject_submodules(repository: Path) -> None:
    entries = _git(repository, "ls-files", "--stage").splitlines()
    if any(entry.startswith("160000 ") for entry in entries):
        raise GitSourceError("Git submodules are not supported by Same Git repository mode yet")


def _status_counts(repository: Path) -> tuple[bool, int, int]:
    rows = [line for line in _git(repository, "status", "--porcelain=v1").splitlines() if line]
    untracked = sum(line.startswith("?? ") for line in rows)
    return bool(rows), len(rows) - untracked, untracked


def inspect_git_sources(
    repository: str | Path,
    baseline_ref: str,
    candidate_source: str,
    candidate_ref: str | None = None,
) -> GitSourcePlan:
    """静态校验 Git 来源，不创建 clone，也不改动用户仓库。"""

    root = _repository_root(repository)
    _reject_submodules(root)
    baseline_revision = _revision(root, baseline_ref, "Baseline")
    if candidate_source == "working_tree":
        candidate_revision = _revision(root, "HEAD", "Candidate working tree")
        dirty, tracked, untracked = _status_counts(root)
    elif candidate_source == "git_ref":
        candidate_revision = _revision(root, candidate_ref or "", "Candidate")
        dirty = False
        tracked = untracked = 0
    else:
        raise GitSourceError("Candidate source must be working_tree or git_ref")
    return GitSourcePlan(
        repository=root,
        baseline_revision=baseline_revision,
        candidate_revision=candidate_revision,
        candidate_source=candidate_source,
        candidate_dirty=dirty,
        tracked_change_count=tracked,
        untracked_change_count=untracked,
    )


def entry_exists(plan: GitSourcePlan, relative_path: str, *, candidate: bool) -> bool:
    """检查脚本入口是否存在于将要冻结的版本中。"""

    if not relative_path or Path(relative_path).is_absolute() or ".." in Path(relative_path).parts:
        return False
    if candidate and plan.candidate_source == "working_tree":
        return (plan.repository / relative_path).is_file()
    revision = plan.candidate_revision if candidate else plan.baseline_revision
    try:
        _git(plan.repository, "cat-file", "-e", f"{revision}:{relative_path}")
    except GitSourceError:
        return False
    return True


def module_exists(plan: GitSourcePlan, module_name: str, *, candidate: bool) -> bool:
    """检查 ``python -m`` 的模块或包入口是否在对应源码版本中。"""

    parts = module_name.split(".")
    if not module_name or not all(part.isidentifier() for part in parts):
        return False
    relative_module = "/".join(parts)
    return entry_exists(plan, f"{relative_module}.py", candidate=candidate) or entry_exists(
        plan, f"{relative_module}/__main__.py", candidate=candidate,
    )


def _clone_revision(repository: Path, revision: str, destination: Path) -> None:
    completed = subprocess.run(
        ["git", "clone", "--shared", "--no-checkout", str(repository), str(destination)],
        text=True, capture_output=True, check=False,
    )
    if completed.returncode:
        raise GitSourceError(completed.stderr.strip() or "could not create local source snapshot")
    _git(destination, "checkout", "--detach", revision)


def _is_sensitive_untracked_path(relative_path: Path) -> bool:
    """未跟踪文件没有经过 Git 审核，常见密钥不能随源码快照传播。"""

    name = relative_path.name.lower()
    return (
        any(part == ".venv" for part in relative_path.parts)
        or name == ".env"
        or name.startswith(".env.")
        or name in _SENSITIVE_UNTRACKED_NAMES
        or relative_path.suffix.lower() in _SENSITIVE_UNTRACKED_SUFFIXES
    )


def _copy_untracked(repository: Path, destination: Path) -> None:
    names = _git(repository, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
    for relative_name in filter(None, names):
        relative_path = Path(relative_name)
        if _is_sensitive_untracked_path(relative_path):
            continue
        source = repository / relative_path
        target = destination / relative_name
        # 未跟踪软链接可能把快照带到仓库外；源码入口应显式提交后再使用。
        if not source.is_file() or source.is_symlink():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)


def create_git_source_snapshots(plan: GitSourcePlan) -> GitSourceSnapshots:
    """把比较双方冻结为临时 clone；原始仓库始终只读。"""

    directory = TemporaryDirectory(prefix="regression-lab-agent-sources-")
    root = Path(directory.name)
    try:
        baseline_root = root / "baseline"
        candidate_root = root / "candidate"
        _clone_revision(plan.repository, plan.baseline_revision, baseline_root)
        _clone_revision(plan.repository, plan.candidate_revision, candidate_root)
        if plan.candidate_source == "working_tree":
            patch = _git(plan.repository, "diff", "--binary", "HEAD")
            if patch:
                _git(candidate_root, "apply", "--binary", input_text=patch)
            _copy_untracked(plan.repository, candidate_root)
        return GitSourceSnapshots(plan, directory, baseline_root, candidate_root)
    except Exception:
        directory.cleanup()
        raise
