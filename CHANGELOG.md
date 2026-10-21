# Release notes

## v0.3.0 — 2026-10-21

- Add native Windows 10/11 support for CMD and PowerShell.
- Use Windows file ownership checks, protected current-user ACLs and process locks.
- Reject junctions, other reparse points, alternate data streams and device paths.
- Use native Windows OpenSSH and its authentication agent service; copy public keys with `clip.exe`.
- Support paths containing spaces and Windows drive letters without changing the switch/return workflow.
- Add a per-user `.exe` installer that adds ghssh to PATH, a portable ZIP and SHA-256 checksums.
- Bundle Python and Python dependencies so Windows users need no environment activation.
- Add Linux/Windows CI and test source behavior, frozen executable, installation, upgrade and uninstall before publishing Windows downloads.

Windows binaries are built and tested on GitHub's Windows runner before being
published to GitHub Releases. The checks cover native Windows permissions,
short/long repository path aliases, the bundled executable, CMD and PowerShell,
PATH setup, upgrades and uninstalling. Git for Windows and Windows OpenSSH Client
are separate prerequisites. The installer does not modify existing SSH keys or
start system services.

## v0.2.0 — 2026-10-15

- Switch a repository's commit name and email together with its GitHub SSH key.
- Use `--git-name`, `--git-email`, and optional `--repo`; interactive switches ask for missing identity details.
- Restore the original repository-local identity with `ghssh switch --back`, including inheritance from global settings.
- Keep SSH-only switching available explicitly with `--ssh-only`.
- Preview both SSH and commit changes with `--dry-run`.
- Roll back both settings on failure, preserve unrelated Git config changes, and refuse conflicting author overrides.
- Support existing v0.1.0 SSH-only return points and isolated Git worktree configuration.

Commit identity changes apply only to the selected repository. Global Git settings
and previously created commits are unchanged. New sessions record the repository
so `--back` can restore it even when run from another directory.

## v0.1.0 — 2026-10-15

The first release of ghssh for Linux.

- Temporarily switch to another GitHub account's SSH key, then return with `ghssh switch --back`.
- Create a separate key for another account while keeping your original key intact.
- Add, rotate, list, remove, and test GitHub SSH keys.
- Back up your setup and safely restore the GitHub configuration.
- Preview changes with `--dry-run` before applying them.
- Optionally upload public keys with the authenticated GitHub CLI.
- Install with pipx and run `ghssh` from any directory without activating an environment.

Safety features include fingerprint checks, backups, protected file permissions,
conservative SSH config edits, and rollback when local operations fail.

Requires Python 3.11+ and OpenSSH. This release supports Linux only.
