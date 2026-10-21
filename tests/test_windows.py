"""Windows behavior plus native filesystem tests on Windows CI."""

import os
import subprocess
from pathlib import Path, PureWindowsPath

import pytest

from ghssh import cli
from ghssh import platform_support as platform
from ghssh.utils import GhsshError, Runner, locked, read_bytes

native = pytest.mark.skipif(os.name != "nt", reason="requires real Windows security APIs")


def test_windows_validation_needs_no_temp_file(manager, monkeypatch):
    monkeypatch.setattr(platform, "WINDOWS", True)
    manager.ssh.validate_config(
        'Host github.com\r\n User git\r\n IdentityFile "C:/Users/Test/.ssh/key with spaces"\r\n'
    )
    args, options = manager.runner.calls[-1]
    assert args[:4] == ["ssh", "-G", "-F", "NUL"]
    assert 'IdentityFile "C:/Users/Test/.ssh/key with spaces"' in args
    assert not any(value.startswith("Host ") for value in args)
    assert "input" not in options
    assert not manager.layout.ssh.exists()


def test_windows_validation_rejects_overlong_command(manager, monkeypatch):
    monkeypatch.setattr(platform, "WINDOWS", True)
    with pytest.raises(GhsshError, match="too large"):
        manager.ssh.validate_config("Host github.com\n" + " ServerAliveInterval 10\n" * 1400)
    assert not manager.runner.calls


def test_windows_agent_unavailable_has_service_instructions(manager, monkeypatch):
    monkeypatch.setattr(platform, "WINDOWS", True)
    monkeypatch.setattr(manager.ssh, "agent_fingerprints", lambda: (False, set()))
    with pytest.raises(GhsshError, match="Administrator PowerShell"):
        manager.ssh.ensure_agent()
    assert not manager.runner.calls
    assert not manager.ssh.started_agent


def test_windows_agent_reused_without_starting_unix_agent(manager, monkeypatch):
    monkeypatch.setattr(platform, "WINDOWS", True)
    monkeypatch.setattr(manager.ssh, "agent_fingerprints", lambda: (True, set()))
    manager.ssh.ensure_agent()
    assert not manager.runner.calls


def test_windows_clipboard_uses_clip(monkeypatch):
    calls = []
    monkeypatch.setattr(platform, "WINDOWS", True)
    monkeypatch.setattr(cli.shutil, "which", lambda name: name)
    monkeypatch.setattr(Runner, "run", lambda self, args, **kw: calls.append((args, kw)))
    cli.copy_public("ssh-ed25519 public-key")
    assert calls == [(["clip.exe"], {"input": "ssh-ed25519 public-key\n", "timeout": 5})]


@pytest.mark.parametrize(
    "value",
    [r"C:\Users\Test\.ssh\key:secret", r"C:\Users\Test\.ssh\NUL",
     r"C:\Users\Test\.ssh\key.", r"C:\Users\Test\.ssh\key ",
     r"\\server\share\key", r"\\?\C:\Users\Test\key", r"C:\Users\Test\.ssh\COM1.txt"],
)
def test_windows_special_paths_rejected(value, monkeypatch):
    monkeypatch.setattr(platform, "WINDOWS", True)
    assert not platform.safe_parts(PureWindowsPath(value))


def test_windows_native_paths_accepted(monkeypatch):
    monkeypatch.setattr(platform, "WINDOWS", True)
    assert platform.safe_parts(PureWindowsPath("C:/Users/Test User/.ssh/key"))


@native
def test_native_identity_drive_and_spaces(manager):
    key = manager.layout.ssh / "key with spaces"
    assert manager.layout.identity_path(key.as_posix()) == key


@native
def test_native_open_refuses_hardlink(manager, tmp_path):
    manager.layout.ensure()
    original = tmp_path / "private"
    original.write_text("never read")
    path = manager.layout.ssh / "hardlink"
    path.hardlink_to(original)
    with pytest.raises(OSError, match="hard-linked"):
        read_bytes(path)


@native
def test_native_junction_refused(manager, tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(manager.layout.ssh), str(target)],
        check=True, capture_output=True,
    )
    try:
        with pytest.raises(GhsshError, match="reparse"):
            manager.layout.ensure()
    finally:
        # rmdir removes the junction itself, leaving the target directory intact.
        os.rmdir(manager.layout.ssh)


@native
def test_native_lock_excludes_a_second_process(manager):
    script = (
        "from pathlib import Path; from ghssh.utils import Layout, locked; "
        f"layout=Layout(Path({str(manager.layout.home)!r})); "
        "scope=locked(layout); scope.__enter__()"
    )
    import sys

    with locked(manager.layout):
        result = Runner().run([sys.executable, "-c", script], check=False)
    assert result.returncode != 0
    assert "Another ghssh" in result.stderr
    with locked(manager.layout):
        pass


@native
def test_native_home_mismatch_refused(monkeypatch, tmp_path):
    from ghssh.utils import Layout

    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(GhsshError, match="HOME differs"):
        Layout.default()


@native
def test_native_runner_uses_windows_openssh(monkeypatch):
    calls = []
    monkeypatch.setattr(
        subprocess, "run", lambda args, **kw: calls.append(args) or subprocess.CompletedProcess(args, 0, "", "")
    )
    Runner().run(["ssh", "-V"])
    assert Path(calls[0][0]).name == "ssh.exe"
    assert Path(calls[0][0]).parent.name == "OpenSSH"


@native
def test_native_rollback_restores_acl(manager):
    import win32con
    import win32security

    from ghssh._windows import user_sid
    from ghssh.utils import Transaction, atomic_write

    manager.layout.ensure()
    path = manager.layout.config
    atomic_write(manager.layout, path, b"original")
    acl = win32security.ACL()
    acl.AddAccessAllowedAce(win32security.ACL_REVISION, win32con.FILE_ALL_ACCESS, user_sid())
    acl.AddAccessAllowedAce(
        win32security.ACL_REVISION, win32con.FILE_GENERIC_READ,
        win32security.CreateWellKnownSid(win32security.WinLocalSystemSid, None),
    )
    win32security.SetNamedSecurityInfo(
        str(path), win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION,
        None, None, acl, None,
    )
    previous = platform.permissions(path)
    with pytest.raises(RuntimeError), Transaction(manager.layout, [path]) as transaction:
        atomic_write(manager.layout, path, b"temporary")
        transaction.mark(path)
        assert platform.permissions(path) != previous
        raise RuntimeError("trigger rollback")
    assert read_bytes(path) == b"original"
    assert platform.permissions(path) == previous
