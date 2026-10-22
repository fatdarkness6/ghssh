# ghssh

**Save your GitHub accounts once. Switch by name, with the right commit identity.**

`ghssh` is a Python terminal tool for Linux and Windows. Give each GitHub account
a nickname, connect it to its SSH key, and save its commit name/email. Switch
between two, three, or more accounts without typing the details again.

**Version:** 0.4.0 · **Platforms:** Linux, Windows 10/11 x64 · **License:** MIT

```sh
ghssh add arsam     # Guided setup: reuse your current key and save commit details.
ghssh add arvand    # Reuse another key, or create a separate one.
ghssh add work      # Add as many accounts as you need.
# From your Git project:
ghssh switch arvand
ghssh switch work
ghssh switch arsam
```

No `--back` is needed for saved accounts. Setup does not change the active account.
`switch NAME` selects the saved SSH key for github.com and sets the saved commit
name/email **only in the selected repository**. Browser and HTTPS logins are separate.

New to SSH? Run **`ghssh guide`** for a step-by-step walkthrough, or read the
[account setup guide](#set-up-your-accounts-once) below.

## Installation

### Windows: install once, run from CMD or PowerShell

Windows builds include Python and all Python dependencies. You do not need to
install Python, activate an environment, or use WSL.

Download the tested Windows installer from
[GitHub Releases](https://github.com/fatdarkness6/ghssh/releases/latest).
The release includes the installer, a portable ZIP and SHA-256 checksums.

1. Install [Git for Windows](https://gitforwindows.org/) and **Windows OpenSSH Client**
   from Windows Optional features if they are not installed already.
2. Download `ghssh-0.4.0-windows-x64-setup.exe` from the release assets and run it.
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

The account setup and switch commands are identical in CMD and PowerShell:

```powershell
ghssh guide
ghssh add arsam --key id_ed25519
ghssh add arvand
cd "C:\Users\YourName\Projects\YourProject"
ghssh switch arvand
ghssh switch arsam
```

Setup asks for each account's commit name and verified email. For a newly created
key, paste the displayed **public** key into that account's GitHub SSH key settings
before switching. Test the selected username with `ssh -T git@github.com`.

Windows uses `%USERPROFILE%\.ssh` for keys, configuration, state and backups.
ghssh uses the **native Windows OpenSSH suite** in `%SystemRoot%\System32\OpenSSH`.
Use forward slashes and double quotes for manually configured SSH identity paths,
for example `IdentityFile "C:/Users/Your Name/.ssh/github_second"`. ghssh writes
this format automatically. A conflicting `HOME` environment variable is refused
so Git and native OpenSSH do not silently read different home directories.

`add NAME` and `switch NAME` need no running agent. Key rotation with `replace` loads the new key into the Windows
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

## Everyday commands

| Command | What to use it for |
| --- | --- |
| `ghssh guide` | Read setup instructions and copyable examples |
| `ghssh add NAME` | Save a nickname, SSH key and commit name/email; reuse or create a key |
| `ghssh accounts` | List saved names, keys and commit details; show the selected SSH key |
| `ghssh switch NAME` | Select a saved key and commit identity in the current Git project |
| `ghssh switch NAME --repo PATH` | Select the commit identity in a specific project |
| `ghssh switch NAME --ssh-only` | Select SSH only, including outside a Git project |
| `ghssh status` | Inspect SSH and the current project's effective commit identity |
| `ghssh test` | Test noninteractive GitHub SSH authentication |
| `ghssh list` | Inspect detected key files, fingerprints and agent identities |
| `ghssh forget NAME` | Forget a nickname while keeping its key files and current settings |
| `ghssh replace` | Rotate the currently selected key; update its saved profile and keep the old key |
| `ghssh backup` | Back up the current GitHub SSH setup |
| `ghssh restore --list` | List SSH backup IDs; restore with `ghssh restore ID` |
| `ghssh remove --config-only` | Remove the GitHub SSH block while keeping keys and profiles |

Run `ghssh COMMAND --help` for that command's options and examples. Use
`--dry-run` on add, switch, forget, replace, remove, backup or restore to preview
without changing files, permissions, agents, the clipboard or GitHub. Use
`--yes` / `-y` to confirm setup or maintenance without a confirmation prompt.
Named switches apply immediately; they need no extra confirmation.
`ghssh --verbose COMMAND` adds error context.

## Set up your accounts once

### 1. Save the account you already use

If you have an existing key such as `~/.ssh/id_ed25519`, reuse it:

```sh
ghssh add arsam --key id_ed25519
```

Enter the commit name and email belonging to that GitHub account. Use an email
verified on that account, or its GitHub noreply address. The nickname `arsam` is
just a local label; it does not have to match your GitHub username. Names accept
letters, numbers, underscores and hyphens, and are stored in lowercase.

If you are unsure which key you have, run `ghssh list`, or just `ghssh add` to
start the wizard. It lists detected private-key filenames and asks for a nickname.
Reusing a key preserves its contents, permissions and ownership registry status;
it does not claim an imported key as generated by ghssh.

### 2. Save another account

```sh
ghssh add arvand
```

At the key question, enter its existing private-key filename, or press Enter to
create a new Ed25519 pair at `~/.ssh/github_arvand`. Enter that account's commit
name/email. Existing destination files are never overwritten. `--key-name FILE`
chooses another filename for a **new** key; `--key FILE` reuses an existing one.

A **passphrase** is a password protecting the private key on your computer.
OpenSSH asks for it directly in your terminal; ghssh never stores the passphrase.
For unattended creation only, `--no-passphrase` explicitly creates an unencrypted
key. It is not used when reusing an existing key.

For a new key, sign into the **matching GitHub account** and paste the displayed
**public key** at [Settings → SSH and GPG keys → New SSH key](https://github.com/settings/ssh/new),
choosing Authentication Key. Add `--copy` to copy it during setup. Keep the
private key private. Registering the public key determines which GitHub account
the key authenticates as; a nickname or email alone cannot change that.

Repeat with `ghssh add work` for a third account, and any other names you need.
Saving a profile does not change active SSH settings, Git settings or the agent.
Profiles are stored privately in `~/.ssh/ghssh-accounts.json` (Windows:
`%USERPROFILE%\.ssh\ghssh-accounts.json`). Each profile contains a key basename,
fingerprint, commit name and email; it contains no private-key bytes or passphrase.
The same key cannot be assigned to different account nicknames.

Fully specified, unattended examples (the first reuses a key; the second creates one):

```sh
ghssh add arsam --key id_ed25519 --git-name "Arsam" --git-email "arsam@example.com" --yes
ghssh add arvand --git-name "Arvand" --git-email "arvand@example.com" --no-passphrase --yes
```

### 3. Switch from your Git project

```sh
cd /path/to/your/project
ghssh accounts
ghssh switch arvand
ssh -T git@github.com
ghssh status
# Work normally in Git or VS Code.
ghssh switch work
ghssh switch arsam
```

On Windows, use your own project path, such as `cd "C:\Users\YourName\Projects\YourProject"`.
The switch commands are the same in Linux shells, CMD and PowerShell. You do not
need `--create`, repeated commit details or `--back` with saved names. Selecting
the same account again is allowed, including when configuring another project.

Use an **SSH remote**, such as `git@github.com:OWNER/REPOSITORY.git`. Check with
`git remote -v`. SSH key selection does not affect HTTPS authentication.
GitHub's SSH greeting identifies the account selected by your key. `ghssh status`
shows the effective commit email in your project. These are separate settings;
named switching updates both. Encrypted keys can prompt for their passphrase
when you use interactive SSH or Git. `ghssh test` uses BatchMode and cannot ask
for a passphrase; use `ssh -T git@github.com` to test interactively.

```sh
ghssh switch arvand --dry-run
ghssh switch arvand --repo /path/to/another/project
ghssh switch arvand --ssh-only
```

**Scope:** the github.com SSH selection applies across repositories using this
SSH configuration. Commit identity changes affect future commits only in the
selected repository, including VS Code commits. Other repositories' local Git
settings, global Git settings and existing commits are preserved. Browser
sessions, GitHub CLI (`gh`) logins and HTTPS credentials are separate.

Each switch creates a safety backup and checks the saved key fingerprint. SSH
and Git edits are locked and rolled back together on failure. Unrelated Git
settings are preserved. Local Include/IncludeIf settings and explicit author,
committer or environment overrides are refused. Linked worktrees need
`extensions.worktreeConfig` so their commit identities can be scoped separately.

To correct saved details, run `ghssh forget NAME`, then `ghssh add NAME --key KEY`
and enter the corrected details. Forgetting keeps all keys, active settings and
backups. `replace` updates a saved profile when rotating its selected key. A
saved profile protects its key from `remove --delete-key`; forget the nickname
first if you really want to delete a ghssh-generated pair.

### Upgrading from temporary switching (v0.1–v0.3)

If `ghssh status` shows an older temporary switch, finish it **once**:

```sh
ghssh switch --back
```

Register the existing keys under names; do not generate replacements unnecessarily:

```sh
ghssh add arsam --key id_ed25519
ghssh add arvand --key github_second
ghssh switch arvand
ghssh switch arsam
```

Old `ghssh switch KEY --create ...` and `ghssh switch --back` commands remain
compatible for existing scripts and return points. They retain the one-level
temporary workflow. A saved nickname takes precedence over a matching key filename.
The new `add` command saves an account rather than immediately configuring SSH;
`--email` on `add` is an alias for the saved **commit email**. For raw key rotation,
`replace --email` remains a public-key comment.

## SSH configuration and security model

The tool edits a single explicit `Host github.com` block and preserves unrelated
Host blocks and comments. It sets HostName github.com, User git, a quoted absolute
IdentityFile, and IdentitiesOnly yes. It never rewrites unrelated settings from a
whole-file snapshot. ghssh refuses mutation when it
finds duplicate GitHub blocks, a shared GitHub Host block, identity-bearing
wildcards affecting GitHub, conflicting global identity settings, Include, or
Match. Split those configurations into simple explicit Host blocks first. Relative
IdentityFile paths, `%`/environment expansion, and identities outside `~/.ssh` are
also refused rather than guessed. `status` explains configuration ambiguity.

On Linux, permissions are `700` for `~/.ssh` and backup directories, `600` for
private keys, config, saved accounts, ownership state and backup files, and `644` for active public
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

For key rotation on Linux, the tool reuses the current agent. If necessary it starts one and prints export
commands for `SSH_AUTH_SOCK` and `SSH_AGENT_PID`; a child process cannot update
your parent shell. Copy those exports to use that agent in subsequent shell
commands. Restoring files does not alter the agent: load the restored identity
with the displayed `ssh-add` command when ready.
On Windows, ghssh reuses the native OpenSSH agent service and never starts a Unix
agent or changes system service settings automatically.

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

SSH backups restore the current GitHub SSH section and key, not the named-account
registry or commit settings. Keep a separate protected copy of `ghssh-accounts.json`
if you need to recover saved names.

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
`v0.4.0`, to build and publish the installer, portable ZIP and checksums to GitHub
Releases. Publication is gated on the Windows build and tests succeeding. The
build job has read-only repository permissions; only the publication job can
write release assets.

Windows executables must be built on Windows; the Linux wheel is not a Windows
executable. For a manual Windows build, install Python 3.11+ x64 and Inno Setup 6,
then use the same commands as the workflow. `packaging/windows/ghssh.iss` keeps
the installation identity stable across versions. No signing certificate is
configured in this repository.
