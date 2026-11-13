"""
Branch and commit the server runs from, read from the .git folder of the installation
(no git executable needed: the Windows service account may not have it on PATH).
Shown next to the version in Settings, so a PC left on a branch under test is noticed.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]


def _git_dir(root: Path) -> Optional[Path]:
    g = root / ".git"
    if g.is_dir():
        return g
    if g.is_file():                     # worktree: "gitdir: <path>"
        text = g.read_text(encoding="utf-8", errors="replace").strip()
        if text.startswith("gitdir:"):
            p = Path(text[7:].strip())
            return p if p.is_absolute() else (root / p).resolve()
    return None


def _ref_commit(git: Path, ref: str) -> Optional[str]:
    for base in (git, _common_dir(git)):
        f = base / ref
        if f.is_file():
            return f.read_text(encoding="utf-8", errors="replace").strip() or None
        packed = base / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8", errors="replace").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0]
    return None


def _common_dir(git: Path) -> Path:
    c = git / "commondir"
    if c.is_file():
        p = Path(c.read_text(encoding="utf-8", errors="replace").strip())
        return p if p.is_absolute() else (git / p).resolve()
    return git


def read_build_info(root: Path = REPO_ROOT) -> dict:
    """{"branch": "main" | None (detached or unknown), "commit": short hash | None}."""
    try:
        git = _git_dir(root)
        if git is None:
            return {"branch": None, "commit": None}
        head = (git / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
        if head.startswith("ref:"):
            ref = head[4:].strip()
            branch = ref.removeprefix("refs/heads/")
            commit = _ref_commit(git, ref)
        else:
            branch, commit = None, head
        return {"branch": branch, "commit": commit[:7] if commit else None}
    except OSError:
        return {"branch": None, "commit": None}


@lru_cache(maxsize=1)
def build_info() -> dict:
    """Read once per process: the service restarts after every update."""
    return read_build_info()
