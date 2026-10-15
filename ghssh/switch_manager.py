"""Temporary GitHub identity selection with a durable, one-level return point."""

from __future__ import annotations

import json
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

from .config_manager import SSHConfig
from .git_manager import GitIdentity, GitPlan
from .manager import Change, Manager
from .utils import GhsshError, Transaction, atomic_write, locked, read_bytes


@dataclass(frozen=True)
class SwitchPlan:
    config: SSHConfig
    old_key: Path | None
    key: Path
    new_config: str
    fingerprint: str | None
    old_fingerprint: str | None
    git_identity: GitPlan | None = None


@dataclass(frozen=True)
class Session:
    previous_section: str
    previous_fingerprint: str | None
    active_section: str
    active_key: Path
    fingerprint: str
    backup: Path
    config_existed: bool
    git_identity: GitIdentity | None = None


class SwitchManager:
    """Temporarily select SSH and optional repository commit identities."""

    def __init__(self, manager: Manager):
        self.manager = manager
        self.layout = manager.layout

    def read(self) -> Session | None:
        path = self.layout.checked(self.layout.switch_state)
        if not path.exists():
            return None
        try:
            data = json.loads(read_bytes(path))
            if not isinstance(data, dict) or data.get("version") not in (1, 2):
                raise ValueError("unsupported switch state")
            git_identity = (
                GitIdentity.parse(data["git_identity"])
                if data.get("git_identity") is not None
                else None
            )
            if data["version"] == 1 and git_identity is not None:
                raise ValueError("legacy switch state cannot contain a Git identity")
            if data["version"] == 2 and git_identity is None:
                raise ValueError("version 2 switch state requires a Git identity")
            for name in (
                "previous_section",
                "active_section",
                "active_key",
                "fingerprint",
                "backup_id",
            ):
                if not isinstance(data[name], str):
                    raise TypeError(f"invalid {name}")
            if type(data["config_existed"]) is not bool:
                raise ValueError("invalid config_existed")
            previous_fp = data["previous_fingerprint"]
            if previous_fp is not None and not isinstance(previous_fp, str):
                raise ValueError("invalid previous fingerprint")
            key = self.manager.ssh.key_path(data["active_key"])
            active = SSHConfig(data["active_section"])
            active.validate_edit()
            if len(active.blocks) != 1 or active.globals or active.identity(self.layout) != key:
                raise ValueError("invalid active GitHub section")
            previous = SSHConfig(data["previous_section"])
            previous.validate_edit()
            if data["previous_section"] and (
                len(previous.blocks) != 1 or not previous.target() or previous.globals
            ):
                raise ValueError("invalid previous GitHub section")
            previous.identity(self.layout)
            backup = self.manager.backups.select(data["backup_id"])
            return Session(
                data["previous_section"],
                previous_fp,
                data["active_section"],
                key,
                data["fingerprint"],
                backup,
                data["config_existed"],
                git_identity,
            )
        except (ValueError, KeyError, TypeError, UnicodeError) as exc:
            raise GhsshError(
                f"Invalid temporary-switch state; use your safety backup. {exc}"
            ) from exc

    def plan(
        self,
        name: str,
        *,
        create: bool = False,
        git_name: str | None = None,
        git_email: str | None = None,
        repository: Path | None = None,
    ) -> SwitchPlan:
        self.manager.ensure_not_switching()
        if (git_name is None) != (git_email is None):
            raise GhsshError("Provide both --git-name and --git-email for commit switching.")
        if repository is not None and git_name is None:
            raise GhsshError("--repo needs --git-name and --git-email, or use --ssh-only.")
        git_plan = (
            self.manager.git.plan(repository or Path.cwd(), git_name, git_email)
            if git_name is not None
            else None
        )
        config, old = self.manager.inspect()
        external = list(config.globals)
        for block in config.blocks:
            if block.github and block is not config.target():
                external.extend(block.directives)
        if any(
            d.name == "addkeystoagent" and tuple(v.lower() for v in d.values) != ("no",)
            for d in external
        ):
            raise GhsshError(
                "Global/wildcard AddKeysToAgent enables automatic loading; set it to no before a temporary switch."
            )
        key = self.manager.ssh.key_path(name)
        if old == key:
            raise GhsshError("This key is already selected for github.com.")
        fingerprint = None
        if create:
            self.manager.ssh.available(key)
            self.manager.ssh.registry()
        else:
            self.layout.checked(key, exists=True)
            fingerprint = self.manager.ssh.identity_fingerprint(key)
        old_fp = self.manager.ssh.identity_fingerprint(old) if old else None
        new_config = config.upsert(self.manager.identity_value(key), disable_agent_additions=True)
        self.manager.ssh.validate_config(new_config)
        return SwitchPlan(config, old, key, new_config, fingerprint, old_fp, git_plan)

    def start(
        self,
        name: str,
        *,
        create: bool = False,
        comment: str = "",
        no_passphrase: bool = False,
        expected: SwitchPlan | None = None,
        git_name: str | None = None,
        git_email: str | None = None,
        repository: Path | None = None,
    ) -> Change:
        plan = self.plan(
            name, create=create, git_name=git_name, git_email=git_email, repository=repository
        )
        if expected and (
            plan.config.text != expected.config.text
            or plan.fingerprint != expected.fingerprint
            or plan.old_fingerprint != expected.old_fingerprint
            or plan.git_identity != expected.git_identity
        ):
            raise GhsshError("SSH setup changed since confirmation; run switch again.")
        if any(char in comment for char in ("\n", "\r", "\x00")):
            raise GhsshError("Key comment must be one line.")
        with locked(self.layout):
            checked = self.plan(
                name, create=create, git_name=git_name, git_email=git_email, repository=repository
            )
            if (
                checked.config.text != plan.config.text
                or checked.fingerprint != plan.fingerprint
                or checked.old_fingerprint != plan.old_fingerprint
                or checked.git_identity != plan.git_identity
            ):
                raise GhsshError("SSH setup changed during the operation; run switch again.")
            config_existed = self.layout.config.exists()
            backup = self.manager.backups.create(plan.config)
            if plan.git_identity:
                atomic_write(
                    self.layout,
                    backup / "git-identity.json",
                    json.dumps(plan.git_identity.identity.serialize(), indent=2).encode() + b"\n",
                )
            paths = [self.layout.config, self.layout.switch_state]
            if create:
                paths += [plan.key, Path(str(plan.key) + ".pub"), self.layout.state]
            with (
                (
                    self.manager.git.edit(plan.git_identity) if plan.git_identity else nullcontext()
                ) as git_edit,
                Transaction(self.layout, paths) as transaction,
            ):
                fingerprint = plan.fingerprint
                if create:
                    self.manager.ssh.generate(
                        plan.key, comment or "GitHub temporary account", no_passphrase=no_passphrase
                    )
                    transaction.mark(plan.key, Path(str(plan.key) + ".pub"))
                    fingerprint = self.manager.ssh.identity_fingerprint(plan.key)
                    self.manager.ssh.record(plan.key, fingerprint)
                    transaction.mark(self.layout.state)
                self.manager.config.write(plan.new_config, plan.config.text)
                transaction.mark(self.layout.config)
                if git_edit:
                    git_edit.apply()
                state = {
                    "version": 2 if plan.git_identity else 1,
                    "previous_section": plan.config.section(),
                    "previous_fingerprint": plan.old_fingerprint,
                    "active_section": SSHConfig(plan.new_config).section(),
                    "active_key": plan.key.name,
                    "fingerprint": fingerprint,
                    "backup_id": backup.name,
                    "config_existed": config_existed,
                }
                if plan.git_identity:
                    state["git_identity"] = plan.git_identity.identity.serialize()
                atomic_write(
                    self.layout,
                    self.layout.switch_state,
                    json.dumps(state, indent=2).encode() + b"\n",
                )
                transaction.mark(self.layout.switch_state)
        return Change(plan.old_key, plan.key, backup)

    def plan_back(self) -> tuple[Session, SSHConfig, str, Path | None]:
        session = self.read()
        if not session:
            raise GhsshError(
                "No temporary switch is active; there is no saved account to return to."
            )
        config, _ = self.manager.inspect()
        if config.section() != session.active_section:
            raise GhsshError(
                f"GitHub configuration changed during the switch; automatic return refused. Safety backup: {session.backup}"
            )
        previous = SSHConfig(session.previous_section)
        old = previous.identity(self.layout)
        if old and self.manager.ssh.identity_fingerprint(old) != session.previous_fingerprint:
            raise GhsshError("Previous key fingerprint changed; automatic return refused.")
        if session.git_identity:
            self.manager.git.plan_restore(session.git_identity)
        restored = (
            config.restore_section(session.previous_section)
            if session.previous_section
            else config.remove()
        )
        self.manager.ssh.validate_config(restored)
        return session, config, restored, old

    def back(
        self, *, expected_config: str | None = None, expected_session: Session | None = None
    ) -> Change:
        session, config, restored, old = self.plan_back()
        if (expected_config is not None and config.text != expected_config) or (
            expected_session is not None and session != expected_session
        ):
            raise GhsshError("SSH setup changed since confirmation; run switch --back again.")
        with locked(self.layout):
            current_session, current_config, _, _ = self.plan_back()
            if current_session != session or current_config.text != config.text:
                raise GhsshError("SSH setup changed during the operation; run switch --back again.")
            backup = self.manager.backups.create(config)
            git_plan = (
                self.manager.git.plan_restore(session.git_identity)
                if session.git_identity
                else None
            )
            if session.git_identity:
                atomic_write(
                    self.layout,
                    backup / "git-identity.json",
                    json.dumps(session.git_identity.serialize(), indent=2).encode() + b"\n",
                )
            with (
                self.manager.git.edit(git_plan) if git_plan else nullcontext() as git_edit,
                Transaction(
                    self.layout, [self.layout.config, self.layout.switch_state]
                ) as transaction,
            ):
                if git_edit:
                    git_edit.apply()
                if not session.config_existed and not restored.strip():
                    self.layout.checked(self.layout.config).unlink()
                else:
                    self.manager.config.write(restored, config.text)
                transaction.mark(self.layout.config)
                self.layout.checked(self.layout.switch_state, exists=True).unlink()
                transaction.mark(self.layout.switch_state)
        return Change(session.active_key, old, backup)
