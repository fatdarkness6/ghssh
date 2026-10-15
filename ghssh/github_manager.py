"""GitHub CLI integration: upload public keys and match remote keys by fingerprint."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from .ssh_manager import SSHManager, public_fingerprint, public_material
from .utils import GhsshError, Runner, read_bytes


@dataclass(frozen=True)
class RemoteKey:
    id: int
    title: str
    fingerprint: str


class GitHubManager:
    def __init__(self, runner: Runner):
        self.runner = runner

    def installed(self) -> bool:
        return shutil.which("gh") is not None

    def authenticated(self) -> bool:
        if not self.installed():
            return False
        return (
            self.runner.run(
                ["gh", "auth", "status", "--hostname", "github.com"], check=False
            ).returncode
            == 0
        )

    def require_account(self) -> str:
        if not self.installed():
            raise GhsshError("GitHub CLI is not installed. Install gh or omit --github.")
        if not self.authenticated():
            raise GhsshError(
                "GitHub CLI is not authenticated. Run: gh auth login --hostname github.com"
            )
        login = self.runner.run(
            ["gh", "api", "--hostname", "github.com", "user", "--jq", ".login"]
        ).stdout.strip()
        if not login or "\n" in login:
            raise GhsshError("Could not determine the authenticated GitHub account.")
        return login

    def add(self, public: Path, title: str, *, account: str | None = None) -> None:
        public_material(read_bytes(public).decode())
        if account is not None and self.require_account() != account:
            raise GhsshError(
                "Authenticated GitHub account changed since confirmation; upload refused."
            )
        self.runner.run(
            ["gh", "ssh-key", "add", str(public), "--title", title, "--type", "authentication"],
            env=self._env(),
        )

    @staticmethod
    def _env() -> dict[str, str]:
        import os

        # Pin gh's ssh-key subcommands to github.com even when GH_HOST is set.
        return {**os.environ, "GH_HOST": "github.com"}

    def matching(self, fingerprint: str, ssh: SSHManager) -> list[RemoteKey]:
        result = self.runner.run(
            ["gh", "api", "--hostname", "github.com", "--paginate", "--slurp", "user/keys"]
        )
        try:
            pages = json.loads(result.stdout)
            if not isinstance(pages, list) or not all(isinstance(page, list) for page in pages):
                raise ValueError("invalid paginated key response")
            matches = []
            for item in (item for page in pages for item in page):
                if (
                    not isinstance(item, dict)
                    or type(item.get("id")) is not int
                    or item["id"] <= 0
                    or not isinstance(item.get("key"), str)
                ):
                    raise ValueError("invalid remote key")
                public_material(item["key"])
                actual = public_fingerprint(item["key"])
                if actual == fingerprint:
                    matches.append(RemoteKey(item["id"], str(item.get("title", "")), actual))
            return matches
        except (ValueError, KeyError, TypeError) as exc:
            raise GhsshError(
                "GitHub returned an invalid key list; no remote key was deleted."
            ) from exc

    def delete(self, key: RemoteKey, ssh: SSHManager, *, account: str | None = None) -> None:
        if account is not None and self.require_account() != account:
            raise GhsshError(
                "Authenticated GitHub account changed since confirmation; deletion refused."
            )
        # Re-fetch and recheck immediately before deletion; never trust an ID/name alone.
        matches = self.matching(key.fingerprint, ssh)
        if key.id not in {match.id for match in matches}:
            raise GhsshError("Remote key changed since confirmation; refusing deletion.")
        self.runner.run(
            ["gh", "api", "--hostname", "github.com", "--method", "DELETE", f"user/keys/{key.id}"]
        )
