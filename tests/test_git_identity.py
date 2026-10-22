"""Use disposable Git repositories; SSH/network commands stay mocked."""

import json
import os
from pathlib import Path

import pytest
from conftest import FakeRunner, public_key
from typer.testing import CliRunner

from ghssh import cli
from ghssh.account_manager import AccountManager
from ghssh.git_manager import GitIdentity
from ghssh.manager import Manager
from ghssh.switch_manager import SwitchManager
from ghssh.utils import GhsshError, Runner


class GitAndSSHRunner(FakeRunner):
    def run(self, args, **kwargs):
        if args[0] == "git":
            self.calls.append((list(args), kwargs))
            return Runner().run(args, **kwargs)
        return super().run(args, **kwargs)


def test_named_switches_keep_commits_in_sync_across_three_accounts(git_setup, monkeypatch):
    manager, repository, global_config = git_setup
    monkeypatch.setattr(cli, "Manager", lambda: manager)
    accounts = AccountManager(manager)
    accounts.add("arsam", "Arsam", "arsam@example.com", key="github_original")
    accounts.add("arvand", "Arvand", "arvand@example.com", key="github_second")
    accounts.add("work", "Work", "work@example.com", no_passphrase=True)
    before_global = global_config.read_bytes()
    before_agent = manager.runner.loaded.copy()
    local(repository, "core.editor", "code --wait")
    runner = CliRunner()
    for name in ("arvand", "work", "arsam", "work", "work"):
        result = runner.invoke(cli.app, ["switch", name, "--repo", str(repository)])
        assert result.exit_code == 0, result.output
        assert f"Switched to '{name}'" in result.output
        assert manager.git.effective(repository, "user.email") == f"{name}@example.com"
        assert manager.config.read().identity(manager.layout).name == accounts.read()[name].key
        assert not manager.layout.switch_state.exists()
        Runner().run(["git", "-C", str(repository), "commit", "--quiet", "--allow-empty", "-m", name])
        email = Runner().run(["git", "-C", str(repository), "log", "-1", "--format=%ae"]).stdout.strip()
        assert email == f"{name}@example.com"
    assert manager.git.effective(repository, "core.editor") == "code --wait"
    assert global_config.read_bytes() == before_global
    assert manager.runner.loaded == before_agent


def test_named_switch_rolls_back_ssh_and_git_if_verification_fails(git_setup, monkeypatch):
    manager, repository, _ = git_setup
    accounts = AccountManager(manager)
    accounts.add("arvand", "Arvand", "arvand@example.com", key="github_second")
    before_ssh = manager.layout.config.read_bytes()
    before_git = (repository / ".git/config").read_bytes()
    def fail(identity):
        raise GhsshError("injected identity failure")
    monkeypatch.setattr(manager.git, "verify_effective", fail)
    with pytest.raises(GhsshError, match="identity failure"):
        accounts.switch("arvand", repository=repository)
    assert manager.layout.config.read_bytes() == before_ssh
    assert (repository / ".git/config").read_bytes() == before_git
    assert not (repository / ".git/config.lock").exists()


def test_named_switch_dry_run_and_stale_plan_are_safe(git_setup, monkeypatch):
    manager, repository, _ = git_setup
    accounts = AccountManager(manager)
    accounts.add("arvand", "Arvand", "arvand@example.com", key="github_second")
    monkeypatch.setattr(cli, "Manager", lambda: manager)
    before_ssh = manager.layout.config.read_bytes()
    before_git = (repository / ".git/config").read_bytes()
    before_accounts = manager.layout.accounts.read_bytes()
    result = CliRunner().invoke(cli.app, ["switch", "arvand", "--repo", str(repository), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert manager.layout.config.read_bytes() == before_ssh
    assert (repository / ".git/config").read_bytes() == before_git
    assert manager.layout.accounts.read_bytes() == before_accounts
    plan = accounts.plan_switch("arvand", repository=repository)
    local(repository, "core.editor", "changed externally")
    with pytest.raises(GhsshError, match="changed since"):
        accounts.switch("arvand", repository=repository, expected=plan)
    assert manager.layout.config.read_bytes() == before_ssh


def test_named_switch_outside_repository_gives_clear_next_step(git_setup):
    manager, _, _ = git_setup
    accounts = AccountManager(manager)
    add = accounts.add("arsam", "Arsam", "arsam@example.com", no_passphrase=True)
    with pytest.raises(GhsshError, match="--repo PATH or --ssh-only"):
        accounts.switch("arsam", repository=manager.layout.home)
    assert add.key == "github_arsam"


@pytest.fixture
def git_setup(manager, monkeypatch):
    monkeypatch.setenv("HOME", str(manager.layout.home))
    for name in tuple(__import__("os").environ):
        if name.startswith("GIT_CONFIG") or name in {
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_COMMON_DIR",
            "GIT_AUTHOR_NAME",
            "GIT_AUTHOR_EMAIL",
            "GIT_COMMITTER_NAME",
            "GIT_COMMITTER_EMAIL",
        }:
            monkeypatch.delenv(name)
    global_config = manager.layout.home / ".gitconfig"
    global_config.write_text("[user]\n name = Primary Developer\n email = primary@example.com\n")
    repository = manager.layout.home / "repo"
    repository.mkdir()
    Runner().run(["git", "init", "--quiet", str(repository)])
    result = Manager(manager.layout, GitAndSSHRunner())
    result.add("github_original", "primary", no_passphrase=True)
    public = public_key("second")
    second = result.layout.ssh / "github_second"
    second.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n" + public + "\n")
    Path(str(second) + ".pub").write_text(public + "\n")
    return result, repository, global_config


def local(repository, key, value):
    Runner().run(["git", "-C", str(repository), "config", "--local", key, value])


def begin(manager, repository):
    return SwitchManager(manager).start(
        "github_second",
        repository=repository,
        git_name="Second Developer",
        git_email="second@example.com",
    )


def test_switch_and_back_restores_inherited_identity(git_setup):
    manager, repository, global_config = git_setup
    original_global = global_config.read_bytes()
    original_ssh = manager.layout.config.read_bytes()
    begin(manager, repository)
    assert manager.git.effective(repository, "user.name") == "Second Developer"
    assert manager.git.effective(repository, "user.email") == "second@example.com"
    manager.git.verify_effective(SwitchManager(manager).read().git_identity)
    state = json.loads(manager.layout.switch_state.read_text())
    assert state["version"] == 2
    assert state["git_identity"]["previous_names"] == []
    SwitchManager(manager).back()
    assert manager.git.effective(repository, "user.name") == "Primary Developer"
    assert manager.git.effective(repository, "user.email") == "primary@example.com"
    assert manager.git.values(repository / ".git/config", "user.name") == ()
    assert manager.layout.config.read_bytes() == original_ssh
    assert global_config.read_bytes() == original_global


def test_restores_existing_local_values_and_preserves_other_changes(git_setup):
    manager, repository, _ = git_setup
    local(repository, "user.name", "Previous Local")
    local(repository, "user.email", "local@example.com")
    local(repository, "remote.origin.url", "git@github.com:example/repo.git")
    begin(manager, repository)
    local(repository, "core.editor", "code --wait")
    SwitchManager(manager).back()
    assert manager.git.effective(repository, "user.name") == "Previous Local"
    assert manager.git.effective(repository, "user.email") == "local@example.com"
    assert manager.git.effective(repository, "core.editor") == "code --wait"
    assert (
        manager.git.effective(repository, "remote.origin.url") == "git@github.com:example/repo.git"
    )


def test_multiple_local_values_restored(git_setup):
    manager, repository, _ = git_setup
    local(repository, "user.name", "First")
    Runner().run(["git", "-C", str(repository), "config", "--add", "user.name", "Last"])
    begin(manager, repository)
    SwitchManager(manager).back()
    assert manager.git.values(repository / ".git/config", "user.name") == ("First", "Last")


def test_back_from_another_directory_uses_saved_repository(git_setup, tmp_path, monkeypatch):
    manager, repository, _ = git_setup
    other = tmp_path / "other"
    other.mkdir()
    Runner().run(["git", "init", "--quiet", str(other)])
    local(other, "user.name", "Other Repository")
    begin(manager, repository)
    monkeypatch.chdir(other)
    SwitchManager(manager).back()
    assert manager.git.effective(repository, "user.name") == "Primary Developer"
    assert manager.git.effective(other, "user.name") == "Other Repository"


def test_manual_identity_edit_blocks_return(git_setup):
    manager, repository, _ = git_setup
    begin(manager, repository)
    local(repository, "user.email", "manual@example.com")
    ssh = manager.layout.config.read_bytes()
    with pytest.raises(GhsshError, match="identity changed"):
        SwitchManager(manager).back()
    assert manager.layout.config.read_bytes() == ssh
    assert manager.layout.switch_state.exists()
    assert manager.git.effective(repository, "user.email") == "manual@example.com"


def test_missing_repository_blocks_return_without_ssh_changes(git_setup):
    manager, repository, _ = git_setup
    begin(manager, repository)
    repository.rename(repository.with_name("moved"))
    ssh = manager.layout.config.read_bytes()
    with pytest.raises(GhsshError, match="Git working repository"):
        SwitchManager(manager).back()
    assert manager.layout.config.read_bytes() == ssh
    assert manager.layout.switch_state.exists()


def test_switch_rolls_back_git_when_session_write_fails(git_setup, monkeypatch):
    import ghssh.switch_manager as module

    manager, repository, _ = git_setup
    before_git = (repository / ".git/config").read_bytes()
    before_ssh = manager.layout.config.read_bytes()
    write = module.atomic_write

    def fail(layout, path, *args, **kwargs):
        if path == manager.layout.switch_state:
            raise OSError("injected state write failure")
        return write(layout, path, *args, **kwargs)

    monkeypatch.setattr(module, "atomic_write", fail)
    with pytest.raises(OSError):
        begin(manager, repository)
    assert (repository / ".git/config").read_bytes() == before_git
    assert manager.layout.config.read_bytes() == before_ssh
    assert not manager.layout.switch_state.exists()
    assert not (repository / ".git/config.lock").exists()


def test_return_rolls_back_git_when_ssh_write_fails(git_setup, monkeypatch):
    manager, repository, _ = git_setup
    begin(manager, repository)
    before_git = (repository / ".git/config").read_bytes()
    before_ssh = manager.layout.config.read_bytes()

    def fail(*args):
        raise GhsshError("injected SSH write failure")

    monkeypatch.setattr(manager.config, "write", fail)
    with pytest.raises(GhsshError):
        SwitchManager(manager).back()
    assert (repository / ".git/config").read_bytes() == before_git
    assert manager.layout.config.read_bytes() == before_ssh
    assert manager.layout.switch_state.exists()


def test_existing_git_lock_blocks_switch(git_setup):
    manager, repository, _ = git_setup
    lock = repository / ".git/config.lock"
    lock.write_text("another process")
    before = manager.layout.config.read_bytes()
    with pytest.raises(GhsshError, match="locked by another process"):
        begin(manager, repository)
    assert manager.layout.config.read_bytes() == before
    assert lock.read_text() == "another process"


def test_preflight_detects_external_config_change(git_setup):
    manager, repository, _ = git_setup
    plan = manager.git.plan(repository, "Second", "second@example.com")
    local(repository, "core.editor", "external")
    with pytest.raises(GhsshError, match="changed since inspection"), manager.git.edit(plan):
        pass
    assert manager.git.effective(repository, "core.editor") == "external"
    assert not (repository / ".git/config.lock").exists()


def test_external_edit_during_git_staging_is_retained(git_setup):
    manager, repository, _ = git_setup
    plan = manager.git.plan(repository, "Second", "second@example.com")
    with pytest.raises(GhsshError, match="retained external edits"), manager.git.edit(plan) as edit:
        with (repository / ".git/config").open("a") as stream:
            stream.write("\n# external edit\n")
        edit.apply()
    assert "# external edit" in (repository / ".git/config").read_text()


def test_symlink_git_config_refused(git_setup):
    manager, repository, _ = git_setup
    config = repository / ".git/config"
    other = repository / "other-config"
    config.rename(other)
    config.symlink_to(other)
    with pytest.raises(GhsshError, match="symlink"):
        begin(manager, repository)


def test_environment_author_override_refused(git_setup, monkeypatch):
    manager, repository, _ = git_setup
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "override@example.com")
    with pytest.raises(GhsshError, match="Git environment overrides"):
        begin(manager, repository)


def test_explicit_author_config_refused(git_setup):
    manager, repository, _ = git_setup
    local(repository, "author.email", "override@example.com")
    with pytest.raises(GhsshError, match="Explicit author/committer"):
        begin(manager, repository)


def test_local_include_refused(git_setup):
    manager, repository, _ = git_setup
    include = repository / "included-config"
    include.write_text("[user]\n email = overridden@example.com\n")
    local(repository, "include.path", str(include))
    with pytest.raises(GhsshError, match="Include/IncludeIf"):
        begin(manager, repository)


def test_saved_config_path_cannot_redirect_to_global_config(git_setup):
    manager, repository, global_config = git_setup
    begin(manager, repository)
    state = json.loads(manager.layout.switch_state.read_text())
    state["git_identity"]["config_path"] = str(global_config)
    manager.layout.switch_state.write_text(json.dumps(state))
    before = global_config.read_bytes()
    with pytest.raises(GhsshError, match="original repository"):
        SwitchManager(manager).back()
    assert global_config.read_bytes() == before


@pytest.mark.parametrize(
    "name,email",
    [
        ("", "a@example.com"),
        ("Bad\nName", "a@example.com"),
        ("Name", "not-email"),
        ("Name", "a@b\nconfig"),
        ("<Name>", "a@example.com"),
    ],
)
def test_invalid_git_identity_refused(git_setup, name, email):
    manager, repository, _ = git_setup
    with pytest.raises(GhsshError):
        manager.git.plan(repository, name, email)


def test_partial_commit_identity_refused(git_setup):
    manager, repository, _ = git_setup
    with pytest.raises(GhsshError, match="both --git-name"):
        SwitchManager(manager).plan("github_second", repository=repository, git_name="Second")


def test_backup_contains_only_identity_metadata(git_setup):
    manager, repository, _ = git_setup
    local(repository, "remote.secret.url", "https://user:secret@example.com/private.git")
    change = begin(manager, repository)
    data = (change.backup / "git-identity.json").read_text()
    assert "secret" not in data
    assert json.loads(data)["name"] == "Second Developer"


def test_linked_worktree_without_scoped_config_refused(git_setup, tmp_path):
    manager, repository, _ = git_setup
    local(repository, "user.name", "Original")
    local(repository, "user.email", "original@example.com")
    Runner().run(
        ["git", "-C", str(repository), "commit", "--allow-empty", "--quiet", "-m", "Initial"]
    )
    linked = tmp_path / "linked"
    Runner().run(
        ["git", "-C", str(repository), "worktree", "add", "--quiet", "-b", "linked", str(linked)]
    )
    with pytest.raises(GhsshError, match="Linked worktrees share config"):
        manager.git.plan(linked, "Second", "second@example.com")


def test_scoped_worktree_config_changes_only_selected_worktree(git_setup, tmp_path):
    manager, repository, _ = git_setup
    Runner().run(
        ["git", "-C", str(repository), "commit", "--allow-empty", "--quiet", "-m", "Initial"]
    )
    local(repository, "extensions.worktreeConfig", "true")
    linked = tmp_path / "linked"
    Runner().run(
        ["git", "-C", str(repository), "worktree", "add", "--quiet", "-b", "linked", str(linked)]
    )
    begin(manager, linked)
    assert manager.git.effective(linked, "user.email") == "second@example.com"
    assert manager.git.effective(repository, "user.email") == "primary@example.com"
    SwitchManager(manager).back()
    assert manager.git.effective(linked, "user.email") == "primary@example.com"


def test_cli_switches_commit_identity_and_back(git_setup, monkeypatch):
    manager, repository, _ = git_setup
    monkeypatch.setattr(cli, "Manager", lambda: manager)
    result = CliRunner().invoke(
        cli.app,
        [
            "switch",
            "github_second",
            "--repo",
            str(repository),
            "--git-name",
            "Second",
            "--git-email",
            "second@example.com",
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    assert manager.git.effective(repository, "user.email") == "second@example.com"
    result = CliRunner().invoke(cli.app, ["switch", "--back", "--yes"])
    assert result.exit_code == 0, result.output
    assert manager.git.effective(repository, "user.email") == "primary@example.com"


def test_cli_interactive_prompts_for_commit_identity(git_setup, monkeypatch):
    manager, repository, _ = git_setup
    monkeypatch.setattr(cli, "Manager", lambda: manager)
    monkeypatch.chdir(repository)
    result = CliRunner().invoke(
        cli.app, ["switch", "github_second"], input="Second\nsecond@example.com\ny\n"
    )
    assert result.exit_code == 0, result.output
    assert manager.git.effective(repository, "user.name") == "Second"


def test_commit_dry_run_changes_no_files(git_setup, monkeypatch):
    manager, repository, _ = git_setup
    monkeypatch.setattr(cli, "Manager", lambda: manager)
    before_git = (repository / ".git/config").read_bytes()
    before_ssh = manager.layout.config.read_bytes()
    result = CliRunner().invoke(
        cli.app,
        [
            "switch",
            "github_second",
            "--repo",
            str(repository),
            "--git-name",
            "Second",
            "--git-email",
            "second@example.com",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (repository / ".git/config").read_bytes() == before_git
    assert manager.layout.config.read_bytes() == before_ssh
    assert not manager.layout.switch_state.exists()
    assert not manager.layout.backups.exists()


def test_yes_without_commit_details_does_not_silently_switch_only_ssh(git_setup, monkeypatch):
    manager, repository, _ = git_setup
    monkeypatch.setattr(cli, "Manager", lambda: manager)
    monkeypatch.chdir(repository)
    result = CliRunner().invoke(cli.app, ["switch", "github_second", "--yes"])
    assert result.exit_code == 1
    assert "--git-name and --git-email" in result.output
    assert not manager.layout.switch_state.exists()


def test_saved_state_validates_types():
    with pytest.raises(GhsshError):
        GitIdentity.parse({"repository": "../global"})


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows short path aliases")
def test_switch_from_windows_short_path_alias(git_setup):
    import win32api

    manager, repository, _ = git_setup
    alias = Path(win32api.GetShortPathName(str(repository)))
    if alias == repository:
        pytest.skip("8.3 aliases are disabled on this volume")
    change = SwitchManager(manager).start(
        "github_second", repository=alias,
        git_name="Second Developer", git_email="second@example.com",
    )
    assert change.backup.is_dir()
    assert manager.git.effective(repository, "user.email") == "second@example.com"
    SwitchManager(manager).back()
    assert manager.git.effective(repository, "user.email") == "primary@example.com"
