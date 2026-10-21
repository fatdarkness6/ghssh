import json
import os
import subprocess

import pytest
from conftest import public_key

from ghssh.github_manager import RemoteKey
from ghssh.ssh_manager import public_fingerprint, public_material
from ghssh.utils import GhsshError, Runner


def test_fingerprint_detection_and_command(configured):
    manager = configured
    key = manager.config.read().identity(manager.layout)
    public = manager.ssh.public_text(key)
    assert manager.ssh.fingerprint(key) == public_fingerprint(public)
    assert ["ssh-keygen", "-E", "sha256", "-lf", str(key)] in [
        call[0] for call in manager.runner.calls
    ]


@pytest.mark.parametrize(
    "text",
    ["", "not a key", "ssh-ed25519 ???", "ssh-ed25519 YQ==", public_key() + "\n" + public_key()],
)
def test_invalid_public_keys(text):
    with pytest.raises(GhsshError):
        public_material(text)


def test_key_discovery_ignores_unrelated_files(configured):
    manager = configured
    (manager.layout.ssh / "known_hosts").write_text("github.com " + public_key())
    (manager.layout.ssh / "random").write_text("some file")
    (manager.layout.ssh / "orphan.pub").write_text(public_key("orphan"))
    pairs = manager.ssh.discover()
    assert len(pairs) == 2
    orphan = next(pair for pair in pairs if pair.public.name == "orphan.pub")
    assert orphan.private is None
    assert all(pair.fingerprint for pair in pairs)


def test_command_runner_uses_arrays_and_no_shell(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "output", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    Runner().run(["echo", "$(touch /tmp/never)"])
    assert calls[0][0] == ["echo", "$(touch /tmp/never)"]
    assert calls[0][1]["shell"] is False


def test_missing_command_and_timeout(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(GhsshError, match="not installed"):
        Runner().run(["missing"])

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("ssh", 30)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(GhsshError, match="timed out"):
        Runner().run(["ssh"])


@pytest.mark.parametrize(
    "code,output,success",
    [
        (
            1,
            "Hi tester! You've successfully authenticated, but GitHub does not provide shell access.",
            True,
        ),
        (
            0,
            "Hi org/repo! You've successfully authenticated, but GitHub does not provide shell access.",
            True,
        ),
        (255, "Permission denied (publickey).", False),
        (1, "Network is unreachable", False),
        (
            255,
            "Hi tester! You've successfully authenticated, but GitHub does not provide shell access.",
            False,
        ),
    ],
)
def test_github_auth_response(manager, code, output, success):
    manager.runner.ssh_code = code
    manager.runner.ssh_output = output
    assert manager.ssh.test_github()[0] is success
    args = manager.runner.calls[-1][0]
    assert args[-1] == "git@github.com"
    assert "BatchMode=yes" in args
    assert not any("StrictHostKeyChecking=no" in value for value in args)


def test_remote_matching_uses_fingerprint_not_title(manager):
    public = public_key("right")
    fp = public_fingerprint(public)
    manager.runner.remote = json.dumps(
        [
            [{"id": 1, "title": "github_ed25519", "key": public_key("wrong")}],
            [{"id": 2, "title": "unrelated filename", "key": public}],
        ]
    )
    matches = manager.github.matching(fp, manager.ssh)
    assert [key.id for key in matches] == [2]
    assert not manager.layout.ssh.exists()


def test_remote_deletion_rechecks_match(manager):
    key = RemoteKey(12, "key", public_fingerprint(public_key()))
    manager.runner.remote = "[[]]"
    with pytest.raises(GhsshError, match="changed since confirmation"):
        manager.github.delete(key, manager.ssh)
    assert not any("DELETE" in args for args, _ in manager.runner.calls)


def test_remote_deletion_command(manager):
    public = public_key()
    key = RemoteKey(12, "key", public_fingerprint(public))
    manager.runner.remote = json.dumps([[{"id": 12, "key": public}]])
    manager.github.delete(key, manager.ssh)
    assert manager.runner.calls[-1][0] == [
        "gh",
        "api",
        "--hostname",
        "github.com",
        "--method",
        "DELETE",
        "user/keys/12",
    ]


def test_upload_only_public_key_and_host_pinned(configured):
    manager = configured
    public = manager.config.read().identity(manager.layout).with_suffix(".pub")
    manager.github.add(public, "title")
    args, kwargs = manager.runner.calls[-1]
    assert args == [
        "gh",
        "ssh-key",
        "add",
        str(public),
        "--title",
        "title",
        "--type",
        "authentication",
    ]
    assert kwargs["env"]["GH_HOST"] == "github.com"


def test_private_key_upload_rejected(configured):
    manager = configured
    private = manager.config.read().identity(manager.layout)
    before = len(manager.runner.calls)
    with pytest.raises(GhsshError, match="public key"):
        manager.github.add(private, "never upload this")
    assert len(manager.runner.calls) == before


def test_account_switch_blocks_remote_deletion(manager, monkeypatch):
    monkeypatch.setattr(manager.github, "require_account", lambda: "different-account")
    key = RemoteKey(12, "key", public_fingerprint(public_key()))
    with pytest.raises(GhsshError, match="account changed"):
        manager.github.delete(key, manager.ssh, account="confirmed-account")
    assert not manager.runner.calls


def test_account_switch_blocks_upload(configured, monkeypatch):
    manager = configured
    monkeypatch.setattr(manager.github, "require_account", lambda: "different-account")
    public = manager.config.read().identity(manager.layout).with_suffix(".pub")
    with pytest.raises(GhsshError, match="account changed"):
        manager.github.add(public, "title", account="confirmed-account")


@pytest.mark.skipif(os.name == "nt", reason="Unix agents use sockets; Windows uses a service")
def test_agent_start_uses_explicit_socket_and_environment(manager):
    manager.layout.ensure()
    manager.runner.agent = False
    manager.ssh.ensure_agent()
    args, kwargs = next(call for call in manager.runner.calls if call[0][0] == "ssh-agent")
    socket = args[args.index("-a") + 1]
    assert "-a" in args and socket.startswith("/tmp/ghssh-agent-") and len(socket) < 108
    assert kwargs["env"]["SSH_AUTH_SOCK"] == "/tmp/fake-agent/socket"
    manager.ssh.stop_started_agent()
