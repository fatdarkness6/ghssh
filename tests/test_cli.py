import pytest
from typer.testing import CliRunner

from ghssh import cli

runner = CliRunner()


def snapshot(home):
    return {
        str(path.relative_to(home)): (
            path.stat().st_mode,
            path.read_bytes() if path.is_file() else None,
        )
        for path in home.rglob("*")
    }


def test_help_and_version():
    assert runner.invoke(cli.app, ["--help"]).exit_code == 0
    assert "ghssh" in runner.invoke(cli.app, ["--version"]).output


@pytest.mark.parametrize("command", ["add", "backup"])
def test_dry_run_absent_ssh_no_writes(manager, monkeypatch, command):
    monkeypatch.setattr(cli, "Manager", lambda: manager)
    before = snapshot(manager.layout.home)
    args = [command, "--dry-run"]
    if command == "add":
        args += ["arsam", "--git-name", "Arsam", "--git-email", "me@example.com"]
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert snapshot(manager.layout.home) == before
    assert all(call[0][0] == "ssh" and "-G" in call[0] for call in manager.runner.calls)


@pytest.mark.parametrize(
    "args",
    [
        ["replace", "--dry-run"],
        ["remove", "--dry-run"],
        ["remove", "--delete-key", "--dry-run"],
        ["backup", "--dry-run"],
    ],
)
def test_existing_dry_run_no_side_effects(configured, monkeypatch, args):
    manager = configured
    monkeypatch.setattr(cli, "Manager", lambda: manager)
    before = snapshot(manager.layout.home)
    loaded = manager.runner.loaded.copy()
    manager.runner.calls.clear()
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert snapshot(manager.layout.home) == before
    assert manager.runner.loaded == loaded
    assert all(
        (call[0][0] == "ssh-keygen" and "-lf" in call[0])
        or (call[0][0] == "ssh" and "-G" in call[0])
        for call in manager.runner.calls
    )


def test_restore_dry_run_no_side_effects(configured, monkeypatch):
    manager = configured
    backup = manager.backups.create()
    monkeypatch.setattr(cli, "Manager", lambda: manager)
    before = snapshot(manager.layout.home)
    result = runner.invoke(cli.app, ["restore", backup.name, "--dry-run"])
    assert result.exit_code == 0, result.output
    assert snapshot(manager.layout.home) == before


def test_add_end_to_end_mocked(manager, monkeypatch):
    monkeypatch.setattr(cli, "Manager", lambda: manager)
    result = runner.invoke(
        cli.app, ["add", "arsam", "--yes", "--no-passphrase", "--git-name", "Arsam", "--email", "me@example.com"]
    )
    assert result.exit_code == 0, result.output
    assert "PUBLIC key" in result.output
    assert "BEGIN OPENSSH PRIVATE KEY" not in result.output
    from ghssh.account_manager import AccountManager

    assert AccountManager(manager).read()["arsam"].key == "github_arsam"
    assert not manager.layout.config.exists()


def test_cancel_leaves_files_untouched(configured, monkeypatch):
    monkeypatch.setattr(cli, "Manager", lambda: configured)
    before = snapshot(configured.layout.home)
    result = runner.invoke(cli.app, ["remove", "--delete-key"], input="n\n")
    assert result.exit_code == 130, result.output
    assert snapshot(configured.layout.home) == before


def test_remove_yes_keeps_files(configured, monkeypatch):
    monkeypatch.setattr(cli, "Manager", lambda: configured)
    key = configured.config.read().identity(configured.layout)
    result = runner.invoke(cli.app, ["remove", "--yes"])
    assert result.exit_code == 0, result.output
    assert key.exists()


def test_status_and_list_dont_change_files(configured, monkeypatch):
    monkeypatch.setattr(cli, "Manager", lambda: configured)
    before = snapshot(configured.layout.home)
    for command in ["status", "list"]:
        result = runner.invoke(cli.app, [command])
        assert result.exit_code == 0, result.output
    assert snapshot(configured.layout.home) == before


def test_remote_delete_still_requires_confirmation(configured, monkeypatch):
    from ghssh.github_manager import RemoteKey

    manager = configured
    monkeypatch.setattr(cli, "Manager", lambda: manager)
    monkeypatch.setattr(manager.github, "require_account", lambda: "tester")
    monkeypatch.setattr(
        manager.github, "matching", lambda *args: [RemoteKey(42, "test key", "SHA256:test")]
    )
    before = snapshot(manager.layout.home)
    result = runner.invoke(cli.app, ["remove", "--github", "--yes"], input="n\n")
    assert result.exit_code == 130, result.output
    assert "key ID 42" in result.output
    assert snapshot(manager.layout.home) == before


def test_restore_rejects_traversal(configured, monkeypatch):
    configured.backups.create()
    monkeypatch.setattr(cli, "Manager", lambda: configured)
    result = runner.invoke(cli.app, ["restore", "../../outside", "--yes"])
    assert result.exit_code == 1
    assert "paths are not accepted" in result.output
