from __future__ import annotations

import base64
import hashlib
import struct
import subprocess
from pathlib import Path

import pytest

from ghssh.manager import Manager
from ghssh.platform_support import WINDOWS
from ghssh.ssh_manager import public_fingerprint
from ghssh.utils import GhsshError, Layout


def assert_permissions(path: Path, expected: int) -> None:
    if not WINDOWS:
        assert path.stat().st_mode & 0o777 == expected
        return
    import win32con
    import win32security

    from ghssh._windows import user_sid

    info = win32security.GetNamedSecurityInfo(
        str(path), win32security.SE_FILE_OBJECT,
        win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION,
    )
    assert info.GetSecurityDescriptorOwner() == user_sid()
    assert info.GetSecurityDescriptorControl()[0] & win32security.SE_DACL_PROTECTED
    acl = info.GetSecurityDescriptorDacl()
    assert acl is not None and acl.GetAceCount() == 1
    header, mask, sid = acl.GetAce(0)
    assert header[0] == win32security.ACCESS_ALLOWED_ACE_TYPE
    assert not header[1] & win32con.INHERITED_ACE
    assert sid == user_sid()
    assert mask == win32con.FILE_ALL_ACCESS


def public_key(seed: str = "test") -> str:
    kind = b"ssh-ed25519"
    payload = hashlib.sha256(seed.encode()).digest()
    blob = struct.pack(">I", len(kind)) + kind + struct.pack(">I", len(payload)) + payload
    return "ssh-ed25519 " + base64.b64encode(blob).decode() + " test@example.com"


class FakeRunner:
    """Mock the command boundary while exercising real temporary filesystem changes."""

    def __init__(self):
        self.calls = []
        self.loaded: set[str] = set()
        self.agent = True
        self.fail_on: str | None = None
        self.ssh_code = 1
        self.ssh_output = "Hi tester! You've successfully authenticated, but GitHub does not provide shell access."
        self.remote = "[]"

    def run(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        if self.fail_on and self.fail_on in " ".join(args):
            raise GhsshError("injected command failure")
        stdout, stderr, code = "", "", 0
        if args[0] == "ssh-keygen" and "-t" in args:
            path = Path(args[args.index("-f") + 1])
            public = public_key(str(path))
            path.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n" + public + "\n")
            Path(str(path) + ".pub").write_text(public + "\n")
        elif args[0] == "ssh-keygen" and "-lf" in args:
            path = Path(args[args.index("-lf") + 1])
            text = path.read_text().strip()
            if text.startswith("-----"):
                text = text.splitlines()[1]
            stdout = f"256 {public_fingerprint(text)} test@example.com (ED25519)\n"
        elif args[0] == "ssh-add":
            if "-l" in args:
                code = (0 if self.loaded else 1) if self.agent else 2
                stdout = "".join(f"256 {fp} test (ED25519)\n" for fp in self.loaded)
            elif "-d" in args:
                text = Path(args[-1]).read_text().strip()
                if text.startswith("-----"):
                    text = text.splitlines()[1]
                self.loaded.discard(public_fingerprint(text))
            else:
                text = Path(args[-1] + ".pub").read_text()
                self.loaded.add(public_fingerprint(text))
        elif args[0] == "ssh-agent" and "-s" in args:
            stdout = "SSH_AUTH_SOCK=/tmp/fake-agent/socket; export SSH_AUTH_SOCK;\nSSH_AGENT_PID=1234; export SSH_AGENT_PID;\n"
            self.agent = True
        elif args[0] == "ssh" and "-G" not in args:
            code, stderr = self.ssh_code, self.ssh_output
        elif args[:2] == ["gh", "api"]:
            stdout = "tester\n" if ".login" in args else self.remote
        return subprocess.CompletedProcess(args, code, stdout, stderr)


@pytest.fixture
def manager(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    layout = Layout(home)
    fake = FakeRunner()
    monkeypatch.setattr("ghssh.ssh_manager.shutil.which", lambda name: "/usr/bin/" + name)
    return Manager(layout, fake)


@pytest.fixture
def configured(manager):
    manager.add("github_old", "old", no_passphrase=True)
    return manager
