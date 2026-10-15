"""Transactional workflows; presentation and confirmation stay in the CLI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .backup_manager import Backup, BackupManager
from .config_manager import ConfigManager, SSHConfig
from .git_manager import GitManager
from .github_manager import GitHubManager
from .ssh_manager import SSHManager
from .utils import GhsshError, Layout, Runner, Transaction, locked


@dataclass(frozen=True)
class Change:
    old_key: Path | None
    new_key: Path | None
    backup: Path | None


class Manager:
    def __init__(self, layout: Layout | None = None, runner: Runner | None = None):
        self.layout = layout or Layout.default()
        self.runner = runner or Runner()
        self.config = ConfigManager(self.layout)
        self.ssh = SSHManager(self.layout, self.runner)
        self.backups = BackupManager(self.layout, self.ssh)
        self.github = GitHubManager(self.runner)
        self.git = GitManager(self.runner)

    def ensure_not_switching(self) -> None:
        if self.layout.checked(self.layout.switch_state).exists():
            raise GhsshError("A temporary account switch is active. Run ghssh switch --back first.")

    def inspect(self) -> tuple[SSHConfig, Path | None]:
        config = self.config.read()
        config.validate_edit()
        if config.text:
            self.ssh.validate_config(config.text)
        return config, config.identity(self.layout)

    @staticmethod
    def identity_value(path: Path) -> str:
        # Double-quoted OpenSSH paths preserve spaces in custom HOME directories.
        return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'

    def add(
        self,
        name: str,
        comment: str,
        *,
        replace: bool = False,
        no_passphrase: bool = False,
        expected_config: str | None = None,
    ) -> Change:
        self.ensure_not_switching()
        path = self.ssh.key_path(name)
        config, old = self.inspect()
        if expected_config is not None and config.text != expected_config:
            raise GhsshError("SSH configuration changed since confirmation; run the command again.")
        if old and not replace:
            raise GhsshError(
                "GitHub already has an identity. Use ghssh replace to rotate it safely."
            )
        if replace and not old:
            raise GhsshError("No GitHub-specific IdentityFile is configured. Use ghssh add first.")
        self.ssh.available(path)
        # Validate complete resulting config before creating any files.
        new_config = config.upsert(self.identity_value(path))
        self.ssh.validate_config(new_config)
        self.ssh.registry()
        with locked(self.layout):
            self.ensure_not_switching()
            if self.config.read().text != config.text:
                raise GhsshError("SSH configuration changed; run the command again.")
            self.ssh.available(path)
            # Even add preserves a pre-existing GitHub block and its options.
            backup = self.backups.create(config) if config.target() else None
            paths = [path, Path(str(path) + ".pub"), self.layout.config, self.layout.state]
            with Transaction(self.layout, paths) as transaction:
                attempted_agent_add = False
                try:
                    self.ssh.generate(path, comment, no_passphrase=no_passphrase)
                    transaction.mark(path, Path(str(path) + ".pub"))
                    fingerprint = self.ssh.fingerprint(Path(str(path) + ".pub"))
                    attempted_agent_add = True
                    self.ssh.add_agent(path)
                    self.config.write(new_config, config.text)
                    transaction.mark(self.layout.config)
                    self.ssh.record(path, fingerprint)
                    transaction.mark(self.layout.state)
                    if self.config.read().identity(self.layout) != path:
                        raise GhsshError("Post-write config validation failed.")
                except BaseException as original:
                    try:
                        if attempted_agent_add and fingerprint in self.ssh.agent_fingerprints()[1]:
                            self.ssh.remove_agent(path)
                        self.ssh.stop_started_agent()
                    except (GhsshError, OSError) as cleanup:
                        raise GhsshError(
                            f"Local rollback required; agent cleanup also failed: {cleanup}"
                        ) from original
                    raise
        return Change(old, path, backup)

    def can_delete(self, config: SSHConfig, key: Path) -> None:
        self.layout.checked(key, exists=True)
        self.layout.checked(Path(str(key) + ".pub"))
        if config.references_elsewhere(key, self.layout):
            raise GhsshError("This identity is referenced by another SSH host. Use --config-only.")
        if not self.ssh.owned(key):
            raise GhsshError(
                "This key has no matching ghssh ownership record. Use --config-only; keep its files."
            )

    def remove(
        self,
        *,
        delete_files: bool = False,
        no_backup: bool = False,
        expected_config: str | None = None,
    ) -> Change:
        self.ensure_not_switching()
        config, key = self.inspect()
        if expected_config is not None and config.text != expected_config:
            raise GhsshError("SSH configuration changed since confirmation; run the command again.")
        if not config.target():
            raise GhsshError("No explicit GitHub Host block exists; nothing to remove.")
        if delete_files:
            if not key:
                raise GhsshError("No GitHub IdentityFile is configured; use --config-only.")
            self.can_delete(config, key)
        with locked(self.layout):
            self.ensure_not_switching()
            if self.config.read().text != config.text:
                raise GhsshError("SSH configuration changed; run the command again.")
            if delete_files:
                self.can_delete(config, key)
            backup = None if no_backup else self.backups.create(config)
            paths = [self.layout.config]
            if delete_files:
                paths += [key, Path(str(key) + ".pub"), self.layout.state]
            with Transaction(self.layout, paths) as transaction:
                self.config.write(config.remove(), config.text)
                transaction.mark(self.layout.config)
                # Remove the agent identity before deleting the files needed to identify it.
                if key and not config.references_elsewhere(key, self.layout):
                    candidate = Path(str(key) + ".pub")
                    if not candidate.exists():
                        candidate = key
                    if candidate.exists():
                        fingerprint = self.ssh.identity_fingerprint(key)
                        if fingerprint in self.ssh.agent_fingerprints()[1]:
                            self.ssh.remove_agent(key)
                if delete_files:
                    key.unlink()
                    transaction.mark(key)
                    public = Path(str(key) + ".pub")
                    public.unlink(missing_ok=True)
                    transaction.mark(public)
                    self.ssh.record(key, None)
                    transaction.mark(self.layout.state)
        return Change(key, None, backup)

    def restore(
        self,
        backup_id: str,
        *,
        expected_config: str | None = None,
        expected_backup: Backup | None = None,
    ) -> Change:
        self.ensure_not_switching()
        source = self.backups.validate(self.backups.select(backup_id))
        if expected_backup is not None and source != expected_backup:
            raise GhsshError("Selected backup changed since confirmation; restoration refused.")
        config, old = self.inspect()
        if expected_config is not None and config.text != expected_config:
            raise GhsshError("SSH configuration changed since confirmation; run the command again.")
        section = source.files["github.conf"].decode()
        new_config = config.restore_section(section) if section else config.remove()
        self.ssh.validate_config(new_config)
        # A restore must never alter an identity referenced by another host.
        if source.identity and config.references_elsewhere(source.identity, self.layout):
            raise GhsshError(
                "Restored key path is used by another host; restore manually to a new path."
            )
        with locked(self.layout):
            self.ensure_not_switching()
            if self.backups.validate(self.backups.select(backup_id)) != source:
                raise GhsshError(
                    "Selected backup changed during the operation; restoration refused."
                )
            if self.config.read().text != config.text:
                raise GhsshError("SSH configuration changed; run the command again.")
            current_backup = self.backups.create(config)
            paths = [self.layout.config, self.layout.state]
            if source.identity:
                paths += [source.identity, Path(str(source.identity) + ".pub")]
                # The restoration target may differ from the current GitHub identity.
                # Preserve those overwritten bytes in a durable recovery snapshot too.
                self.backups.save_restore_target(current_backup, source.identity)
            with Transaction(self.layout, paths) as transaction:
                self.backups.restore_files(source, transaction)
                self.config.write(new_config, config.text)
                transaction.mark(self.layout.config)
                if source.identity:
                    self.ssh.record(source.identity, source.fingerprint if source.managed else None)
                    transaction.mark(self.layout.state)
        return Change(old, source.identity, current_backup)
