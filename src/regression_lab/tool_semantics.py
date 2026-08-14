"""Privacy-preserving semantic attributes for Coding Agent tool spans.

The observer records the *shape* of an action, never file contents, prompts,
raw shell commands, or arbitrary tool argument values.  These fields enable
deterministic behavior analysis without turning the trace into a data dump.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


_PATH_TOOLS = frozenset({"read_file", "write_file", "edit_file"})


def _relative_target(root: Path, value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        candidate = (root / value).resolve()
        relative = candidate.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return relative.as_posix()


def semantic_tool_attributes(tool_name: str, arguments: dict[str, Any], *, worktree: Path) -> dict[str, Any]:
    """Return the approved, desensitized attributes for one tool invocation."""

    keys = sorted(key for key in arguments if isinstance(key, str))
    target_path = _relative_target(worktree, arguments.get("path")) if tool_name in _PATH_TOOLS else None
    material: dict[str, Any] = {"tool_name": tool_name, "argument_keys": keys}
    if target_path is not None:
        material["target_path"] = target_path
    fingerprint = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    attributes: dict[str, Any] = {
        "argument_keys": keys,
        "argument_fingerprint": f"sha256:{fingerprint}",
    }
    if target_path is not None:
        attributes["target_path"] = target_path
    return attributes
