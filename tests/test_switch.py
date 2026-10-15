import json
from pathlib import Path

import pytest
from conftest import public_key
from typer.testing import CliRunner

from ghssh import cli
from ghssh.switch_manager import SwitchManager
from ghssh.utils import GhsshError


def alternate(manager, name="github_second"):
    manager.layout.ensure()
    path = manager.layout.ssh / name
    text = public_key(name)
    path.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n" + text + "\n")
    Path(str(path) + ".pub").write_text(text + "\n")
    return path


def snapshot(manager):
    return {
        str(path.relative_to(manager.layout.home)): (
            path.stat().st_mode,
            path.read_bytes() if path.is_file() else None,
        )
        for path in manager.layout.home.rglob("*")
    }


def test_switch_existing_and_return_keeps_both_keys_and_agent(configured):
    manager = configured
    second = alternate(manager)
    original = manager.config.read().identity(manager.layout)
    original_config = manager.layout.config.read_text()
    original_keys = (original.read_bytes(), second.read_bytes())
    loaded = manager.runner.loaded.copy()
    switcher = SwitchManager(manager)
    change = switcher.start(second.name)
    assert change.backup.is_dir()
    assert manager.config.read().identity(manager.layout) == second
    assert manager.layout.switch_state.stat().st_mode & 0o777 == 0o600
    assert switcher.read().active_key == second
    assert "AddKeysToAgent no" in manager.layout.config.read_text()
    switcher.back()
    assert manager.layout.config.read_text() == original_config
    assert (original.read_bytes(), second.read_bytes()) == original_keys
    assert manager.runner.loaded == loaded
    assert not manager.layout.switch_state.exists()


def test_create_secondary_then_reuse_without_regenerating(configured):
    manager = configured
    switcher = SwitchManager(manager)
    switcher.start("github_second", create=True, no_passphrase=True)
    second = manager.config.read().identity(manager.layout)
    original_data = second.read_bytes()
    assert manager.ssh.owned(second)
    switcher.back()
    manager.runner.calls.clear()
    switcher.start("github_second")
    assert manager.config.read().identity(manager.layout) == second
    assert second.read_bytes() == original_data
    assert not any(args[0] == "ssh-keygen" and "-t" in args for args, _ in manager.runner.calls)
    assert not any(args[0] in {"ssh-agent", "ssh-add"} for args, _ in manager.runner.calls)


def test_original_implicit_default_identity_restored(manager):
    original = alternate(manager, "id_ed25519")
    data = original.read_bytes()
    switcher = SwitchManager(manager)
    switcher.start("github_second", create=True, no_passphrase=True)
    assert switcher.read().previous_section == ""
    assert manager.config.read().identity(manager.layout).name == "github_second"
    switcher.back()
    assert not manager.layout.config.exists()
    assert original.read_bytes() == data
    assert (manager.layout.ssh / "github_second").exists()


def test_other_host_edits_survive_return(configured):
    manager = configured
    second = alternate(manager)
    switcher = SwitchManager(manager)
    switcher.start(second.name)
    with manager.layout.config.open("a") as stream:
        stream.write("Host vps.example\n    Port 2222\n")
    switcher.back()
    assert "Host vps.example\n    Port 2222\n" in manager.layout.config.read_text()
    assert manager.config.read().identity(manager.layout).name == "github_old"


def test_nesting_refused(configured):
    manager = configured
    switcher = SwitchManager(manager)
    switcher.start("github_second", create=True, no_passphrase=True)
    before = snapshot(manager)
    with pytest.raises(GhsshError, match="switch --back"):
        switcher.start("github_third", create=True, no_passphrase=True)
    assert snapshot(manager) == before


@pytest.mark.parametrize("action", ["replace", "remove", "restore"])
def test_destructive_workflows_protect_return_point(configured, action):
    manager = configured
    switcher = SwitchManager(manager)
    change = switcher.start("github_second", create=True, no_passphrase=True)
    with pytest.raises(GhsshError, match="switch --back"):
        if action == "replace":
            manager.add("github_third", "", replace=True, no_passphrase=True)
        elif action == "remove":
            manager.remove()
        else:
            manager.restore(change.backup.name)
    assert switcher.read() is not None


def test_existing_key_mismatch_refused(configured):
    manager = configured
    second = alternate(manager)
    Path(str(second) + ".pub").write_text(public_key("wrong"))
    before = snapshot(manager)
    with pytest.raises(GhsshError, match="fingerprints differ"):
        SwitchManager(manager).start(second.name)
    assert snapshot(manager) == before


def test_external_github_edit_blocks_automatic_return(configured):
    manager = configured
    switcher = SwitchManager(manager)
    switcher.start("github_second", create=True, no_passphrase=True)
    manager.layout.config.write_text(
        manager.layout.config.read_text().replace("User git", "User changed")
    )
    before = snapshot(manager)
    with pytest.raises(GhsshError, match="configuration changed"):
        switcher.back()
    assert snapshot(manager) == before


def test_previous_key_changed_blocks_return(configured):
    manager = configured
    original = manager.config.read().identity(manager.layout)
    switcher = SwitchManager(manager)
    switcher.start("github_second", create=True, no_passphrase=True)
    alternate(manager, original.name)
    with pytest.raises(GhsshError, match="fingerprint changed"):
        switcher.back()


def test_create_failure_rolls_back_return_point_and_keys(configured, monkeypatch):
    manager = configured
    original_config = manager.layout.config.read_bytes()
    original_state = manager.layout.state.read_bytes()

    def fail(*args):
        raise GhsshError("injected config failure")

    monkeypatch.setattr(manager.config, "write", fail)
    with pytest.raises(GhsshError):
        SwitchManager(manager).start("github_second", create=True, no_passphrase=True)
    assert manager.layout.config.read_bytes() == original_config
    assert manager.layout.state.read_bytes() == original_state
    assert not manager.layout.switch_state.exists()
    assert not (manager.layout.ssh / "github_second").exists()


def test_return_failure_keeps_session_and_current_identity(configured, monkeypatch):
    manager = configured
    switcher = SwitchManager(manager)
    switcher.start("github_second", create=True, no_passphrase=True)
    original_config = manager.layout.config.read_bytes()

    def fail(*args):
        raise GhsshError("injected config failure")

    monkeypatch.setattr(manager.config, "write", fail)
    with pytest.raises(GhsshError):
        switcher.back()
    assert manager.layout.config.read_bytes() == original_config
    assert manager.layout.switch_state.exists()


def test_corrupt_switch_state_cannot_redirect_return(configured):
    manager = configured
    switcher = SwitchManager(manager)
    switcher.start("github_second", create=True, no_passphrase=True)
    data = json.loads(manager.layout.switch_state.read_text())
    data["active_key"] = "../outside"
    manager.layout.switch_state.write_text(json.dumps(data))
    with pytest.raises(GhsshError):
        switcher.back()


def test_automatic_agent_loading_disabled_in_temporary_block(configured):
    manager = configured
    with manager.layout.config.open("a") as stream:
        stream.write("    AddKeysToAgent yes\n")
    switcher = SwitchManager(manager)
    switcher.start("github_second", create=True, no_passphrase=True)
    assert "AddKeysToAgent no" in manager.layout.config.read_text()
    switcher.back()
    assert "AddKeysToAgent yes" in manager.layout.config.read_text()


def test_global_automatic_agent_loading_refused(configured):
    manager = configured
    manager.layout.config.write_text("AddKeysToAgent yes\n" + manager.layout.config.read_text())
    with pytest.raises(GhsshError, match="Global/wildcard AddKeysToAgent"):
        SwitchManager(manager).plan("github_second", create=True)


@pytest.mark.parametrize(
    "args",
    [["switch", "github_second", "--dry-run"], ["switch", "github_new", "--create", "--dry-run"]],
)
def test_switch_dry_run_changes_nothing(configured, monkeypatch, args):
    manager = configured
    alternate(manager)
    monkeypatch.setattr(cli, "Manager", lambda: manager)
    before = snapshot(manager)
    manager.runner.calls.clear()
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert snapshot(manager) == before
    assert not any(args[0] in {"ssh-agent", "ssh-add", "gh"} for args, _ in manager.runner.calls)


def test_return_dry_run_changes_nothing(configured, monkeypatch):
    manager = configured
    SwitchManager(manager).start("github_second", create=True, no_passphrase=True)
    monkeypatch.setattr(cli, "Manager", lambda: manager)
    before = snapshot(manager)
    result = CliRunner().invoke(cli.app, ["switch", "--back", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert snapshot(manager) == before


def test_switch_cli_create_and_back(configured, monkeypatch):
    manager = configured
    monkeypatch.setattr(cli, "Manager", lambda: manager)
    runner = CliRunner()
    result = runner.invoke(
        cli.app, ["switch", "github_second", "--create", "--no-passphrase", "--yes"]
    )
    assert result.exit_code == 0, result.output
    assert "SECOND GitHub account" in result.output
    assert "PRIVATE KEY" not in result.output
    result = runner.invoke(cli.app, ["switch", "--back", "--yes"])
    assert result.exit_code == 0, result.output
    assert manager.config.read().identity(manager.layout).name == "github_old"


def test_switch_cancel_changes_nothing(configured, monkeypatch):
    manager = configured
    alternate(manager)
    monkeypatch.setattr(cli, "Manager", lambda: manager)
    before = snapshot(manager)
    result = CliRunner().invoke(cli.app, ["switch", "github_second"], input="n\n")
    assert result.exit_code == 130
    assert snapshot(manager) == before


def test_back_without_active_switch_explains(manager):
    with pytest.raises(GhsshError, match="No temporary switch"):
        SwitchManager(manager).back()
