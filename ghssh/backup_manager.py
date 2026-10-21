"""Private, self-describing backups and narrowly scoped restoration plans."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .config_manager import ConfigManager, SSHConfig
from .platform_support import secure_mode
from .ssh_manager import SSHManager
from .utils import GhsshError, Layout, Transaction, atomic_write, read_bytes


@dataclass(frozen=True)
class Backup:
    directory: Path
    identity: Path | None
    files: dict[str, bytes] = field(repr=False)
    fingerprint: str | None
    managed: bool


class BackupManager:
    def __init__(self, layout: Layout, ssh: SSHManager):
        self.layout = layout
        self.ssh = ssh

    def list(self) -> list[Path]:
        root = self.layout.checked(self.layout.backups)
        if not root.exists():
            return []
        return sorted(
            (
                path
                for path in root.iterdir()
                if not path.is_symlink()
                and path.is_dir()
                and re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_[a-f0-9]{8}", path.name)
            ),
            reverse=True,
        )

    def select(self, name: str) -> Path:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_[a-f0-9]{8}", name):
            raise GhsshError(
                "Choose a backup ID from ghssh restore --list; paths are not accepted."
            )
        path = self.layout.checked(self.layout.backups / name)
        if not path.is_dir():
            raise GhsshError(f"Backup not found: {name}")
        return path

    def create(self, config: SSHConfig | None = None) -> Path:
        config = config or ConfigManager(self.layout).read()
        config.validate_edit()
        identity = config.identity(self.layout)
        files = {"config.snapshot": config.text.encode(), "github.conf": config.section().encode()}
        fingerprint = None
        managed = False
        if identity:
            for source, name in (
                (identity, "private_key"),
                (Path(str(identity) + ".pub"), "public_key.pub"),
            ):
                self.layout.checked(source)
                if source.exists():
                    files[name] = read_bytes(source)
            if "public_key.pub" in files:
                fingerprint = self.ssh.fingerprint(Path(str(identity) + ".pub"))
            elif "private_key" in files:
                fingerprint = self.ssh.fingerprint(identity)
            managed = self.ssh.owned(identity) if "private_key" in files else False
        self.layout.ensure()
        root = self.layout.checked(self.layout.backups)
        root.mkdir(mode=0o700, exist_ok=True)
        secure_mode(root, 0o700)
        backup_id = datetime.now(UTC).strftime("%Y-%m-%d_%H-%M-%S_") + uuid4().hex[:8]
        directory = root / backup_id
        directory.mkdir(mode=0o700)
        secure_mode(directory, 0o700)
        metadata = {
            "version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "identity": str(identity.relative_to(self.layout.ssh)) if identity else None,
            "fingerprint": fingerprint,
            "managed": managed,
            "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
        }
        try:
            for name, data in files.items():
                atomic_write(self.layout, directory / name, data, 0o600)
            atomic_write(
                self.layout,
                directory / "metadata.json",
                json.dumps(metadata, indent=2).encode() + b"\n",
                0o600,
            )
        except BaseException:
            shutil.rmtree(directory)
            raise
        return directory

    def save_restore_target(self, directory: Path, identity: Path) -> None:
        """Preserve a restoration destination even when it isn't the current GitHub key."""
        inventory = {}
        for path, name in (
            (identity, "restore-target.private"),
            (Path(str(identity) + ".pub"), "restore-target.pub"),
        ):
            self.layout.checked(path)
            if path.exists():
                data = read_bytes(path)
                atomic_write(self.layout, directory / name, data, 0o600)
                inventory[name] = hashlib.sha256(data).hexdigest()
        atomic_write(self.layout, directory / "restore-target.txt", (str(identity) + "\n").encode())
        metadata = json.loads(read_bytes(directory / "metadata.json"))
        metadata["restore_target"] = {
            "identity": str(identity.relative_to(self.layout.ssh)),
            "files": inventory,
        }
        atomic_write(
            self.layout,
            directory / "metadata.json",
            json.dumps(metadata, indent=2).encode() + b"\n",
        )

    def validate(self, directory: Path) -> Backup:
        # Re-select using a basename and constrain the complete path.
        if directory != self.select(directory.name):
            raise GhsshError("Backup path is outside the backup directory.")
        try:
            metadata = json.loads(
                read_bytes(self.layout.checked(directory / "metadata.json", exists=True))
            )
            if not isinstance(metadata, dict) or metadata.get("version") != 1:
                raise ValueError("unsupported metadata version")
            inventory = metadata["files"]
            allowed = {"config.snapshot", "github.conf", "private_key", "public_key.pub"}
            if (
                not isinstance(inventory, dict)
                or not {"config.snapshot", "github.conf"} <= inventory.keys()
                or not inventory.keys() <= allowed
            ):
                raise ValueError("invalid file inventory")
            files = {}
            for name, digest in inventory.items():
                data = read_bytes(self.layout.checked(directory / name, exists=True))
                if not isinstance(digest, str) or hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError(f"checksum mismatch: {name}")
                files[name] = data
            identity_value = metadata["identity"]
            if identity_value is not None and (
                not isinstance(identity_value, str) or Path(identity_value).is_absolute()
            ):
                raise ValueError("invalid identity path")
            identity = (
                self.layout.checked(self.layout.ssh / identity_value) if identity_value else None
            )
            snapshot = SSHConfig(files["config.snapshot"].decode())
            snapshot.validate_edit()
            section = files["github.conf"].decode()
            if snapshot.section() != section:
                raise ValueError("config snapshot and GitHub section differ")
            if snapshot.identity(self.layout) != identity:
                raise ValueError("config identity and metadata differ")
            fingerprint = metadata.get("fingerprint")
            if identity:
                if "private_key" not in files:
                    raise ValueError("backup has no private key for its configured identity")
                private_fp = self.ssh.fingerprint(directory / "private_key")
                if private_fp != fingerprint:
                    raise ValueError("private key fingerprint mismatch")
                if (
                    "public_key.pub" in files
                    and self.ssh.fingerprint(directory / "public_key.pub") != private_fp
                ):
                    raise ValueError("public/private key fingerprint mismatch")
            elif any(name in files for name in ("private_key", "public_key.pub")):
                raise ValueError("key files without a configured identity")
            if type(metadata.get("managed")) is not bool:
                raise ValueError("invalid ownership flag")
            return Backup(directory, identity, files, fingerprint, metadata["managed"])
        except (ValueError, KeyError, TypeError, UnicodeError) as exc:
            raise GhsshError(f"Invalid backup: {exc}") from exc

    def restore_files(self, backup: Backup, transaction: Transaction) -> None:
        if backup.identity:
            # Paths are checked again immediately before writing.
            self.layout.checked(backup.identity)
            if not backup.identity.parent.is_dir():
                raise GhsshError("Original key directory no longer exists; restore it manually.")
            atomic_write(self.layout, backup.identity, backup.files["private_key"], 0o600)
            transaction.mark(backup.identity)
            public_path = Path(str(backup.identity) + ".pub")
            if "public_key.pub" in backup.files:
                atomic_write(self.layout, public_path, backup.files["public_key.pub"], 0o644)
                transaction.mark(public_path)
            elif public_path.exists():
                # Do not leave a stale public half paired with the restored private key.
                raise GhsshError(
                    "Backup lacks a public key but a public file exists; restore manually."
                )
