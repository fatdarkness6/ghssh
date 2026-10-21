"""Small OS boundary for file ownership, private permissions and process locks."""

from __future__ import annotations

import os
import stat
import sys
from contextlib import contextmanager
from pathlib import Path

WINDOWS = os.name == "nt"
Permissions = int | str

if WINDOWS:
    from . import _windows
else:
    import fcntl


def is_link(path: Path) -> bool:
    """Include Windows junctions and every other reparse point, not just symlinks."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def owned(path: Path, info: os.stat_result | None = None) -> bool:
    if WINDOWS:
        return _windows.owned(path)
    return (info or path.stat()).st_uid == os.getuid()


def safe_parts(path: Path) -> bool:
    if not WINDOWS:
        return True
    # Reject device/UNC namespaces, alternate data streams and Win32 aliases.
    if not path.drive or path.drive.startswith("\\"):
        return False
    for part in path.parts[1:]:
        base = part.split(".", 1)[0].rstrip(" ").upper()
        if (
            ":" in part
            or part.endswith((".", " "))
            or any(c in part for c in '<>"|?*')
            or any(ord(c) < 32 for c in part)
            or base in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
            or base in {f"{prefix}{n}" for prefix in ("COM", "LPT") for n in "123456789¹²³"}
        ):
            return False
    return True


def permissions(path: Path) -> Permissions:
    return _windows.permissions(path) if WINDOWS else stat.S_IMODE(path.stat().st_mode)


def secure_mode(path: Path, mode: Permissions, *, fd: int | None = None) -> None:
    if WINDOWS:
        if isinstance(mode, str):
            _windows.restore_permissions(path, mode)
        else:
            _windows.secure(path, directory=path.is_dir(), fd=fd)
    elif fd is not None:
        assert isinstance(mode, int)
        os.fchmod(fd, mode)
    else:
        assert isinstance(mode, int)
        path.chmod(mode)


def open_checked(path: Path, flags: int, mode: int = 0o600) -> int:
    if WINDOWS:
        return _windows.open_checked(path, flags)
    return os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, mode)


def fd_owned(fd: int) -> bool:
    if WINDOWS:
        return _windows.fd_owned(fd)
    return os.fstat(fd).st_uid == os.getuid()


def lock_file(fd: int) -> None:
    if WINDOWS:
        _windows.lock_file(fd)
    else:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def ssh_command(name: str) -> str:
    """Use one consistent native OpenSSH suite on Windows, including its agent."""
    if not WINDOWS:
        return name
    system = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    path = system / "System32" / "OpenSSH" / (name + ".exe")
    if not path.is_file():
        raise FileNotFoundError(
            "Windows OpenSSH Client is missing. Install it in Settings > System > "
            "Optional features > OpenSSH Client, then open a new terminal."
        )
    return str(path)


@contextmanager
def external_commands():
    """Do not pass a frozen program's DLL search directory to Git or OpenSSH."""
    if WINDOWS and getattr(sys, "frozen", False):
        import ctypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        set_directory = kernel.SetDllDirectoryW
        set_directory.argtypes = [ctypes.c_wchar_p]
        set_directory.restype = ctypes.c_int
        if not set_directory(None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            if not set_directory(sys._MEIPASS):
                raise ctypes.WinError(ctypes.get_last_error())
    else:
        yield
