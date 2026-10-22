[← Back to ghssh](../README.md)

# Development

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

[Tests](../.github/workflows/ci.yml) runs on Linux and Windows with Python 3.11 and
3.13. The [Windows installer workflow](../.github/workflows/windows-release.yml)
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
