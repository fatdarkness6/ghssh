"""Repository-scoped commit identities with reversible, locked config edits."""

from __future__ import annotations

import os
import re
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

from .utils import GhsshError, Runner, read_bytes


def checked_path(path: Path) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        raise GhsshError("Git configuration path must be absolute without traversal.")
    for component in reversed((path, *path.parents)):
        if component.is_symlink():
            raise GhsshError(f"Refusing a symlink in the Git configuration path: {component}")
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise GhsshError(f"Refusing unsafe Git configuration file: {path}")
    parent = path.parent.stat()
    if parent.st_uid != os.getuid() or not stat.S_ISDIR(parent.st_mode):
        raise GhsshError(f"Git configuration directory is not owned by this user: {path.parent}")
    return path


def validate_identity(name: str, email: str) -> None:
    if (
        not name.strip()
        or name != name.strip()
        or len(name) > 200
        or any(ord(char) < 32 or char in "<>" for char in name)
    ):
        raise GhsshError("Commit name must be nonempty, on one line, without < or >.")
    if (
        len(email) > 254
        or email != email.strip()
        or email.count("@") != 1
        or not all(email.split("@"))
        or any(char.isspace() or ord(char) < 32 or char in "<>" for char in email)
    ):
        raise GhsshError(
            "Provide a valid, single-line commit email verified on the target GitHub account."
        )


@dataclass(frozen=True)
class GitIdentity:
    repository: Path
    config_path: Path
    previous_names: tuple[str, ...]
    previous_emails: tuple[str, ...]
    name: str
    email: str

    def serialize(self) -> dict:
        return {
            "repository": str(self.repository),
            "config_path": str(self.config_path),
            "previous_names": list(self.previous_names),
            "previous_emails": list(self.previous_emails),
            "name": self.name,
            "email": self.email,
        }

    @classmethod
    def parse(cls, data: object) -> GitIdentity:
        if not isinstance(data, dict):
            raise GhsshError("Invalid saved Git identity.")
        try:
            for key in ("repository", "config_path", "name", "email"):
                if not isinstance(data[key], str):
                    raise TypeError(key)
            for key in ("previous_names", "previous_emails"):
                values = data[key]
                if (
                    not isinstance(values, list)
                    or len(values) > 100
                    or not all(isinstance(value, str) and "\x00" not in value for value in values)
                ):
                    raise ValueError(key)
            validate_identity(data["name"], data["email"])
            repository, config = Path(data["repository"]), Path(data["config_path"])
            if any(not p.is_absolute() or ".." in p.parts for p in (repository, config)):
                raise ValueError("unsafe repository/config path")
            return cls(
                repository,
                config,
                tuple(data["previous_names"]),
                tuple(data["previous_emails"]),
                data["name"],
                data["email"],
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise GhsshError(f"Invalid saved Git identity: {exc}") from exc


@dataclass(frozen=True)
class GitPlan:
    identity: GitIdentity
    before: bytes | None = field(repr=False)
    mode: int
    previous_effective_name: str
    previous_effective_email: str
    restoring: bool = False


class GitManager:
    def __init__(self, runner: Runner):
        self.runner = runner

    @staticmethod
    def check_environment() -> None:
        overrides = [
            key
            for key in os.environ
            if key.startswith("GIT_CONFIG")
            or key
            in {
                "GIT_DIR",
                "GIT_COMMON_DIR",
                "GIT_WORK_TREE",
                "GIT_AUTHOR_NAME",
                "GIT_AUTHOR_EMAIL",
                "GIT_COMMITTER_NAME",
                "GIT_COMMITTER_EMAIL",
            }
        ]
        if overrides:
            raise GhsshError(
                "Git environment overrides could bypass the selected identity. Unset: "
                + ", ".join(sorted(overrides))
            )

    def discover(self, directory: Path) -> tuple[Path, Path]:
        self.check_environment()
        directory = directory.absolute()
        if ".." in directory.parts or any(p.is_symlink() for p in (directory, *directory.parents)):
            raise GhsshError("Use a repository path without traversal or symlinks.")
        result = self.runner.run(
            ["git", "-C", str(directory), "rev-parse", "--show-toplevel"], check=False
        )
        if result.returncode:
            raise GhsshError(
                "Commit switching needs a Git working repository. Use --repo PATH or --ssh-only."
            )
        repository = Path(result.stdout.strip())
        if (
            not repository.is_absolute()
            or not directory.is_relative_to(repository)
            or repository.stat().st_uid != os.getuid()
        ):
            raise GhsshError("Cannot safely identify the selected Git working repository.")

        def git_path(option: str) -> Path:
            value = self.runner.run(
                ["git", "-C", str(repository), "rev-parse", option]
            ).stdout.strip()
            path = Path(value)
            return path if path.is_absolute() else repository / path

        git_dir = git_path("--absolute-git-dir")
        common = git_path("--git-common-dir")
        extension = self.runner.run(
            [
                "git",
                "-C",
                str(repository),
                "config",
                "--bool",
                "--get",
                "extensions.worktreeConfig",
            ],
            check=False,
        )
        if extension.returncode not in (0, 1):
            raise GhsshError("Cannot read Git worktree configuration.")
        worktree_config = extension.stdout.strip() == "true"
        if git_dir != common and not worktree_config:
            raise GhsshError(
                "Linked worktrees share config. Enable extensions.worktreeConfig before switching commit identity, or use --ssh-only."
            )
        config = git_dir / "config.worktree" if worktree_config else common / "config"
        return repository, checked_path(config)

    def values(self, path: Path, key: str) -> tuple[str, ...]:
        if not path.exists():
            return ()
        result = self.runner.run(
            ["git", "config", "--no-includes", "--file", str(path), "--null", "--get-all", key],
            check=False,
        )
        if result.returncode == 1:
            return ()
        if result.returncode or not result.stdout.endswith("\x00"):
            raise GhsshError(f"Cannot read repository-local {key}.")
        return tuple(result.stdout[:-1].split("\x00"))

    def effective(self, repository: Path, key: str) -> str:
        result = self.runner.run(
            ["git", "-C", str(repository), "config", "--get", key], check=False
        )
        if result.returncode not in (0, 1):
            raise GhsshError(f"Cannot determine effective {key}.")
        return result.stdout.rstrip("\n") if result.returncode == 0 else ""

    def plan(self, directory: Path, name: str, email: str) -> GitPlan:
        validate_identity(name, email)
        repository, path = self.discover(directory)
        self.check_overrides(repository, path)
        before = read_bytes(path) if path.exists() else None
        identity = GitIdentity(
            repository,
            path,
            self.values(path, "user.name"),
            self.values(path, "user.email"),
            name,
            email,
        )
        return GitPlan(
            identity,
            before,
            stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600,
            self.effective(repository, "user.name"),
            self.effective(repository, "user.email"),
        )

    def plan_restore(self, identity: GitIdentity) -> GitPlan:
        repository, path = self.discover(identity.repository)
        if repository != identity.repository or path != identity.config_path:
            raise GhsshError(
                "Saved Git configuration no longer belongs to the original repository."
            )
        self.check_overrides(repository, path)
        if self.values(path, "user.name") != (identity.name,) or self.values(
            path, "user.email"
        ) != (identity.email,):
            raise GhsshError(
                "Repository commit identity changed during the switch; automatic return refused."
            )
        return GitPlan(
            identity,
            read_bytes(path) if path.exists() else None,
            stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600,
            self.effective(repository, "user.name"),
            self.effective(repository, "user.email"),
            True,
        )

    def edit(self, plan: GitPlan) -> GitEdit:
        return GitEdit(self, plan)

    def check_overrides(self, repository: Path, path: Path) -> None:
        result = self.runner.run(
            [
                "git",
                "-C",
                str(repository),
                "config",
                "--name-only",
                "--get-regexp",
                r"^(author|committer)\.(name|email)$",
            ],
            check=False,
        )
        if result.returncode == 0:
            raise GhsshError(
                "Explicit author/committer Git settings override user.name/user.email. Remove those overrides or use --ssh-only."
            )
        if result.returncode != 1:
            raise GhsshError("Cannot inspect Git author/committer settings.")
        if path.exists():
            included = self.runner.run(
                [
                    "git",
                    "config",
                    "--no-includes",
                    "--file",
                    str(path),
                    "--name-only",
                    "--get-regexp",
                    r"^include(if\..*)?\.path$",
                ],
                check=False,
            )
            if included.returncode == 0:
                raise GhsshError(
                    "Repository-local Include/IncludeIf settings need manual identity management; use --ssh-only."
                )
            if included.returncode != 1:
                raise GhsshError("Cannot inspect Git include settings.")

    def verify_effective(self, identity: GitIdentity) -> None:
        for variable in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
            result = self.runner.run(["git", "-C", str(identity.repository), "var", variable])
            match = re.fullmatch(r"(.*) <([^<>]*)> \d+ [+-]\d{4}", result.stdout.strip())
            if not match or match.group(1) != identity.name or match.group(2) != identity.email:
                raise GhsshError(
                    "Another Git setting overrides the selected commit identity; changes will be rolled back."
                )


class GitEdit:
    """Hold Git's config lock until both SSH and commit-identity changes complete."""

    def __init__(self, manager: GitManager, plan: GitPlan):
        self.manager, self.plan = manager, plan
        self.path = plan.identity.config_path
        self.lock = Path(str(self.path) + ".lock")
        self.fd: int | None = None
        self.applied: bytes | None = None

    def __enter__(self) -> Self:
        checked_path(self.path)
        self.manager.check_environment()
        try:
            self.fd = os.open(
                self.lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
        except FileExistsError as exc:
            raise GhsshError(
                f"Git configuration is locked by another process: {self.lock}"
            ) from exc
        try:
            current = read_bytes(self.path) if self.path.exists() else None
            if current != self.plan.before:
                raise GhsshError("Git configuration changed since inspection; run switch again.")
            return self
        except BaseException:
            self._release()
            raise

    def _write(self, data: bytes) -> None:
        checked_path(self.path)
        fd, filename = tempfile.mkstemp(prefix=".ghssh-git-", dir=self.path.parent)
        staged = Path(filename)
        try:
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), self.plan.mode)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            checked_path(self.path)
            os.replace(staged, self.path)
        finally:
            staged.unlink(missing_ok=True)

    def apply(self) -> None:
        fd, filename = tempfile.mkstemp(prefix=".ghssh-git-edit-", dir=self.path.parent)
        staged = Path(filename)
        os.close(fd)
        try:
            staged.write_bytes(self.plan.before or b"")
            identity = self.plan.identity
            for key, values in (
                ("user.name", identity.previous_names if self.plan.restoring else (identity.name,)),
                (
                    "user.email",
                    identity.previous_emails if self.plan.restoring else (identity.email,),
                ),
            ):
                result = self.manager.runner.run(
                    ["git", "config", "--no-includes", "--file", str(staged), "--unset-all", key],
                    check=False,
                )
                if result.returncode not in (0, 5):
                    raise GhsshError(f"Cannot stage repository-local {key}.")
                for value in values:
                    self.manager.runner.run(
                        [
                            "git",
                            "config",
                            "--no-includes",
                            "--file",
                            str(staged),
                            "--add",
                            key,
                            value,
                        ]
                    )
            updated = read_bytes(staged)
            current = read_bytes(self.path) if self.path.exists() else None
            if current != self.plan.before:
                raise GhsshError(
                    "Git configuration changed during the operation; retained external edits."
                )
            self._write(updated)
            self.applied = updated
            if not self.plan.restoring:
                self.manager.verify_effective(identity)
        finally:
            staged.unlink(missing_ok=True)
            Path(str(staged) + ".lock").unlink(missing_ok=True)

    def _release(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
            self.lock.unlink(missing_ok=True)

    def __exit__(
        self, kind: type[BaseException] | None, value: BaseException | None, tb: object
    ) -> bool:
        try:
            if kind is not None and self.applied is not None:
                checked_path(self.path)
                if read_bytes(self.path) != self.applied:
                    raise GhsshError(
                        "Git rollback refused to overwrite external edits; use git-identity.json in the safety backup."
                    ) from value
                if self.plan.before is None:
                    self.path.unlink()
                else:
                    self._write(self.plan.before)
        finally:
            self._release()
        return False
