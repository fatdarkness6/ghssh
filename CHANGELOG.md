# Release notes

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
