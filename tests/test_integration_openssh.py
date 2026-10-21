"""Opt-in integration check using only disposable local keys and a dedicated agent."""

import os
import shutil

import pytest

from ghssh.manager import Manager
from ghssh.switch_manager import SwitchManager
from ghssh.utils import Layout, Runner


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("GHSSH_RUN_INTEGRATION") != "1" or os.name == "nt",
    reason="opt-in Linux agent test; Windows uses the service-free switch test",
)
def test_real_openssh_lifecycle(tmp_path, monkeypatch):
    for tool in ("ssh", "ssh-keygen", "ssh-agent", "ssh-add"):
        if not shutil.which(tool):
            pytest.skip(f"{tool} is not installed")
    manager = Manager(Layout(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    manager.ssh.env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "SSH_AUTH_SOCK": str(tmp_path / "no-agent"),
    }
    try:
        first = manager.add("github_original", "integration", no_passphrase=True)
        assert manager.ssh.owned(first.new_key)
        original_fp = manager.ssh.identity_fingerprint(first.new_key)
        second = manager.add("github_rotated", "integration", replace=True, no_passphrase=True)
        assert manager.backups.validate(second.backup).fingerprint == original_fp
        manager.restore(second.backup.name)
        assert manager.config.read().identity(manager.layout) == first.new_key
        manager.remove(delete_files=True)
        assert not first.new_key.exists()
        assert second.new_key.exists()
        # Encrypted OpenSSH keys also expose a public fingerprint without decrypting them.
        encrypted = tmp_path / ".ssh/encrypted"
        Runner().run(
            ["ssh-keygen", "-t", "ed25519", "-f", str(encrypted), "-N", "disposable-test-only"]
        )
        assert manager.ssh.identity_fingerprint(encrypted).startswith("SHA256:")
        # Temporarily select an encrypted second-account key without agent changes.
        # At this point the original GitHub block was removed, so this also checks
        # restoration of implicit/default SSH behavior.
        before_agent = manager.ssh.agent_fingerprints()
        repository = tmp_path / "repository"
        repository.mkdir()
        git = Runner()
        git.run(["git", "init", "--quiet", str(repository)])
        git.run(["git", "-C", str(repository), "config", "user.name", "Original Account"])
        git.run(["git", "-C", str(repository), "config", "user.email", "original@example.com"])
        switcher = SwitchManager(manager)
        switcher.start(
            encrypted.name,
            repository=repository,
            git_name="Second Account",
            git_email="second@example.com",
        )
        effective = (
            Runner().run(["ssh", "-G", "-F", str(manager.layout.config), "github.com"]).stdout
        )
        assert f"identityfile {encrypted}" in effective
        assert "identitiesonly yes" in effective
        assert "addkeystoagent false" in effective or "addkeystoagent no" in effective
        git.run(
            [
                "git",
                "-C",
                str(repository),
                "commit",
                "--allow-empty",
                "--quiet",
                "-m",
                "Switched identity",
            ]
        )
        authored = git.run(
            ["git", "-C", str(repository), "log", "-1", "--format=%an <%ae>"]
        ).stdout.strip()
        assert authored == "Second Account <second@example.com>"
        switcher.back()
        assert manager.config.read().target() is None
        assert manager.ssh.agent_fingerprints() == before_agent
        assert encrypted.exists()
        assert manager.git.effective(repository, "user.name") == "Original Account"
        assert manager.git.effective(repository, "user.email") == "original@example.com"
        assert (
            git.run(
                ["git", "-C", str(repository), "log", "-1", "--format=%an <%ae>"]
            ).stdout.strip()
            == authored
        )
    finally:
        manager.ssh.stop_started_agent()
