"""Exercise the frozen executable using only a disposable home and repository."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from ghssh import __version__


def main() -> None:
    executable = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="ghssh-frozen-") as temporary:
        home = Path(temporary) / "profile with spaces"
        home.mkdir()
        env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
        for key in tuple(env):
            if key.startswith("GIT_"):
                del env[key]

        def run(*args: str) -> str:
            result = subprocess.run(
                list(args), cwd=home, env=env, capture_output=True, text=True,
                encoding="utf-8", timeout=60, check=False,
            )
            if result.returncode:
                raise RuntimeError(f"Smoke command failed: {args[0]}\n{result.stdout}\n{result.stderr}")
            return result.stdout

        version = run(str(executable), "--version")
        assert f"ghssh {__version__}" in version, version
        assert "switch" in run(str(executable), "--help")
        run(str(executable), "status")
        run(
            str(executable), "switch", "github_second", "--create", "--ssh-only", "--dry-run"
        )
        assert not (home / ".ssh").exists(), "Dry-run wrote to the disposable home"

        run("git", "init", "--quiet", str(home))
        run("git", "config", "user.name", "Original Account")
        run("git", "config", "user.email", "original@example.com")
        before = (home / ".git/config").read_bytes()
        run(
            str(executable), "switch", "github_second", "--create", "--no-passphrase",
            "--git-name", "Second Account", "--git-email", "second@example.com", "--yes",
        )
        assert run("git", "config", "user.email").strip() == "second@example.com"
        key = home / ".ssh/github_second"
        assert key.is_file() and Path(str(key) + ".pub").is_file()
        run(str(executable), "list")
        run(str(executable), "status")
        run(str(executable), "backup")
        run(str(executable), "switch", "--back", "--yes")
        assert (home / ".git/config").read_bytes() == before
        assert not (home / ".ssh/config").exists()
        assert not (home / ".ssh/ghssh-switch.json").exists()
        assert key.is_file(), "Returning deleted the alternate key"
        # Exercise the same persistent profiles through the frozen launcher and
        # the installed CMD/PowerShell path (test_installer.ps1 calls this script).
        run(str(executable), "guide")
        for name in ("arsam", "arvand", "work"):
            args = [
                str(executable), "add", name, "--git-name", name.title(),
                "--git-email", f"{name}@example.com", "--yes",
            ]
            if name == "arvand":
                args += ["--key", "github_second"]
            else:
                args += ["--no-passphrase"]
            run(*args)
        assert not (home / ".ssh/config").exists(), "Adding profiles switched SSH"
        assert run("git", "config", "user.email").strip() == "original@example.com"
        for name in ("arsam", "arvand", "work", "arsam"):
            run(str(executable), "switch", name)
            assert run("git", "config", "user.email").strip() == f"{name}@example.com"
            run("git", "commit", "--quiet", "--allow-empty", "-m", name)
            assert run("git", "log", "-1", "--format=%ae").strip() == f"{name}@example.com"
            assert not (home / ".ssh/ghssh-switch.json").exists()
        saved_config = (home / ".ssh/config").read_bytes()
        saved_git = (home / ".git/config").read_bytes()
        run(str(executable), "switch", "work", "--dry-run")
        assert (home / ".ssh/config").read_bytes() == saved_config
        assert (home / ".git/config").read_bytes() == saved_git
        assert "arsam" in run(str(executable), "accounts")
        run(str(executable), "forget", "work", "--yes")
        assert (home / ".ssh/github_work").is_file(), "Forgetting a profile deleted its key"
    print("Frozen CLI smoke checks passed (no network or agent changes).")


if __name__ == "__main__":
    main()
