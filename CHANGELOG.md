# Release notes

## v0.4.0 — 2026-10-22

- Save any number of GitHub accounts with friendly nicknames: `ghssh add arsam`.
- Reuse existing SSH keys or create separate keys in a guided setup, and save each account's commit name and verified email once.
- Switch directly with `ghssh switch arsam`, `ghssh switch arvand`, or any other saved name; named switches need no `--back`.
- Select the saved SSH key and repository-local commit identity together; preview with `--dry-run` or explicitly select `--ssh-only`.
- Add `ghssh accounts`, `ghssh forget NAME`, and a step-by-step `ghssh guide`.
- Rewrite help around setup, examples, commit identity, passphrases, SSH remotes, and the scope of account switching.
- Protect saved profiles with private permissions, fingerprint checks, locking and atomic writes; update saved key references during rotation.
- Keep existing temporary key/return-point commands compatible. Finish an old active switch once with `ghssh switch --back` before using named switching.
- Exercise three-account switches and actual commit attribution on Linux and Windows, including the frozen executable and installed application.

`add` now saves an account without changing active SSH/Git settings or loading an
agent. After registering its public key on the correct GitHub account, run
`ghssh switch NAME` inside the project. Names are case-insensitive and stored
lowercase. Existing keys, global Git settings and old commits are preserved.

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
