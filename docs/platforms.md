[← Back to ghssh](../README.md)

# Installation and platform notes

For the shortest installation steps, start with the [README](../README.md#install).
These notes cover platform settings, portable installs, updates and troubleshooting.

## Windows

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

## Linux

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
[release notes](../CHANGELOG.md) for version history.

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
