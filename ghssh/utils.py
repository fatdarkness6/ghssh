"""Command execution, checked paths, atomic writes, locking, and rollback."""

from __future__ import annotations

import contextlib
import fcntl
import os
import stat
import subprocess
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Self


class GhsshError(Exception):
    """An actionable operational error safe to display to the user."""


class Runner:
    def run(
        self,
        args: Sequence[str],
        *,
        check: bool = True,
        interactive: bool = False,
        env: Mapping[str, str] | None = None,
        input: str | None = None,
        timeout: float | None = 30,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                list(args),
                check=False,
                shell=False,
                text=True,
                input=input,
                capture_output=not interactive,
                env=dict(env) if env is not None else None,
                timeout=None if interactive else timeout,
            )
        except FileNotFoundError as exc:
            raise GhsshError(f"Required command is not installed: {args[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise GhsshError(f"{args[0]} timed out; check connectivity or your SSH agent.") from exc
        if check and result.returncode:
            # Never echo arguments: a future caller might supply sensitive input.
            detail = (result.stderr or "").strip()
            raise GhsshError(f"{args[0]} failed (exit {result.returncode}). {detail}")
        return result


@dataclass(frozen=True)
class Layout:
    home: Path

    @classmethod
    def default(cls) -> Layout:
        return cls(Path.home())

    @property
    def ssh(self) -> Path:
        return self.home / ".ssh"

    @property
    def config(self) -> Path:
        return self.ssh / "config"

    @property
    def state(self) -> Path:
        return self.ssh / "ghssh-state.json"

    @property
    def switch_state(self) -> Path:
        return self.ssh / "ghssh-switch.json"

    @property
    def backups(self) -> Path:
        return self.ssh / "ghssh-backups"

    def checked(self, path: Path, *, exists: bool = False) -> Path:
        """Accept only owned, non-symlink paths strictly below this SSH directory."""
        if not path.is_absolute() or ".." in path.parts:
            raise GhsshError(f"Unsafe path: {path}")
        try:
            relative = path.relative_to(self.ssh)
        except ValueError as exc:
            raise GhsshError(f"Refusing a path outside {self.ssh}: {path}") from exc
        if not relative.parts:
            raise GhsshError("Expected a file beneath the SSH directory.")
        current = self.ssh
        for part in ("", *relative.parts):
            if part:
                current /= part
            try:
                info = current.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid():
                raise GhsshError(f"Refusing symlink or unowned path: {current}")
            if current != path and not stat.S_ISDIR(info.st_mode):
                raise GhsshError(f"Expected directory: {current}")
            if current == path and not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise GhsshError(f"Refusing special file: {current}")
            if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                raise GhsshError(f"Refusing hard-linked file: {current}")
        if exists and not path.is_file():
            raise GhsshError(f"Required file does not exist: {path}")
        return path

    def ensure(self) -> None:
        self.checked(self.config)
        self.ssh.mkdir(mode=0o700, exist_ok=True)
        self.ssh.chmod(0o700)

    def identity_path(self, value: str) -> Path:
        if value.startswith("~/"):
            path = self.home / value[2:]
        elif value.startswith("/"):
            path = Path(value)
        else:
            raise GhsshError(
                "IdentityFile must be absolute or start with ~/. Configure it explicitly."
            )
        if "%" in value or "${" in value:
            raise GhsshError("IdentityFile token/environment expansion is not supported safely.")
        return self.checked(path)


def read_bytes(path: Path, *, limit: int = 4 * 1024 * 1024) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise GhsshError(f"Refusing unsafe file: {path}")
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise GhsshError(f"File exceeds safety size limit: {path}")
        return data


def atomic_write(layout: Layout, path: Path, data: bytes, mode: int = 0o600) -> None:
    layout.checked(path)
    fd, temp_name = tempfile.mkstemp(prefix=".ghssh-write-", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        layout.checked(path)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@contextlib.contextmanager
def locked(layout: Layout) -> Iterator[None]:
    layout.ensure()
    path = layout.checked(layout.ssh / ".ghssh.lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            raise GhsshError("Unsafe ghssh lock file.")
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise GhsshError(
                "Another ghssh operation is running. Try again when it finishes."
            ) from exc
        yield
    finally:
        os.close(fd)


class Transaction:
    """Rollback only the explicitly touched local files, including on Ctrl-C."""

    def __init__(self, layout: Layout, paths: Sequence[Path]):
        self.layout = layout
        self.snapshots: dict[Path, tuple[bytes, int] | None] = {}
        self.changed: dict[Path, tuple[bytes, int] | None] = {}
        for path in paths:
            layout.checked(path)
            if path.exists():
                self.snapshots[path] = (read_bytes(path), stat.S_IMODE(path.stat().st_mode))
            else:
                self.snapshots[path] = None

    def mark(self, *paths: Path) -> None:
        """Record only files actually changed by this operation, and their new state."""
        for path in paths:
            if path not in self.snapshots:
                raise GhsshError(f"Transaction did not snapshot {path}")
            self.layout.checked(path)
            self.changed[path] = (
                (read_bytes(path), stat.S_IMODE(path.stat().st_mode)) if path.exists() else None
            )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, kind: type[BaseException] | None, value: BaseException | None, tb: object
    ) -> bool:
        if kind is not None:
            failures = []
            for path, expected in reversed(list(self.changed.items())):
                try:
                    self.layout.checked(path)
                    current = (
                        (read_bytes(path), stat.S_IMODE(path.stat().st_mode))
                        if path.exists()
                        else None
                    )
                    if current != expected:
                        raise GhsshError(
                            "File changed outside this transaction; retained external changes"
                        )
                    snapshot = self.snapshots[path]
                    if snapshot is None:
                        path.unlink(missing_ok=True)
                    else:
                        atomic_write(self.layout, path, *snapshot)
                except (OSError, GhsshError) as exc:
                    failures.append(f"{path}: {exc}")
            if failures:
                raise GhsshError(
                    "Rollback incomplete; use the backup. " + "; ".join(failures)
                ) from value
        return False
