from __future__ import annotations

from pathlib import Path


def data_path(path: str | Path) -> Path:
    """Resolve relative state paths under the user's ~/.routelet directory."""
    resolved = Path(path).expanduser()
    if not resolved.is_absolute():
        resolved = Path.home() / ".routelet" / resolved
    return resolved.resolve()


def resolve_path(path: str | None, default: str) -> Path:
    """Use a shared default, while keeping explicit paths relative to cwd."""
    if path is None:
        return data_path(default)
    return Path(path).expanduser().resolve()
