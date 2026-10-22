# ghssh

**Switch GitHub accounts by name. Keep your commit identity in sync.**

Save each account's SSH key, commit name and email once. Switch between personal,
work and other accounts with one command on **Linux or Windows**.

```sh
ghssh switch personal
ghssh switch work
```

**v0.4.0** · Linux · Windows 10/11 x64 · [MIT license](LICENSE)

## Install

### Windows

1. Install [Git for Windows](https://gitforwindows.org/) and **Windows OpenSSH Client** if needed.
2. Download and run the **[Windows installer (.exe)](https://github.com/fatdarkness6/ghssh/releases/download/v0.4.0/ghssh-0.4.0-windows-x64-setup.exe)**. Leave **Add ghssh to my user PATH** selected.
3. Open a new CMD or PowerShell window. Reopen VS Code if using its terminal.

Python is bundled. No environment activation is needed.
[Portable ZIP, upgrades and Windows settings →](docs/platforms.md)

### Linux

Requires Python 3.11+, Git, OpenSSH and [pipx](https://pipx.pypa.io/stable/how-to/install-pipx.html).

```sh
pipx install git+https://github.com/fatdarkness6/ghssh.git
pipx ensurepath
```

Open a new terminal, then run `ghssh --version`. No `source` or activation needed.
[Linux prerequisites and other install options →](docs/platforms.md#linux)

## Get started

### 1. Save your accounts

```sh
ghssh add personal
ghssh add work
```

Guided setup asks for a nickname, an existing key or a new one, and the account's
commit name and verified GitHub email. Add as many accounts as you need.

Already have a key? Reuse it with `ghssh add personal --key id_ed25519`.
For each **new key**, paste the displayed **public key** into the matching
[GitHub account's SSH key settings](https://github.com/settings/ssh/new) as an
**Authentication Key**. Saving an account leaves the active account unchanged.

### 2. Switch from your Git project

```sh
cd PATH_TO_YOUR_PROJECT
ghssh switch work
ghssh switch personal
```

The commands are the same on Linux, CMD and PowerShell. Each switch selects the
saved SSH key and sets the commit name/email for future commits in that project.
Switch directly between saved names; no `--back` is needed.

**Use an SSH Git remote**, such as `git@github.com:OWNER/REPO.git`. The SSH selection
applies across projects using that configuration; commit settings change only in
the selected project. Existing commits, browser sessions and HTTPS logins stay unchanged.

### 3. Check your setup

```sh
ghssh accounts
ghssh status
ssh -T git@github.com
```

## Useful commands

| Command | What it does |
| --- | --- |
| `ghssh guide` | Walk through setup step by step |
| `ghssh switch work --dry-run` | Preview a switch |
| `ghssh switch work --repo PATH` | Select another Git project |
| `ghssh switch work --ssh-only` | Select SSH without changing commit settings |
| `ghssh list` | Find existing SSH keys |
| `ghssh backup` | Back up the current GitHub SSH setup |

For all options, run `ghssh COMMAND --help` or read the [command reference](docs/usage.md).
Switches check key fingerprints, create safety backups and roll back local changes on failure.

## More help

- [Account setup, all commands and upgrading from older versions](docs/usage.md)
- [Windows/Linux installation and troubleshooting](docs/platforms.md)
- [Security, backups and recovery](docs/security.md)
- [Development and Windows builds](docs/development.md)
- [Release notes](CHANGELOG.md) · [Downloads](https://github.com/fatdarkness6/ghssh/releases/latest) · [Report an issue](https://github.com/fatdarkness6/ghssh/issues)
