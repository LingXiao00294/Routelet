from __future__ import annotations

import os
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


def prepare_state_file(path: Path) -> None:
    """Create state parents and restrict managed POSIX directories/files."""
    path = path.resolve()
    root = data_path(".")
    if not path.is_relative_to(root):
        # Explicit paths may live in shared/project directories we do not own.
        path.parent.mkdir(parents=True, exist_ok=True)
        return

    directory = root
    directories = [directory]
    for part in path.relative_to(root).parts[:-1]:
        directory = directory / part
        directories.append(directory)
    for directory in directories:
        mode = 0o700 if os.name == "posix" else 0o777
        directory.mkdir(mode=mode, parents=True, exist_ok=True)
        if os.name == "posix":
            directory.chmod(0o700)
    if os.name == "posix" and path.is_file():
        path.chmod(0o600)


def restrict_file_permissions(fd: int) -> None:
    """Use owner-only POSIX access without altering Windows ACLs."""
    if os.name == "posix":
        os.fchmod(fd, 0o600)


def private_file_opener(path: str, flags: int) -> int:
    """Create state files privately before writing any sensitive content."""
    fd = os.open(path, flags, 0o600)
    try:
        restrict_file_permissions(fd)
    except BaseException:
        os.close(fd)
        raise
    return fd
