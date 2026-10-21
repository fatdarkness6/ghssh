import json
from pathlib import Path

import pytest
from conftest import assert_permissions

from ghssh.utils import GhsshError


def test_add_sets_permissions_and_ownership(manager):
    change = manager.add("github_first", "test@example.com", no_passphrase=True)
    assert change.old_key is None
    key = change.new_key
    assert_permissions(manager.layout.ssh, 0o700)
    assert_permissions(key, 0o600)
    assert_permissions(Path(str(key) + ".pub"), 0o644)
    assert_permissions(manager.layout.config, 0o600)
    assert_permissions(manager.layout.state, 0o600)
    assert manager.config.read().identity(manager.layout) == key
    assert manager.ssh.owned(key)


def test_no_overwrite_existing_key(manager):
    manager.layout.ensure()
    key = manager.layout.ssh / "github_first"
    key.write_text("unrelated data")
    with pytest.raises(GhsshError, match="already exists"):
        manager.add(key.name, "", no_passphrase=True)
    assert key.read_text() == "unrelated data"
    assert not manager.layout.config.exists()


def test_replace_keeps_old_key_and_creates_complete_backup(configured):
    manager = configured
    old = manager.config.read().identity(manager.layout)
    old_data = old.read_bytes()
    config = manager.layout.config.read_bytes()
    change = manager.add("github_new", "new", replace=True, no_passphrase=True)
    assert old.read_bytes() == old_data
    assert change.backup.joinpath("private_key").read_bytes() == old_data
    assert change.backup.joinpath("config.snapshot").read_bytes() == config
    assert manager.config.read().identity(manager.layout) == change.new_key
    assert manager.backups.validate(change.backup).identity == old
    assert_permissions(change.backup, 0o700)
    for path in change.backup.iterdir():
        assert_permissions(path, 0o600)


def test_missing_github_identity_replace_refused(manager):
    with pytest.raises(GhsshError, match="No GitHub-specific"):
        manager.add("github_new", "", replace=True, no_passphrase=True)
    assert not manager.layout.ssh.exists()


def test_config_only_remove_keeps_keys(configured):
    manager = configured
    key = manager.config.read().identity(manager.layout)
    fp = manager.ssh.fingerprint(key)
    change = manager.remove()
    assert key.exists() and Path(str(key) + ".pub").exists()
    assert manager.config.read().target() is None
    assert change.backup.is_dir()
    assert fp not in manager.runner.loaded


def test_delete_owned_pair_only(configured):
    manager = configured
    key = manager.config.read().identity(manager.layout)
    other = manager.layout.ssh / "vps_key"
    other.write_text("unrelated")
    manager.remove(delete_files=True)
    assert not key.exists() and not Path(str(key) + ".pub").exists()
    assert other.read_text() == "unrelated"
    assert key.name not in manager.ssh.registry()


def test_delete_unowned_key_refused(configured):
    manager = configured
    manager.layout.state.unlink()
    before = manager.layout.config.read_bytes()
    with pytest.raises(GhsshError, match="ownership record"):
        manager.remove(delete_files=True)
    assert manager.layout.config.read_bytes() == before


def test_delete_shared_key_refused_but_config_only_works(configured):
    manager = configured
    key = manager.config.read().identity(manager.layout)
    with manager.layout.config.open("a") as file:
        file.write(f"Host vps.example\n IdentityFile {key}\n")
    with pytest.raises(GhsshError, match="another SSH host"):
        manager.remove(delete_files=True)
    manager.remove()
    assert key.exists()
    assert "Host vps.example" in manager.layout.config.read_text()
    assert manager.runner.loaded  # shared agent identity is retained


def test_no_backup_explicit(configured):
    manager = configured
    change = manager.remove(no_backup=True)
    assert change.backup is None
    assert not manager.layout.backups.exists()


def test_backup_restore_preserves_other_hosts(configured):
    manager = configured
    original = manager.config.read().identity(manager.layout)
    original_data = original.read_bytes()
    backup = manager.backups.create()
    manager.add("github_new", "", replace=True, no_passphrase=True)
    with manager.layout.config.open("a") as stream:
        stream.write("Host server.example\n    Port 2222\n")
    original.write_text("overwritten original file")
    change = manager.restore(backup.name)
    assert manager.config.read().identity(manager.layout) == original
    assert original.read_bytes() == original_data
    assert "Host server.example\n    Port 2222\n" in manager.layout.config.read_text()
    assert_permissions(original, 0o600)
    assert_permissions(Path(str(original) + ".pub"), 0o644)
    assert (change.backup / "restore-target.private").read_text() == "overwritten original file"


def test_restore_without_github_block(manager):
    manager.layout.ensure()
    backup = manager.backups.create()
    manager.add("github_new", "", no_passphrase=True)
    manager.restore(backup.name)
    assert manager.config.read().target() is None
    assert (manager.layout.ssh / "github_new").exists()


def test_tampered_backup_refused(configured):
    manager = configured
    backup = manager.backups.create()
    (backup / "private_key").write_text("corrupted")
    before = manager.layout.config.read_bytes()
    with pytest.raises(GhsshError, match="checksum mismatch"):
        manager.restore(backup.name)
    assert manager.layout.config.read_bytes() == before


def test_backup_metadata_traversal_refused(configured):
    manager = configured
    backup = manager.backups.create()
    metadata = json.loads((backup / "metadata.json").read_text())
    metadata["identity"] = "../foreign"
    (backup / "metadata.json").write_text(json.dumps(metadata))
    with pytest.raises(GhsshError):
        manager.restore(backup.name)


@pytest.mark.parametrize(
    "name", ["../other", "/tmp/key", ".", "config", "authorized_keys", "foo.pub", "a/b"]
)
def test_invalid_key_names_refused(manager, name):
    with pytest.raises(GhsshError):
        manager.ssh.key_path(name)


def test_symlink_and_hardlink_refused(manager, tmp_path):
    manager.layout.ensure()
    outside = tmp_path / "outside"
    outside.write_text("do not touch")
    manager.layout.config.symlink_to(outside)
    with pytest.raises(GhsshError, match="symlink"):
        manager.inspect()
    manager.layout.config.unlink()
    manager.layout.config.hardlink_to(outside)
    with pytest.raises(GhsshError, match="hard-linked"):
        manager.inspect()
    assert outside.read_text() == "do not touch"


def test_generation_failure_rolls_back(manager):
    manager.runner.fail_on = "ssh-add "
    with pytest.raises(GhsshError):
        manager.add("github_new", "", no_passphrase=True)
    assert not (manager.layout.ssh / "github_new").exists()
    assert not (manager.layout.ssh / "github_new.pub").exists()
    assert not manager.layout.config.exists()
    assert not manager.layout.state.exists()


def test_config_failure_rolls_back_key_and_agent(configured, monkeypatch):
    manager = configured
    before = manager.layout.config.read_bytes()
    loaded = manager.runner.loaded.copy()

    def fail(*args):
        raise GhsshError("injected write failure")

    monkeypatch.setattr(manager.config, "write", fail)
    with pytest.raises(GhsshError):
        manager.add("github_new", "", replace=True, no_passphrase=True)
    assert manager.layout.config.read_bytes() == before
    assert not (manager.layout.ssh / "github_new").exists()
    assert manager.runner.loaded == loaded
    assert manager.backups.list()


def test_ctrl_c_rolls_back(manager, monkeypatch):
    def interrupt(path):
        raise KeyboardInterrupt

    monkeypatch.setattr(manager.ssh, "add_agent", interrupt)
    with pytest.raises(KeyboardInterrupt):
        manager.add("github_new", "", no_passphrase=True)
    assert not manager.layout.config.exists()
    assert not (manager.layout.ssh / "github_new").exists()


def test_removal_agent_failure_rolls_back_config(configured):
    manager = configured
    before = manager.layout.config.read_bytes()
    manager.runner.fail_on = "ssh-add -d"
    with pytest.raises(GhsshError):
        manager.remove(delete_files=True)
    assert manager.layout.config.read_bytes() == before
    assert manager.config.read().identity(manager.layout).exists()


def test_restore_failure_rolls_back(configured, monkeypatch):
    manager = configured
    backup = manager.backups.create()
    manager.add("github_new", "", replace=True, no_passphrase=True)
    before = manager.layout.config.read_bytes()

    def fail(*args):
        raise GhsshError("injected restore failure")

    monkeypatch.setattr(manager.config, "write", fail)
    with pytest.raises(GhsshError):
        manager.restore(backup.name)
    assert manager.layout.config.read_bytes() == before


def test_confirmation_snapshot_revalidated(configured):
    manager = configured
    with pytest.raises(GhsshError, match="since confirmation"):
        manager.remove(expected_config="stale config")
    assert manager.config.read().target()


def test_external_config_edit_survives_rollback(configured, monkeypatch):
    manager = configured
    generate = manager.ssh.generate
    external = manager.layout.config.read_text() + "Host external-edit\n Port 2200\n"

    def generate_and_edit(*args, **kwargs):
        generate(*args, **kwargs)
        manager.layout.config.write_text(external)

    monkeypatch.setattr(manager.ssh, "generate", generate_and_edit)
    with pytest.raises(GhsshError, match="changed during"):
        manager.add("github_new", "", replace=True, no_passphrase=True)
    assert manager.layout.config.read_text() == external
    assert not (manager.layout.ssh / "github_new").exists()


def test_external_destination_created_after_snapshot_is_retained(manager, monkeypatch):
    def race(path, *args, **kwargs):
        path.write_text("another process created this")
        raise GhsshError("Key file already exists")

    monkeypatch.setattr(manager.ssh, "generate", race)
    with pytest.raises(GhsshError, match="already exists"):
        manager.add("github_race", "", no_passphrase=True)
    assert (manager.layout.ssh / "github_race").read_text() == "another process created this"


def test_rollback_refuses_to_clobber_changed_touched_file(manager):
    from ghssh.utils import Transaction, atomic_write

    manager.layout.ensure()
    path = manager.layout.config
    path.write_text("original")
    with (
        pytest.raises(GhsshError, match="retained external changes"),
        Transaction(manager.layout, [path]) as transaction,
    ):
        atomic_write(manager.layout, path, b"own edit")
        transaction.mark(path)
        path.write_text("external edit")
        raise ValueError("trigger rollback")
    assert path.read_text() == "external edit"


def test_existing_lock_blocks_mutation(configured):
    from ghssh.utils import locked

    manager = configured
    before = manager.layout.config.read_bytes()
    with locked(manager.layout), pytest.raises(GhsshError, match="Another ghssh"):
        manager.remove()
    assert manager.layout.config.read_bytes() == before


def test_symlink_ssh_directory_refused(manager, tmp_path):
    outside = tmp_path / "other-ssh"
    outside.mkdir()
    manager.layout.ssh.symlink_to(outside, target_is_directory=True)
    with pytest.raises(GhsshError, match="symlink"):
        manager.add("github_key", "", no_passphrase=True)
    assert not list(outside.iterdir())


def test_fingerprint_mismatch_blocks_deletion_and_unload(configured):
    from conftest import public_key

    manager = configured
    key = manager.config.read().identity(manager.layout)
    Path(str(key) + ".pub").write_text(public_key("different"))
    before = manager.layout.config.read_bytes()
    with pytest.raises(GhsshError, match="ownership record"):
        manager.remove(delete_files=True)
    with pytest.raises(GhsshError, match="fingerprints differ"):
        manager.remove()
    assert manager.layout.config.read_bytes() == before
    assert not any("-d" in args for args, _ in manager.runner.calls)


def test_restore_detects_backup_changed_since_confirmation(configured):
    from dataclasses import replace

    manager = configured
    path = manager.backups.create()
    source = manager.backups.validate(path)
    with pytest.raises(GhsshError, match="backup changed since confirmation"):
        manager.restore(path.name, expected_backup=replace(source, managed=False))


def test_restore_backup_symlink_refused(configured, tmp_path):
    manager = configured
    backup = manager.backups.create()
    private = backup / "private_key"
    outside = tmp_path / "foreign-key"
    outside.write_bytes(private.read_bytes())
    private.unlink()
    private.symlink_to(outside)
    with pytest.raises(GhsshError, match="symlink"):
        manager.restore(backup.name)


def test_invalid_openssh_config_fails_before_writes(manager):
    manager.runner.fail_on = "ssh -G"
    with pytest.raises(GhsshError):
        manager.add("github_new", "", no_passphrase=True)
    assert not manager.layout.ssh.exists()


def test_backup_and_restore_github_section_without_identity(manager):
    manager.layout.ensure()
    original = "Host github.com\n    HostName github.com\n    User git\n"
    manager.layout.config.write_text(original)
    backup = manager.backups.create()
    assert manager.backups.validate(backup).identity is None
    manager.add("github_new", "", no_passphrase=True)
    manager.restore(backup.name)
    assert manager.config.read().target() is not None
    assert manager.config.read().identity(manager.layout) is None
    assert manager.layout.config.read_text().startswith(original)
