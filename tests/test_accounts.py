"""Account setup, arbitrary direct switches, persistence and failure protection."""

import json
from pathlib import Path

import pytest
from conftest import assert_permissions, public_key
from rich.text import Text
from typer.testing import CliRunner

from ghssh import cli
from ghssh.account_manager import AccountManager
from ghssh.switch_manager import SwitchManager
from ghssh.utils import GhsshError, atomic_write

runner = CliRunner()


def snapshot(home):
    return {str(p.relative_to(home)): p.read_bytes() for p in home.rglob("*") if p.is_file()}


def add(accounts, name):
    return accounts.add(name, name.title(), f"{name}@example.com", no_passphrase=True)


def test_create_three_accounts_and_switch_freely_without_agent(manager):
    accounts = AccountManager(manager)
    for name in ("arsam", "arvand", "work"):
        add(accounts, name)
    assert not manager.layout.config.exists()
    assert not manager.runner.loaded
    assert_permissions(manager.layout.accounts, 0o600)
    assert_permissions(manager.layout.ssh, 0o700)
    before = {p: p.read_bytes() for p in manager.layout.ssh.glob("github_*")}
    for name in ("arsam", "arvand", "work", "arsam", "arsam", "work"):
        change = accounts.switch(name, ssh_only=True)
        assert manager.config.read().identity(manager.layout).name == "github_" + name
        manager.backups.validate(change.backup)
        assert not manager.layout.switch_state.exists()
    assert not manager.runner.loaded
    assert all(p.read_bytes() == data for p, data in before.items())
    assert not any(call[0][0] in {"ssh-add", "ssh-agent"} for call in manager.runner.calls)


def test_import_retains_existing_keys_config_and_agent(configured):
    manager = configured
    key = manager.config.read().identity(manager.layout)
    original = key.read_bytes(), Path(str(key) + ".pub").read_bytes(), manager.layout.config.read_bytes()
    loaded = manager.runner.loaded.copy()
    account = AccountManager(manager).add("Arsam", "Arsam", "arsam@example.com", key=key.name)
    assert account.key == key.name
    assert "arsam" in AccountManager(manager).read()
    assert (key.read_bytes(), Path(str(key) + ".pub").read_bytes(), manager.layout.config.read_bytes()) == original
    assert manager.runner.loaded == loaded
    assert manager.ssh.owned(key)


def test_duplicate_name_and_key_refused(configured):
    accounts = AccountManager(configured)
    accounts.add("arsam", "Arsam", "arsam@example.com", key="github_old")
    before = snapshot(configured.layout.home)
    with pytest.raises(GhsshError, match="already exists"):
        add(accounts, "ARSAM")
    with pytest.raises(GhsshError, match="another account"):
        accounts.add("other", "Other", "other@example.com", key="github_old")
    assert snapshot(configured.layout.home) == before


def test_import_does_not_claim_ownership_of_unmanaged_key(manager):
    manager.layout.ensure()
    key = manager.layout.ssh / "id_ed25519"
    public = public_key("existing unmanaged account")
    key.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n" + public + "\n")
    Path(str(key) + ".pub").write_text(public + "\n")
    AccountManager(manager).add("arsam", "Arsam", "arsam@example.com", key=key.name)
    assert not manager.layout.state.exists()
    assert not manager.ssh.owned(key)


@pytest.mark.parametrize("name", ["", "../work", "work account", "a/b", "-bad", "a" * 49])
def test_unsafe_names_refused_without_writes(manager, name):
    with pytest.raises(GhsshError, match="nickname"):
        add(AccountManager(manager), name)
    assert not manager.layout.ssh.exists()


def test_key_fingerprint_change_blocks_switch(manager):
    accounts = AccountManager(manager)
    add(accounts, "arsam")
    add(accounts, "work")
    path = manager.layout.ssh / "github_arsam"
    other = manager.layout.ssh / "github_work"
    path.write_bytes(other.read_bytes())
    Path(str(path) + ".pub").write_bytes(Path(str(other) + ".pub").read_bytes())
    before = snapshot(manager.layout.home)
    with pytest.raises(GhsshError, match="has changed"):
        accounts.switch("arsam", ssh_only=True)
    assert snapshot(manager.layout.home) == before


def test_import_requires_private_key(manager):
    accounts = AccountManager(manager)
    add(accounts, "arsam")
    (manager.layout.ssh / "github_arsam").unlink()
    with pytest.raises(GhsshError, match="does not exist"):
        accounts.add("other", "Other", "other@example.com", key="github_arsam")


@pytest.mark.parametrize("change", [
    lambda data: data.update(version=99),
    lambda data: data["accounts"]["arsam"].update(key="../outside"),
    lambda data: data["accounts"]["arsam"].update(git_email="bad\nemail"),
    lambda data: data["accounts"]["arsam"].update(fingerprint="SHA256:bad"),
])
def test_corrupt_account_file_fails_closed(manager, change):
    accounts = AccountManager(manager)
    add(accounts, "arsam")
    data = json.loads(manager.layout.accounts.read_bytes())
    change(data)
    manager.layout.accounts.write_text(json.dumps(data))
    with pytest.raises(GhsshError, match="Cannot read saved accounts"):
        accounts.read()


def test_external_account_edit_blocks_confirmed_add(manager):
    accounts = AccountManager(manager)
    plan = accounts.plan_add("arsam", "Arsam", "arsam@example.com")
    add(accounts, "work")
    with pytest.raises(GhsshError, match="changed since"):
        accounts.add("arsam", "Arsam", "arsam@example.com", expected=plan, no_passphrase=True)
    assert not (manager.layout.ssh / "github_arsam").exists()


def test_add_rolls_back_generated_keys_on_registry_failure(manager, monkeypatch):
    accounts = AccountManager(manager)
    def fail(*args):
        raise OSError("injected save failure")
    monkeypatch.setattr(accounts, "_write", fail)
    with pytest.raises(OSError, match="save failure"):
        add(accounts, "arsam")
    assert not (manager.layout.ssh / "github_arsam").exists()
    assert not (manager.layout.ssh / "github_arsam.pub").exists()
    assert not manager.layout.state.exists()
    assert not manager.layout.accounts.exists()


def test_forget_retains_key_and_active_config(manager):
    accounts = AccountManager(manager)
    add(accounts, "arsam")
    accounts.switch("arsam", ssh_only=True)
    before = manager.layout.config.read_bytes()
    accounts.forget("arsam")
    assert accounts.read() == {}
    assert manager.layout.config.read_bytes() == before
    assert (manager.layout.ssh / "github_arsam").is_file()


def test_rotate_updates_saved_account_and_protects_its_key(configured):
    accounts = AccountManager(configured)
    accounts.add("arsam", "Arsam", "arsam@example.com", key="github_old")
    configured.add("github_new", "new", replace=True, no_passphrase=True)
    assert accounts.read()["arsam"].key == "github_new"
    accounts.switch("arsam", ssh_only=True)
    with pytest.raises(GhsshError, match="saved account"):
        configured.remove(delete_files=True)
    accounts.forget("arsam")
    configured.remove(delete_files=True)
    assert not (configured.layout.ssh / "github_new").exists()


def test_old_return_points_still_work(configured):
    accounts = AccountManager(configured)
    add(accounts, "arsam")
    switcher = SwitchManager(configured)
    switcher.start("github_arsam")
    with pytest.raises(GhsshError, match="switch --back once"):
        accounts.switch("arsam", ssh_only=True)
    switcher.back()
    accounts.switch("arsam", ssh_only=True)
    assert not configured.layout.switch_state.exists()


def test_setup_wizard_reuses_current_key(configured, monkeypatch):
    monkeypatch.setattr(cli, "Manager", lambda: configured)
    before = configured.layout.config.read_bytes()
    result = runner.invoke(cli.app, ["add"], input="arsam\ngithub_old\nArsam\narsam@example.com\ny\n")
    assert result.exit_code == 0, result.output
    assert "Saved account 'arsam'" in result.output
    assert configured.layout.config.read_bytes() == before
    result = runner.invoke(cli.app, ["switch", "arsam", "--ssh-only"])
    assert result.exit_code == 0, result.output
    assert "Switched to 'arsam'" in result.output


def test_named_dry_run_and_inspection_are_read_only(manager, monkeypatch):
    monkeypatch.setattr(cli, "Manager", lambda: manager)
    add(AccountManager(manager), "arsam")
    before = snapshot(manager.layout.home)
    for command in (["switch", "arsam", "--ssh-only", "--dry-run"], ["accounts"], ["guide"],
                    ["forget", "arsam", "--dry-run"]):
        result = runner.invoke(cli.app, command)
        assert result.exit_code == 0, result.output
    assert snapshot(manager.layout.home) == before


def test_missing_name_and_unknown_account_errors_are_actionable(manager, monkeypatch):
    monkeypatch.setattr(cli, "Manager", lambda: manager)
    for args in (["add", "--yes"], ["switch"], ["switch", "missing"]):
        result = runner.invoke(cli.app, args)
        assert result.exit_code != 0
        assert "ghssh add" in result.output
    assert not manager.layout.ssh.exists()


def test_shared_addkeystoagent_setting_refused(manager):
    accounts = AccountManager(manager)
    add(accounts, "arsam")
    atomic_write(manager.layout, manager.layout.config, b"AddKeysToAgent yes\n")
    with pytest.raises(GhsshError, match="AddKeysToAgent"):
        accounts.switch("arsam", ssh_only=True)


@pytest.mark.parametrize("force_color", ["0", "1"])
def test_help_explains_setup_switch_and_scope(force_color):
    for args, phrases in [
        (["--help"], ["ghssh add arsam", "ghssh accounts", "ghssh guide"]),
        (["add", "--help"], ["existing key", "commit", "passphrase"]),
        (["switch", "--help"], ["any number", "--repo", "--ssh-only", "future commits"]),
    ]:
        result = runner.invoke(cli.app, args, terminal_width=110, env={"FORCE_COLOR": force_color})
        assert result.exit_code == 0, result.output
        text = " ".join(Text.from_ansi(result.output).plain.split())
        for phrase in phrases:
            assert phrase in text
