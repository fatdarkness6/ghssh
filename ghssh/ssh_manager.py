"""OpenSSH operations and local key provenance; no private key is ever displayed."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .utils import GhsshError, Layout, Runner, atomic_write, read_bytes

RESERVED = {
    "config",
    "known_hosts",
    "known_hosts.old",
    "authorized_keys",
    "authorized_keys2",
    "environment",
    "rc",
    "ghssh-state.json",
    "ghssh-switch.json",
}


def public_material(text: str) -> tuple[str, str]:
    parts = text.strip().split()
    if len(parts) < 2 or len(text.strip().splitlines()) != 1:
        raise GhsshError("Expected exactly one OpenSSH public key.")
    if (
        not (
            parts[0].startswith("ssh-")
            or parts[0].startswith("ecdsa-")
            or parts[0].startswith("sk-")
        )
        or "-cert-" in parts[0]
    ):
        raise GhsshError("Unsupported public key format (certificates are not supported).")
    try:
        blob = base64.b64decode(parts[1], validate=True)
        length = struct.unpack(">I", blob[:4])[0]
        if blob[4 : 4 + length].decode("ascii") != parts[0] or len(blob) <= 4 + length:
            raise ValueError("key type mismatch")
    except (ValueError, UnicodeError, binascii.Error, struct.error) as exc:
        raise GhsshError("Invalid OpenSSH public key encoding.") from exc
    return parts[0], parts[1]


def public_fingerprint(text: str) -> str:
    """OpenSSH SHA256 format for remote public keys, with no temporary files."""
    _, encoded = public_material(text)
    digest = hashlib.sha256(base64.b64decode(encoded)).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


@dataclass(frozen=True)
class KeyInfo:
    private: Path | None
    public: Path | None
    fingerprint: str | None


class SSHManager:
    def __init__(self, layout: Layout, runner: Runner):
        self.layout = layout
        self.runner = runner
        self.env = dict(os.environ)
        self.started_agent = False
        self.agent_directory: Path | None = None

    def fingerprint(self, path: Path) -> str:
        self.layout.checked(path, exists=True)
        result = self.runner.run(["ssh-keygen", "-E", "sha256", "-lf", str(path)])
        match = re.search(r"(?m)^\d+ (SHA256:[A-Za-z0-9+/]+)(?:\s|$)", result.stdout)
        if not match or len(result.stdout.strip().splitlines()) != 1:
            raise GhsshError(f"Cannot identify a single SSH key fingerprint: {path}")
        return match.group(1)

    def validate_config(self, text: str) -> None:
        """Let OpenSSH catch invalid directives, reading stdin without a temp file."""
        self.runner.run(
            ["ssh", "-G", "-F", "/dev/stdin", "-o", "CanonicalizeHostname=no", "github.com"],
            input=text,
        )

    def public_text(self, private: Path) -> str:
        path = self.layout.checked(Path(str(private) + ".pub"), exists=True)
        text = read_bytes(path).decode("utf-8").strip()
        public_material(text)
        return text

    def identity_fingerprint(self, private: Path) -> str:
        self.layout.checked(private)
        public = self.layout.checked(Path(str(private) + ".pub"))
        private_fp = self.fingerprint(private) if private.exists() else None
        public_fp = self.fingerprint(public) if public.exists() else None
        if private_fp and public_fp and private_fp != public_fp:
            raise GhsshError(
                "Private and public key fingerprints differ; refusing to guess the identity."
            )
        if not (private_fp or public_fp):
            raise GhsshError(f"No key files exist for {private}")
        return private_fp or public_fp

    def key_path(self, name: str) -> Path:
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", name)
            or name in RESERVED
            or name.endswith(".pub")
            or name.startswith("ghssh-")
        ):
            raise GhsshError(
                "Key name must be a simple basename (letters, digits, _, -, .), not a reserved SSH file."
            )
        return self.layout.checked(self.layout.ssh / name)

    def available(self, path: Path) -> None:
        for candidate in (path, Path(str(path) + ".pub")):
            self.layout.checked(candidate)
            if candidate.exists():
                raise GhsshError(
                    f"Key file already exists: {candidate}. Choose a new --key-name; existing keys are retained."
                )

    def generate(self, path: Path, comment: str, *, no_passphrase: bool = False) -> None:
        self.available(path)
        if "\n" in comment or "\r" in comment or "\x00" in comment:
            raise GhsshError("Key comment must be a single line.")
        with tempfile.TemporaryDirectory(prefix=".ghssh-key-", dir=self.layout.ssh) as directory:
            staged = Path(directory) / "identity"
            args = ["ssh-keygen", "-t", "ed25519", "-f", str(staged), "-C", comment]
            if no_passphrase:
                args += ["-N", ""]
            self.runner.run(args, interactive=not no_passphrase)
            public = Path(str(staged) + ".pub")
            if not staged.is_file() or not public.is_file():
                raise GhsshError("ssh-keygen did not produce a complete key pair.")
            public_material(read_bytes(public).decode())
            staged.chmod(0o600)
            public.chmod(0o644)
            self.available(path)
            # Hard links publish each new file with exclusive-create semantics.
            # The staging directory is deleted before the key is subsequently inspected.
            os.link(staged, path, follow_symlinks=False)
            try:
                os.link(public, Path(str(path) + ".pub"), follow_symlinks=False)
            except BaseException:
                path.unlink()
                raise

    def agent_fingerprints(self) -> tuple[bool, set[str]]:
        if not shutil.which("ssh-add"):
            return False, set()
        result = self.runner.run(["ssh-add", "-l", "-E", "sha256"], check=False, env=self.env)
        if result.returncode not in (0, 1):
            return False, set()
        return True, set(re.findall(r"(?m)^\d+ (SHA256:[A-Za-z0-9+/]+)\s", result.stdout or ""))

    def ensure_agent(self) -> None:
        if self.agent_fingerprints()[0]:
            return
        # Unix socket paths are limited to about 108 bytes; HOME can be much longer.
        self.agent_directory = Path(tempfile.mkdtemp(prefix="ghssh-agent-", dir="/tmp"))
        socket_path = self.agent_directory / "agent.sock"
        try:
            result = self.runner.run(["ssh-agent", "-a", str(socket_path), "-s"], env=self.env)
        except BaseException:
            shutil.rmtree(self.agent_directory)
            self.agent_directory = None
            raise
        socket = re.search(r"(?:^|\n)SSH_AUTH_SOCK=([^;\n]+);", result.stdout)
        pid = re.search(r"(?:^|\n)SSH_AGENT_PID=(\d+);", result.stdout)
        if not socket or not pid or not socket.group(1).startswith("/"):
            raise GhsshError("Could not parse ssh-agent environment.")
        self.env.update(SSH_AUTH_SOCK=socket.group(1), SSH_AGENT_PID=pid.group(1))
        self.started_agent = True

    def add_agent(self, path: Path) -> None:
        self.ensure_agent()
        self.runner.run(["ssh-add", str(path)], interactive=True, env=self.env)

    def remove_agent(self, path: Path) -> None:
        self.identity_fingerprint(path)
        public = Path(str(path) + ".pub")
        # ssh-add -d accepts a public key; it never needs the private key's passphrase.
        key = public if public.is_file() else path
        self.runner.run(["ssh-add", "-d", str(key)], env=self.env)

    def stop_started_agent(self) -> None:
        if self.started_agent:
            self.runner.run(["ssh-agent", "-k"], env=self.env, check=False)
            self.started_agent = False
            if self.agent_directory is not None:
                shutil.rmtree(self.agent_directory)
                self.agent_directory = None

    def discover(self) -> list[KeyInfo]:
        self.layout.checked(self.layout.config)
        if not self.layout.ssh.exists():
            return []
        pairs: dict[str, dict[str, Path]] = {}
        for path in sorted(self.layout.ssh.iterdir()):
            if path.name in RESERVED or path.name.startswith("."):
                continue
            if path.is_symlink() or not path.is_file():
                continue
            try:
                self.layout.checked(path, exists=True)
                data = read_bytes(path, limit=1024 * 1024)
                if path.suffix == ".pub":
                    public_material(data.decode())
                    pairs.setdefault(path.name[:-4], {})["public"] = path
                elif re.match(
                    rb"-----BEGIN (?:OPENSSH |RSA |EC |DSA |ENCRYPTED )?PRIVATE KEY-----", data
                ):
                    pairs.setdefault(path.name, {})["private"] = path
            except (GhsshError, OSError, UnicodeError):
                continue
        result = []
        for pair in pairs.values():
            try:
                fingerprint = self.fingerprint(pair.get("public") or pair["private"])
            except GhsshError:
                fingerprint = None
            result.append(KeyInfo(pair.get("private"), pair.get("public"), fingerprint))
        return result

    def registry(self) -> dict[str, str]:
        path = self.layout.checked(self.layout.state)
        if not path.exists():
            return {}
        try:
            data = json.loads(read_bytes(path))
            if not isinstance(data, dict) or data.get("version") != 1:
                raise ValueError("unsupported state version")
            keys = data["keys"]
            if not isinstance(keys, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in keys.items()
            ):
                raise ValueError("invalid key registry")
            return keys
        except (ValueError, KeyError, TypeError) as exc:
            raise GhsshError(
                "Invalid ghssh ownership state; refusing destructive actions."
            ) from exc

    def record(self, path: Path, fingerprint: str | None) -> None:
        keys = self.registry()
        if fingerprint is None:
            keys.pop(path.name, None)
        else:
            keys[path.name] = fingerprint
        atomic_write(
            self.layout,
            self.layout.state,
            json.dumps({"version": 1, "keys": keys}, indent=2).encode() + b"\n",
        )

    def owned(self, path: Path) -> bool:
        if path.parent != self.layout.ssh:
            return False
        expected = self.registry().get(path.name)
        if not expected or not path.is_file():
            return False
        public = Path(str(path) + ".pub")
        return self.fingerprint(path) == expected and (
            not public.exists() or self.fingerprint(public) == expected
        )

    def test_github(self) -> tuple[bool, str]:
        result = self.runner.run(
            ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "git@github.com"],
            check=False,
            env=self.env,
        )
        output = "\n".join(filter(None, [result.stdout, result.stderr])).strip()
        success = result.returncode in (0, 1) and bool(
            re.search(
                r"Hi [^\r\n!]+! You've successfully authenticated, but GitHub does not provide shell access\.",
                output,
            )
        )
        return success, output
