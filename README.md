# ghssh

**Switch GitHub accounts safely, and return to your original SSH setup with one command.**

`ghssh` is a terminal tool for Linux and Windows for managing GitHub SSH keys. Keep a separate
key for each account, switch when you need to, and keep your original keys intact.
You can also create keys, check your connection, and back up or restore your setup.

**Version:** 0.3.0 · **Platforms:** Linux, Windows 10/11 x64 · **License:** MIT

```sh
# Run inside the repository you want to work on:
ghssh switch github_second  # Prompts for the other account's commit name/email.
ghssh switch --back         # Restore the previous SSH and commit settings.
```

New to SSH keys? The [second-account guide](#temporarily-use-a-second-github-account)
walks through creating a key and adding it to your other account. The switch can
update repository commit details along with SSH; browser login stays separate.

## Installation

### Windows: install once, run from CMD or PowerShell

Windows builds include Python and all Python dependencies. You do not need to
install Python, activate an environment, or use WSL.

Download the tested Windows installer from
[GitHub Releases](https://github.com/fatdarkness6/ghssh/releases/latest).
The release includes the installer, a portable ZIP and SHA-256 checksums.

1. Install [Git for Windows](https://gitforwindows.org/) and **Windows OpenSSH Client**
   from Windows Optional features if they are not installed already.
2. Download `ghssh-0.3.0-windows-x64-setup.exe` from the release assets and run it.
3. Leave **Add ghssh to my user PATH** selected.
4. Open a **new** CMD or PowerShell window. Reopen VS Code if using its terminal.

```powershell
ghssh --version
ghssh --help
ghssh status
```

The installer uses `%LOCALAPPDATA%\Programs\ghssh` and updates only your user
PATH. Installation does not require administrator privileges. Git and OpenSSH are
separate system prerequisites; the installer does not download or alter them.
Run ghssh in your normal user terminal.

From your project directory, create a key for your other account and switch both
SSH and future commit authorship:

```powershell
cd "C:\Users\YourName\Projects\YourProject"
ghssh switch github_second --create --git-name "Second Account" --git-email "second@example.com" --copy
```

Add the displayed **public** key to the second GitHub account, then check it:

```powershell
ssh -T git@github.com
# Work normally in Git or VS Code, using an SSH remote.
ghssh switch --back
```

For later switches, omit `--create` and reuse the existing key. Both shells accept
these single-line commands. The encrypted key can prompt for its passphrase.

Windows uses `%USERPROFILE%\.ssh` for keys, configuration, state and backups.
ghssh uses the **native Windows OpenSSH suite** in `%SystemRoot%\System32\OpenSSH`.
Use forward slashes and double quotes for manually configured SSH identity paths,
for example `IdentityFile "C:/Users/Your Name/.ssh/github_second"`. ghssh writes
this format automatically. A conflicting `HOME` environment variable is refused
so Git and native OpenSSH do not silently read different home directories.

`switch` needs no running agent. `add` and `replace` load keys into the Windows
**OpenSSH Authentication Agent** service. If that service is disabled, enable it
once from an **Administrator PowerShell**, then retry ghssh as your normal user:

```powershell
Set-Service ssh-agent -StartupType Automatic
Start-Service ssh-agent
```

Git for Windows can use its bundled SSH client. If you want Git and VS Code to
share the native Windows agent, you can explicitly select native SSH for your
repository (this is a separate Git setting):

```powershell
git config --local core.sshCommand C:/Windows/System32/OpenSSH/ssh.exe
```

Replace `C:/Windows` if your Windows directory is elsewhere. ghssh does not
overwrite an existing `core.sshCommand`, `GIT_SSH`, HTTPS credentials or browser
login. Custom SSH wrappers can bypass the expected configuration; check your
Git setup if Git authenticates differently from `ghssh test`.

The release also includes a portable ZIP. Extract the **whole folder**, including
`_internal`, and run `.\ghssh.exe`; add its folder to PATH yourself if desired.
An unsigned installer can display a Windows publisher/SmartScreen warning. The
release provides SHA-256 checksums in `SHA256SUMS`.

Upgrade by running the new installer. Uninstall through Windows Installed apps;
the uninstaller removes the PATH entry it added and leaves SSH keys, settings and
backups intact. Windows ARM-native, 32-bit, and macOS packages are not provided.

### Linux: install with pipx

Use **pipx** to install `ghssh` as a normal terminal command. pipx creates and
manages the application's Python environment and exposes its launcher on your
PATH; users do not need to run `source` or create a shell alias. Python 3.11+ and
OpenSSH's client tools (`ssh`, `ssh-keygen`, `ssh-agent`, `ssh-add`) are required.

For example, on Ubuntu 24.04+ or Debian 12+:

```sh
sudo apt update
sudo apt install pipx git python3-venv openssh-client
pipx ensurepath
```

For other distributions, use the package manager's pipx and OpenSSH packages; see
the [official pipx installation instructions](https://pipx.pypa.io/stable/how-to/install-pipx.html).

Install directly from GitHub:

```sh
pipx install 'git+https://github.com/fatdarkness6/ghssh.git'
```

Open a new terminal after `pipx ensurepath` so the PATH change takes effect. Then,
from any directory:

```sh
ghssh --help
ghssh status
```

Check the installed version with `ghssh --version`. See the
[release notes](CHANGELOG.md) for version history.

Alternatively, after cloning the repository, install from its root:

```sh
pipx install .
```

The standard `[project.scripts]` entry in `pyproject.toml` creates the `ghssh`
launcher during installation. A regular pipx installation copies the package into
its managed environment; users can move or delete their source checkout afterward.
For updates and removal:

```sh
pipx upgrade ghssh
pipx uninstall ghssh
```

Uninstalling the application does not delete SSH keys, configuration or backups.
Optional helpers: `gh` for GitHub integration and `wl-copy`, `xclip`, or `xsel`
for `--copy`.

For development or a manually managed virtual environment instead:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/ghssh --help
```

Only this manually managed alternative needs activation or the full executable
path. Installing the application with pipx does not require root; the `sudo apt`
commands above are only for installing system prerequisites.

## Usage

```sh
ghssh status
ghssh add --email user@example.com
ghssh add --github --copy
ghssh list
ghssh test
ghssh replace --dry-run
ghssh replace --github
ghssh backup
ghssh restore --list
ghssh restore 2026-10-15_09-30-00_ab12cd34 --dry-run
ghssh restore 2026-10-15_09-30-00_ab12cd34
ghssh remove --config-only
ghssh remove --delete-key
```

`add` prompts for a basename within `~/.ssh`, defaulting to `github_ed25519`.
`replace` chooses a fresh timestamped name and keeps both the previous local key
and its remote GitHub registration. An existing destination is always refused;
choose a different `--key-name`. `--email` and `--comment` are aliases.

OpenSSH prompts directly for a passphrase, keeping it out of Python, logs and
process arguments. Use a terminal for this prompt. Unattended creation requires
the explicit `--no-passphrase` option, which produces an **unencrypted** private
key. `--yes`/`-y` confirms local changes; it does not select a backup, bypass path
checks, or bypass the separate remote deletion confirmation.

| Command | Action |
| --- | --- |
| `add` | Create Ed25519 key pair, load agent, configure GitHub, show public key |
| `replace` | Back up existing setup and switch to a newly generated key |
| `switch KEY` / `switch --back` | Temporarily switch SSH and repository commit identity, then restore both |
| `remove` | Remove GitHub block; optionally delete a verified owned key pair |
| `list` | Show detected private/public pairs, roles, fingerprints and agent identities |
| `status` | Inspect directory, config, key pair, agent and GitHub CLI authentication |
| `test` | Run SSH authentication test, recognizing GitHub's successful exit status 1 |
| `backup` | Save current GitHub setup and metadata |
| `restore` | List/select a backup and safely restore its GitHub setup |

Creation, replacement, switching, removal, backup and restore support `--dry-run`. The plan
includes target files, permissions, agent actions and remote operations. A dry run
does not create directories, change permissions, start agents, invoke clipboard
helpers or modify GitHub. Timestamped backup IDs are allocated only on execution.
Read-only inspection commands (including GitHub API checks with `--github`) may run.
`ghssh --verbose COMMAND` adds error context; every command supports `--help`.

## SSH configuration and security model

The tool edits a single explicit `Host github.com` block and preserves unrelated
Host blocks and comments. It sets HostName github.com, User git, a quoted absolute
IdentityFile, and IdentitiesOnly yes. It never rewrites unrelated settings from a
whole-file snapshot. This first release deliberately refuses mutation when it
finds duplicate GitHub blocks, a shared GitHub Host block, identity-bearing
wildcards affecting GitHub, conflicting global identity settings, Include, or
Match. Split those configurations into simple explicit Host blocks first. Relative
IdentityFile paths, `%`/environment expansion, and identities outside `~/.ssh` are
also refused rather than guessed. `status` explains configuration ambiguity.

On Linux, permissions are `700` for `~/.ssh` and backup directories, `600` for
private keys, config, ownership state and backup files, and `644` for active public
keys. On Windows, ghssh uses protected current-user ACLs for generated files and
directories, including public keys; POSIX mode bits do not protect Windows keys.
Windows requires a local drive with Windows security support, normally NTFS;
network shares and device namespaces are refused. Ownership is checked against
the current user and their process token's default owner; protected files are
assigned to the current user. Operations refuse symlinks, Windows reparse points
(including junctions), hard-linked files, unowned paths, special files and
path traversal. New names are basenames, never arbitrary paths. Existing target
files are not overwritten during generation.

Deletion requires more than a GitHub-looking filename: the exact private-key
fingerprint must match `ghssh-state.json`, its public half must agree if present,
and no other Host block may reference the identity. This registry records only
keys generated by ghssh or restored from its managed backups. A pre-existing key
can be replaced and backed up, but its files cannot be deleted automatically.
Choose `--config-only` to keep them. ghssh cannot know whether a key is also
authorized by an external server; retaining key files is the default, including
with `remove --yes`. Keep a separate identity for every purpose.

Mutations use an advisory process lock, preflight checks, atomic file replacement,
and rollback of touched local files on errors and Ctrl-C. Backups survive failed
operations. Agent changes are compensated during failed key creation when
possible. Agent unloads and remote API calls cannot participate in filesystem
transactions. If remote upload/deletion fails, the complete local change and its
backup are retained, with an explicit error and recovery instructions. SIGKILL,
power loss and edits by programs that ignore ghssh's lock cannot be fully rolled
back automatically. The account running ghssh and its home directory are trusted.

On Linux, the tool reuses the current agent. If necessary it starts one and prints export
commands for `SSH_AUTH_SOCK` and `SSH_AGENT_PID`; a child process cannot update
your parent shell. Copy those exports to use that agent in subsequent shell
commands. Restoring files does not alter the agent: load the restored identity
with the displayed `ssh-add` command when ready.
On Windows, ghssh reuses the native OpenSSH agent service and never starts a Unix
agent or changes system service settings automatically.

## Temporarily use a second GitHub account

Use `switch` for account changes. Keep `replace` for permanent key rotation.
Run it from the Git repository you want to work on, or select one with `--repo`.
The first time, create a separate key while saving a return point:

```sh
ghssh switch github_second --create --email "second@example.com" --copy \
  --git-name "Second Account" --git-email "second@example.com"
```

This retains the original key, creates `~/.ssh/github_second`, backs up the current
setup, temporarily selects the second key for `github.com`, and sets the selected
repository's `user.name` and `user.email`. It also works if
the original account uses a default key such as `id_ed25519` with no explicit
GitHub Host block. Enter a passphrase when OpenSSH asks.

Sign into the **second** account in your browser and add the displayed public key
at [GitHub's SSH key settings](https://github.com/settings/ssh/new). The email is
just a key comment. `--git-email` separately sets the commit author email and must
be verified on the target account (its GitHub noreply address is also supported).
The GitHub account where you register the public key determines which account
authenticates. `switch` does not use the current `gh` login to upload anything.

```sh
ssh -T git@github.com  # Check that GitHub greets the second account's username.
# Use normal Git commands with SSH remotes: git@github.com:OWNER/REPO.git
ghssh switch --back
ssh -T git@github.com  # Check that your original account is back.
```

For later switches, reuse the existing second key:

```sh
ghssh switch github_second --git-name "Second Account" --git-email "second@example.com"
# Work with your second account.
ghssh switch --back
```

If you already have the second account's key, start directly with
`ghssh switch EXISTING_KEY_BASENAME`; `--create` is only needed once. Interactive
switches prompt for missing commit name/email. With `--yes` or `--dry-run`, provide
both values explicitly. Existing destination keys are never overwritten.

To select a repository while running from another directory:

```sh
ghssh switch github_second --repo /path/to/your/repository \
  --git-name "Second Account" --git-email "second@example.com" --dry-run
```

For an SSH-only switch, including when outside a Git repository:

```sh
ghssh switch github_second --ssh-only
ghssh switch --back
```

Switching changes the GitHub SSH configuration and, unless `--ssh-only` is used,
the selected repository's commit name/email. It leaves existing key files and loaded
agent identities alone and disables automatic agent additions for the temporary
GitHub block. An encrypted alternate key may prompt for its passphrase during
normal Git operations. Use the interactive `ssh -T` check above; `ghssh test` uses
BatchMode, which cannot prompt for a key that is not loaded in an agent. Conflicting
global or wildcard AddKeysToAgent settings are refused for temporary switches.

The return point is stored privately in `~/.ssh/ghssh-switch.json` and survives
terminal restarts. Return restores the previous GitHub section while keeping
changes to unrelated Host blocks. It checks the previous key's fingerprint and
refuses to overwrite a GitHub block changed externally during the switch. A safety
backup is created in both directions. Switches have one return point: return before
starting another switch. Replacement, removal and backup restoration are blocked
while a temporary switch is active, protecting that return point.

Commit name/email changes affect future commits in the selected repository,
including normal commits made in VS Code. They never change global settings or
rewrite existing commits. On return, previous local values are restored; if those
values were absent, the overrides are removed so Git inherits its current defaults.
Unrelated Git config edits made during the switch are preserved. A changed commit
identity is treated as a conflict and stops automatic return. The safety backup
includes `git-identity.json` containing the saved author fields for manual recovery.

Only one repository's commit settings are selected, while the `github.com` SSH
selection applies to normal SSH connections from all repositories. Browser
sessions, `gh` authentication and HTTPS credentials remain independent. Local
Include/IncludeIf directives and explicit author/committer overrides require manual
identity management; environment overrides that bypass the selected identity are
also refused. Linked worktrees require `extensions.worktreeConfig` to be enabled
so each worktree can have its own identity.

Saved SSH-only return points from v0.1.0 still work and restore only SSH settings.
Finish any existing switch before starting a new combined SSH/commit switch.

## Backups and recovery

Backups live in `~/.ssh/ghssh-backups/YYYY-MM-DD_HH-MM-SS_<id>/` (UTC):

```text
private_key       # if the configured key exists; mode 600
public_key.pub    # if available; also mode 600 in backups
config.snapshot  # complete config for reference, never blindly restored
github.conf      # original GitHub section
metadata.json    # version, original relative path, ownership, fingerprint, SHA256 checksums
```

Replacement always backs up the old setup. Removal backs up by default;
`--no-backup` explicitly opts out. Backups are never automatically pruned. They
contain private keys and should be protected like the originals. Checksums detect
corruption, not malicious changes made by the owning account.

Restore validates metadata, paths, file checksums, config consistency and key-pair
fingerprints before changing anything. It creates a new backup of the current
GitHub setup and copies any existing restoration-target bytes to
`restore-target.private` / `restore-target.pub` in that recovery directory.
`restore-target.txt` records their original destination. This also preserves a
target that differs from the current GitHub key. Only the GitHub config section
is restored; unrelated Host blocks keep their current contents. Confirmed restore
can overwrite the displayed original key paths; it refuses targets referenced by
other configured hosts. A backup of a missing private key remains useful for
manual recovery but cannot be automatically restored as a complete setup.

No command deletes old backups or old keys left by replacement. Review those
manually after verifying that the new key works everywhere it is needed.

## GitHub CLI integration

Authenticate using `gh auth login --hostname github.com`. `--github` on add or
replace uploads only the public key as an authentication key. Your authenticated
session needs permission to manage SSH authentication keys (for classic OAuth
tokens, `admin:public_key`; use `gh auth refresh --hostname github.com -s admin:public_key`
if gh reports a missing scope). All requests target github.com even if `GH_HOST`
is set to an enterprise host.

`remove --github` lists remote authentication keys using the GitHub API and
compares their SHA256 public-key fingerprints to the configured local identity.
It displays the account, numeric key ID, title and fingerprint, then requires a
separate confirmation even with `--yes`. The fingerprint/ID match is checked
again immediately before deletion. Filenames and titles never authorize deletion.
Remote signing keys and repository deployment keys are outside this release's scope.
Without `--github`, local commands work without gh or any GitHub account.

If registering manually, paste the displayed public key at
[GitHub's SSH key settings](https://github.com/settings/ssh/new), then run
`ghssh test`. The test uses BatchMode and a connection timeout. It does not disable
host-key verification or auto-trust a new host. If OpenSSH reports an unknown host
key, verify GitHub's published host fingerprints before trusting it through an
interactive SSH connection. A matching successful authentication message with
exit status 0 or 1 means success; a network failure or permission denial does not.

## Development

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
# Optional: real OpenSSH, disposable local keys and a dedicated temporary agent:
GHSSH_RUN_INTEGRATION=1 .venv/bin/python -m pytest -m integration
```

Tests use temporary homes and mock command execution. No real GitHub account,
network access, personal keys, running agent or desktop clipboard is required.
`Runner` is the injection point for system commands; `Layout` supplies an isolated
home directory. Filesystem, config parsing, SSH, GitHub, backup workflows and CLI
presentation live in separate modules. Native Windows security tests run on the
Windows CI runner; macOS remains unsupported.

### Building the Windows downloads

[Tests](.github/workflows/ci.yml) runs on Linux and Windows with Python 3.11 and
3.13. The [Windows installer workflow](.github/workflows/windows-release.yml)
tests the source, exercises real local OpenSSH/Git, builds a console application
with PyInstaller, and tests the frozen executable. Inno Setup builds the per-user
installer, followed by installation, CMD/PowerShell/PATH, upgrade, and uninstall
checks. These checks use disposable profiles; they do not contact GitHub or load
keys into an agent.

Pushes to `main` automatically build a Windows preview. You can also run
**Actions → Windows installer → Run workflow**. Successful preview runs provide
downloadable build artifacts without publishing a release.
After reviewing that build, push a tag matching the package version, such as
`v0.3.0`, to build and publish the installer, portable ZIP and checksums to GitHub
Releases. Publication is gated on the Windows build and tests succeeding. The
build job has read-only repository permissions; only the publication job can
write release assets.

Windows executables must be built on Windows; the Linux wheel is not a Windows
executable. For a manual Windows build, install Python 3.11+ x64 and Inno Setup 6,
then use the same commands as the workflow. `packaging/windows/ghssh.iss` keeps
the installation identity stable across versions. No signing certificate is
configured in this repository.
