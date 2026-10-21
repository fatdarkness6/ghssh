"""Real OpenSSH and Git on either OS, without a network connection or agent."""

import os
from pathlib import Path

import pytest

from ghssh.manager import Manager
from ghssh.platform_support import secure_mode
from ghssh.switch_manager import SwitchManager
from ghssh.utils import Layout, Runner, atomic_write


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("GHSSH_RUN_INTEGRATION") != "1", reason="opt-in real OpenSSH/Git check"
)
def test_real_switch_with_spaces_and_restore(tmp_path, monkeypatch):
    home = tmp_path / "profile with spaces"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for key in tuple(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    manager = Manager(Layout(home))
    manager.layout.ensure()
    original = manager.layout.ssh / "original key"
    manager.ssh.generate(original, "Original", no_passphrase=True)
    atomic_write(
        manager.layout, manager.layout.config,
        manager.config.read().upsert(manager.identity_value(original)).encode(),
    )
    before = manager.layout.config.read_bytes()
    repository = home / "repository with spaces"
    repository.mkdir()
    runner = Runner()
    runner.run(["git", "init", "--quiet", str(repository)])
    runner.run(["git", "-C", str(repository), "config", "user.name", "Original Account"])
    runner.run(["git", "-C", str(repository), "config", "user.email", "original@example.com"])
    switcher = SwitchManager(manager)
    change = switcher.start(
        "github_second", create=True, no_passphrase=True,
        repository=repository, git_name="Second Account", git_email="second@example.com",
    )
    assert manager.backups.validate(change.backup).identity == original
    effective = runner.run(["ssh", "-G", "-F", str(manager.layout.config), "github.com"]).stdout
    assert (manager.layout.ssh / "github_second").as_posix() in effective
    assert "identitiesonly yes" in effective
    assert "addkeystoagent no" in effective or "addkeystoagent false" in effective
    runner.run(["git", "-C", str(repository), "commit", "--allow-empty", "--quiet", "-m", "Switched"])
    switcher.back()
    assert manager.layout.config.read_bytes() == before
    assert manager.git.effective(repository, "user.email") == "original@example.com"
    assert runner.run(["git", "-C", str(repository), "log", "-1", "--format=%ae"]).stdout.strip() == "second@example.com"
    assert not manager.layout.switch_state.exists()
    assert original.is_file() and (manager.layout.ssh / "github_second").is_file()
    secure_mode(repository / ".git/config", 0o600)
    assert Path(str(original) + ".pub").is_file()
