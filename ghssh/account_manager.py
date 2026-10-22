"""Named GitHub accounts and direct, transactional SSH/commit selection."""

from __future__ import annotations

import json
import re
from contextlib import nullcontext
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .git_manager import GitPlan, validate_identity
from .manager import Change, Manager
from .utils import GhsshError, Transaction, atomic_write, locked, read_bytes


def account_name(value: str) -> str:
    name = value.lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", name):
        raise GhsshError(
            "Account nickname: use 1-48 letters, numbers, underscores or hyphens "
            "(for example arsam or arvand). Start with a letter or number."
        )
    return name


@dataclass(frozen=True)
class Account:
    key: str
    fingerprint: str
    git_name: str
    git_email: str


@dataclass(frozen=True)
class AddPlan:
    name: str
    account: Account
    create: bool
    before: bytes | None


@dataclass(frozen=True)
class AccountSwitchPlan:
    name: str
    account: Account
    config: str
    new_config: str
    old_key: Path | None
    old_fingerprint: str | None
    git: GitPlan | None
    before: bytes | None


class AccountManager:
    def __init__(self, manager: Manager):
        self.manager = manager
        self.layout = manager.layout

    def _read(self) -> tuple[dict[str, Account], bytes | None]:
        path = self.layout.checked(self.layout.accounts)
        if not path.exists():
            return {}, None
        before = read_bytes(path)
        try:
            data = json.loads(before)
            if (
                not isinstance(data, dict)
                or type(data.get("version")) is not int
                or data["version"] != 1
                or not isinstance(data.get("accounts"), dict)
            ):
                raise ValueError("unsupported accounts format")
            accounts = {}
            for name, item in data["accounts"].items():
                if account_name(name) != name or not isinstance(item, dict):
                    raise ValueError("invalid nickname or account")
                account = Account(**item)
                if not all(isinstance(value, str) for value in asdict(account).values()):
                    raise ValueError("account fields must be text")
                self.manager.ssh.key_path(account.key)
                validate_identity(account.git_name, account.git_email)
                if not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", account.fingerprint):
                    raise ValueError("invalid key fingerprint")
                accounts[name] = account
            return accounts, before
        except (ValueError, TypeError, UnicodeError, GhsshError) as exc:
            raise GhsshError(f"Cannot read saved accounts in {path}: {exc}") from exc

    def read(self) -> dict[str, Account]:
        return self._read()[0]

    def rotate(self, old: Path, new: Path, fingerprint: str, before: bytes | None) -> bool:
        accounts, current = self._read()
        if current != before:
            raise GhsshError("Saved accounts changed since inspection; run replace again.")
        changed = False
        for name, account in accounts.items():
            if self.manager.ssh.key_path(account.key) == old:
                if self.manager.ssh.identity_fingerprint(old) != account.fingerprint:
                    raise GhsshError(f"Saved key for '{name}' has changed; rotation refused.")
                accounts[name] = replace(account, key=new.name, fingerprint=fingerprint)
                changed = True
        if changed:
            self._write(accounts, before)
        return changed

    def _write(self, accounts: dict[str, Account], before: bytes | None) -> None:
        current = read_bytes(self.layout.accounts) if self.layout.accounts.exists() else None
        if current != before:
            raise GhsshError("Saved accounts changed during the operation; run the command again.")
        data = {"version": 1, "accounts": {k: asdict(v) for k, v in sorted(accounts.items())}}
        atomic_write(self.layout, self.layout.accounts, json.dumps(data, indent=2).encode() + b"\n")

    def plan_add(
        self, name: str, git_name: str, git_email: str, *,
        key: str | None = None, key_name: str | None = None,
    ) -> AddPlan:
        name = account_name(name)
        validate_identity(git_name, git_email)
        accounts, before = self._read()
        if name in accounts:
            raise GhsshError(f"Account '{name}' already exists. Run ghssh switch {name}.")
        if key is not None and key_name is not None:
            raise GhsshError("Choose --key to reuse a key, or --key-name to name a new key.")
        path = self.manager.ssh.key_path(key if key is not None else key_name or f"github_{name}")
        if key is not None:
            self.layout.checked(path, exists=True)
            fingerprint = self.manager.ssh.identity_fingerprint(path)
        else:
            self.manager.ssh.available(path)
            self.manager.ssh.registry()
            fingerprint = ""
        if any(
            self.manager.ssh.key_path(item.key) == path
            or (fingerprint and item.fingerprint == fingerprint)
            for item in accounts.values()
        ):
            raise GhsshError("This SSH key is already saved under another account nickname.")
        return AddPlan(name, Account(path.name, fingerprint, git_name, git_email), key is None, before)

    def add(
        self, name: str, git_name: str, git_email: str, *,
        key: str | None = None, key_name: str | None = None,
        no_passphrase: bool = False, expected: AddPlan | None = None,
    ) -> Account:
        plan = self.plan_add(name, git_name, git_email, key=key, key_name=key_name)
        if expected is not None and plan != expected:
            raise GhsshError("Account setup changed since confirmation; run add again.")
        with locked(self.layout):
            if self.plan_add(name, git_name, git_email, key=key, key_name=key_name) != plan:
                raise GhsshError("Account setup changed during the operation; run add again.")
            accounts = self.read()
            path = self.manager.ssh.key_path(plan.account.key)
            paths = [self.layout.accounts]
            if plan.create:
                paths += [path, Path(str(path) + ".pub"), self.layout.state]
            with Transaction(self.layout, paths) as transaction:
                account = plan.account
                if plan.create:
                    self.manager.ssh.generate(path, git_email, no_passphrase=no_passphrase)
                    transaction.mark(path, Path(str(path) + ".pub"))
                    fingerprint = self.manager.ssh.identity_fingerprint(path)
                    self.manager.ssh.record(path, fingerprint)
                    transaction.mark(self.layout.state)
                    account = Account(path.name, fingerprint, git_name, git_email)
                accounts[plan.name] = account
                self._write(accounts, plan.before)
                transaction.mark(self.layout.accounts)
        return account

    def plan_switch(
        self, name: str, *, repository: Path | None = None, ssh_only: bool = False,
    ) -> AccountSwitchPlan:
        self.manager.ensure_not_switching()
        accounts, before = self._read()
        name = account_name(name)
        if name not in accounts:
            raise GhsshError(f"No account named '{name}'. Run ghssh accounts or ghssh add {name}.")
        if repository is not None and ssh_only:
            raise GhsshError("Choose --repo PATH for commit switching, or --ssh-only.")
        account = accounts[name]
        key = self.manager.ssh.key_path(account.key)
        self.layout.checked(key, exists=True)
        if self.manager.ssh.identity_fingerprint(key) != account.fingerprint:
            raise GhsshError(f"The SSH key saved for '{name}' has changed; switching refused.")
        git = None if ssh_only else self.manager.git.plan(
            repository or Path.cwd(), account.git_name, account.git_email
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
            raise GhsshError("Set global/wildcard AddKeysToAgent to no before switching accounts.")
        updated = config.upsert(self.manager.identity_value(key), disable_agent_additions=True)
        self.manager.ssh.validate_config(updated)
        old_fp = self.manager.ssh.identity_fingerprint(old) if old else None
        return AccountSwitchPlan(name, account, config.text, updated, old, old_fp, git, before)

    def forget(self, name: str, *, expected: Account | None = None) -> None:
        name = account_name(name)
        with locked(self.layout):
            accounts, before = self._read()
            if name not in accounts:
                raise GhsshError(f"No saved account named '{name}'. Run ghssh accounts.")
            if expected is not None and accounts[name] != expected:
                raise GhsshError("Saved account changed since confirmation; run forget again.")
            del accounts[name]
            with Transaction(self.layout, [self.layout.accounts]) as transaction:
                self._write(accounts, before)
                transaction.mark(self.layout.accounts)

    def switch(
        self, name: str, *, repository: Path | None = None, ssh_only: bool = False,
        expected: AccountSwitchPlan | None = None,
    ) -> Change:
        plan = self.plan_switch(name, repository=repository, ssh_only=ssh_only)
        if expected is not None and plan != expected:
            raise GhsshError("Account setup changed since inspection; run switch again.")
        with locked(self.layout):
            if self.plan_switch(name, repository=repository, ssh_only=ssh_only) != plan:
                raise GhsshError("Account setup changed during the operation; run switch again.")
            backup = self.manager.backups.create(self.manager.config.read())
            if plan.git:
                atomic_write(
                    self.layout, backup / "git-identity.json",
                    json.dumps(plan.git.identity.serialize(), indent=2).encode() + b"\n",
                )
            with (
                self.manager.git.edit(plan.git) if plan.git else nullcontext() as git_edit,
                Transaction(self.layout, [self.layout.config]) as transaction,
            ):
                self.manager.config.write(plan.new_config, plan.config)
                transaction.mark(self.layout.config)
                if git_edit:
                    git_edit.apply()
        return Change(plan.old_key, self.manager.ssh.key_path(plan.account.key), backup)
