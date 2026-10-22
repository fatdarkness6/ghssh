"""Typer commands and human confirmations for ghssh."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from . import __version__
from . import platform_support as platform
from .account_manager import AccountManager, account_name
from .manager import Change, Manager
from .switch_manager import SwitchManager
from .utils import GhsshError

if platform.WINDOWS:
    # Console APIs support Unicode, but redirected Windows streams can default
    # to a legacy code page. Keep CLI pipes and captured output consistently UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(
    no_args_is_help=True,
    invoke_without_command=True,
    help=(
        "Switch between any number of GitHub accounts on Linux and Windows. "
        "Save a nickname, SSH key and commit identity once, then switch by name.\n\n"
        "Start here: ghssh add arsam (guided setup; reuse a key or create one). "
        "Repeat for arvand or any other account.\n\n"
        "Inside your Git project: ghssh switch arsam. "
        "This selects the SSH key and the name/email for future commits in that project. "
        "Use ghssh accounts to see your saved names."
    ),
    epilog=(
        "Examples: ghssh add arsam | ghssh add arvand | ghssh switch arvand | "
        "ghssh switch arsam. For step-by-step instructions: ghssh guide. "
        "For command options and examples: ghssh COMMAND --help."
    ),
)
console = Console(markup=False, highlight=False)
VERBOSE = False
Yes = Annotated[bool, typer.Option("--yes", "-y", help="Confirm local changes without prompting.")]
DryRun = Annotated[
    bool, typer.Option("--dry-run", help="Show the plan without changing files, agents, or GitHub.")
]
GitHub = Annotated[
    bool, typer.Option("--github", help="Use the authenticated github.com GitHub CLI account.")
]


def guarded(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (GhsshError, OSError, UnicodeError) as exc:
            console.print(f"Error: {exc}")
            if VERBOSE:
                console.print(
                    f"Details: {type(exc).__name__}; local file operations attempt automatic rollback."
                )
            raise typer.Exit(1) from exc
        except (KeyboardInterrupt, typer.Abort):
            console.print("Cancelled. Any in-progress local transaction was rolled back.")
            raise typer.Exit(130) from None

    return wrapper


@app.callback()
def main(
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show additional error context.")
    ] = False,
    version: Annotated[
        bool, typer.Option("--version", is_eager=True, help="Show the installed version and exit.")
    ] = False,
) -> None:
    """Manage GitHub SSH keys without disturbing unrelated SSH identities."""
    global VERBOSE
    VERBOSE = verbose
    if version:
        console.print(f"ghssh {__version__}")
        raise typer.Exit()
    if sys.platform not in {"linux", "win32"}:
        raise typer.BadParameter("This release supports Linux and Windows.")


def confirm(message: str, yes: bool) -> None:
    if not yes and not typer.confirm(message, default=False):
        raise typer.Abort()


def permission_label(mode: int) -> str:
    return "current-user ACL" if platform.WINDOWS else f"{mode:o}"


def command_text(args: list[str]) -> str:
    return subprocess.list2cmdline(args) if platform.WINDOWS else shlex.join(args)


def table(title: str, rows: list[tuple[str, str]]) -> None:
    result = Table(title=title, show_header=False, box=None)
    result.add_column(style="bold")
    result.add_column()
    for label, value in rows:
        result.add_row(Text(label), Text(value))
    console.print(result)


def summary(change: Change, configuration: str | None = None) -> None:
    table(
        "GitHub SSH changes",
        [
            ("Previous key", str(change.old_key or "none")),
            ("Current key", str(change.new_key or "none")),
            ("Backup", str(change.backup or "not created")),
            (
                "Configuration",
                configuration
                or (
                    "github.com Host block updated"
                    if change.new_key
                    else "github.com Host block removed"
                ),
            ),
        ],
    )


def publish(
    manager: Manager,
    key: Path,
    github: bool,
    copy: bool,
    account: str | None = None,
    *,
    interactive_test: bool = False,
) -> None:
    public = Path(str(key) + ".pub")
    console.print("Public key:")
    console.print(manager.ssh.public_text(key))
    console.print(f"Fingerprint: {manager.ssh.fingerprint(public)}")
    if copy:
        copy_public(manager.ssh.public_text(key))
    if github:
        try:
            manager.github.add(public, f"ghssh {key.name}", account=account)
        except GhsshError as exc:
            console.print(
                "Local setup is complete and retained. GitHub upload failed; register the public key below."
            )
            console.print(
                "Use https://github.com/settings/ssh/new or retry: "
                + command_text(["gh", "ssh-key", "add", str(public), "--type", "authentication"])
            )
            raise GhsshError(str(exc)) from exc
        console.print("Public key uploaded to GitHub. Run ghssh test to check authentication.")
    else:
        console.print("GitHub still needs this PUBLIC key: https://github.com/settings/ssh/new")
        check = "ssh -T git@github.com" if interactive_test else "ghssh test"
        console.print(f"Choose New SSH key → Authentication Key, paste it, then run {check}.")


def copy_public(text: str) -> None:
    from .utils import Runner

    candidates = [["clip.exe"]] if platform.WINDOWS else [
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
    ]
    for command in candidates:
        if shutil.which(command[0]):
            try:
                Runner().run(command, input=text + "\n", timeout=5)
                console.print("Public key copied to clipboard.")
                return
            except GhsshError as exc:
                console.print(f"Clipboard unavailable: {exc}")
    console.print("No working clipboard helper found; copy the displayed public key.")


def agent_notice(manager: Manager) -> None:
    if manager.ssh.started_agent:
        console.print("Started ssh-agent. To use it from this parent shell, run:")
        console.print(f"export SSH_AUTH_SOCK={shlex.quote(manager.ssh.env['SSH_AUTH_SOCK'])}")
        console.print(f"export SSH_AGENT_PID={shlex.quote(manager.ssh.env['SSH_AGENT_PID'])}")
        console.print("This process cannot change the parent shell's environment.")


def create_key(
    *,
    replace: bool,
    key_name: str | None,
    email: str,
    github: bool,
    no_passphrase: bool,
    copy: bool,
    yes: bool,
    dry_run: bool,
) -> None:
    manager = Manager()
    manager.ensure_not_switching()
    config, current = manager.inspect()
    if current and not replace:
        raise GhsshError("GitHub already has an identity; use ghssh replace.")
    if replace and not current:
        raise GhsshError("No GitHub-specific IdentityFile exists. Use ghssh add first.")
    default = "github_ed25519"
    if replace:
        default += "_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S_") + uuid4().hex[:6]
    if key_name is None:
        key_name = (
            default
            if yes or dry_run
            else typer.prompt("GitHub key name (inside ~/.ssh)", default=default)
        )
    key = manager.ssh.key_path(key_name)
    manager.ssh.available(key)
    manager.ssh.registry()
    manager.ssh.validate_config(config.upsert(manager.identity_value(key)))
    if any(char in email for char in ("\n", "\r", "\x00")):
        raise GhsshError("Email/comment must be one line.")
    account = manager.github.require_account() if github else None
    table(
        "Dry run: planned changes" if dry_run else "Planned changes",
        [
            ("SSH directory", f"Create if absent; protect {manager.layout.ssh} ({permission_label(0o700)})"),
            ("Old key", f"{current} (retained)" if current else "none"),
            (
                "Backup",
                f"New UTC timestamped directory under {manager.layout.backups}"
                if config.target()
                else "no existing GitHub block",
            ),
            ("Generate", f"Ed25519 pair: {key} ({permission_label(0o600)}), {key}.pub ({permission_label(0o644)})"),
            (
                "Passphrase",
                "empty (explicit --no-passphrase)"
                if no_passphrase
                else "ssh-keygen will prompt securely",
            ),
            ("SSH agent", (
                f"Use Windows OpenSSH agent service, then ssh-add {key}"
                if platform.WINDOWS else f"Use existing agent or start one, then ssh-add {key}"
            )),
            (
                "Configuration",
                f"{manager.layout.config} ({permission_label(0o600)}): GitHub IdentityFile → {key}, User git, IdentitiesOnly yes",
            ),
            ("Ownership record", f"Record new key fingerprint in {manager.layout.state} ({permission_label(0o600)})"),
            (
                "GitHub",
                f"Upload only {key}.pub to account {account}"
                if github
                else "Public key must be registered manually",
            ),
            ("Clipboard", "Copy public key using available helper" if copy else "unchanged"),
        ],
    )
    if dry_run:
        return
    if not no_passphrase and not sys.stdin.isatty():
        raise GhsshError(
            "Passphrase entry requires a terminal. Use a terminal or explicitly pass --no-passphrase."
        )
    confirm("Create and configure this GitHub key?", yes)
    change = manager.add(
        key_name,
        email or "GitHub ghssh",
        replace=replace,
        no_passphrase=no_passphrase,
        expected_config=config.text,
    )
    summary(change)
    agent_notice(manager)
    publish(manager, key, github, copy, account)


@app.command(rich_help_panel="Getting started")
def guide() -> None:
    """Read a step-by-step account setup and switching guide with copyable examples."""
    console.print("1. Save your first account: ghssh add arsam")
    console.print("   Enter a nickname, your existing private-key filename (e.g. id_ed25519),")
    console.print("   and the commit name/email belonging to that GitHub account.")
    console.print("2. Save the next account: ghssh add arvand")
    console.print("   Reuse its key, or press Enter at the key question to create a separate one.")
    console.print("   A passphrase is a password protecting the private key. OpenSSH prompts securely.")
    console.print("   Paste the displayed PUBLIC key into the matching account's SSH key settings:")
    console.print("   https://github.com/settings/ssh/new (Authentication Key). Keep the private key private.")
    console.print("3. Open your existing Git project: cd PATH_TO_YOUR_PROJECT")
    console.print("   Use an SSH remote, e.g. git@github.com:OWNER/REPOSITORY.git. Check: git remote -v")
    console.print("4. Choose an account: ghssh switch arvand")
    console.print("   SSH uses its saved key; future commits in this project use its saved name/email.")
    console.print("   Check the username: ssh -T git@github.com. Check commit settings: ghssh status")
    console.print("5. Change anytime: ghssh switch arsam")
    console.print("   Three or more accounts work the same way. No --back is needed for saved accounts.")
    console.print("Useful: ghssh accounts | ghssh list | ghssh add --help | ghssh switch --help")
    console.print("Preview: ghssh switch arvand --dry-run. Another project: ghssh switch arvand --repo PATH")
    console.print("Outside a Git project: ghssh switch arvand --ssh-only (commit settings stay unchanged).")
    console.print("SSH selection affects github.com across projects; commit identity changes only in the selected project.")
    console.print("Existing commits, browser sessions, gh CLI logins and HTTPS credentials stay unchanged.")
    console.print("Linux, Windows CMD and PowerShell use the same commands; no environment activation is needed after installation.")


@app.command(rich_help_panel="Accounts")
@guarded
def add(
    name: Annotated[
        str | None, typer.Argument(metavar="NAME", help="Nickname to save, e.g. arsam, arvand or work.")
    ] = None,
    key: Annotated[
        str | None, typer.Option("--key", help="Reuse an existing private key in ~/.ssh, e.g. id_ed25519.")
    ] = None,
    key_name: Annotated[
        str | None, typer.Option(help="Filename for a NEW key; defaults to github_NAME. Never overwrites.")
    ] = None,
    git_name: Annotated[
        str | None, typer.Option(help="Save the commit author name for this account.")
    ] = None,
    git_email: Annotated[
        str | None, typer.Option("--git-email", "--email", help="Save a verified GitHub commit email (or noreply address).")
    ] = None,
    github: GitHub = False,
    no_passphrase: Annotated[
        bool, typer.Option("--no-passphrase", help="Create a key with no passphrase (unencrypted); not used with --key.")
    ] = False,
    copy: Annotated[
        bool, typer.Option("--copy", help="Copy the public key to the clipboard.")
    ] = False,
    yes: Yes = False,
    dry_run: DryRun = False,
) -> None:
    """Save a named account. Run without options for guided setup.

    Example: ghssh add arsam --key id_ed25519 (reuse your existing key).

    Example: ghssh add arvand (create another key and save its commit details).

    Setup does not change your active SSH or Git identity. After adding the public
    key to the right GitHub account, run ghssh switch NAME from your Git project.
    A passphrase is a password protecting your private key; OpenSSH asks for it.
    """
    manager = Manager()
    accounts = AccountManager(manager)
    interactive = not (yes or dry_run)
    if name is None:
        if not interactive:
            raise GhsshError("Provide a nickname: ghssh add arsam --git-name NAME --git-email EMAIL.")
        name = typer.prompt("Account nickname (for example arsam or arvand)")
    name = account_name(name)
    if name in accounts.read():
        raise GhsshError(f"Account '{name}' already exists. Run ghssh switch {name}.")
    if key is None and key_name is None and interactive:
        detected = [item.private.name for item in manager.ssh.discover() if item.private]
        if detected:
            console.print("Existing SSH keys: " + ", ".join(detected))
        console.print("Reuse your current key for an existing account, or create a separate key for a new account.")
        key = typer.prompt(
            "Existing private-key filename (press Enter to create a new key)", default="",
            show_default=False,
        ) or None
    if key is not None and no_passphrase:
        raise typer.BadParameter("--no-passphrase creates a new key; omit it when reusing --key.")
    if git_name is None or git_email is None:
        if not interactive:
            raise GhsshError("Save both --git-name and --git-email, or omit --yes/--dry-run to use guided setup.")
        console.print("GitHub uses your commit email to credit commits. Use an email verified on THIS account.")
        git_name = git_name if git_name is not None else typer.prompt("Commit author name")
        git_email = git_email if git_email is not None else typer.prompt("Verified GitHub commit email (or noreply address)")
    plan = accounts.plan_add(name, git_name, git_email, key=key, key_name=key_name)
    path = manager.ssh.key_path(plan.account.key)
    if key is not None and (copy or github):
        manager.ssh.public_text(path)
    account = manager.github.require_account() if github else None
    table(
        "Dry run: add account" if dry_run else "Save GitHub account",
        [
            ("Nickname", name),
            ("SSH key", str(path) + (" (create new pair)" if plan.create else " (reuse existing key)")),
            ("Commit identity", f"{git_name} <{git_email}>"),
            ("Saved profiles", str(manager.layout.accounts)),
            ("Active SSH / Git / agent", "unchanged during setup"),
            ("GitHub", f"Upload public key to {account}" if github else "Register the public key on this account if needed"),
        ],
    )
    if dry_run:
        return
    if plan.create and not no_passphrase and not sys.stdin.isatty():
        raise GhsshError("Creating a key asks for a passphrase in a terminal; use a terminal or explicit --no-passphrase.")
    confirm(f"Save account '{name}'?", yes)
    accounts.add(
        name, git_name, git_email, key=key, key_name=key_name,
        no_passphrase=no_passphrase, expected=plan,
    )
    console.print(f"Saved account '{name}'.")
    if plan.create or Path(str(path) + ".pub").exists():
        publish(manager, path, github, copy, account, interactive_test=True)
    console.print(f"Next, open your Git project and run: ghssh switch {name}")
    console.print("Switching selects SSH authentication and future commit authorship together.")


@app.command(rich_help_panel="Key maintenance and recovery")
@guarded
def replace(
    key_name: Annotated[
        str | None, typer.Option(help="New basename; never the old key's name.")
    ] = None,
    email: Annotated[str, typer.Option("--email", "--comment")] = "",
    github: GitHub = False,
    no_passphrase: Annotated[
        bool, typer.Option("--no-passphrase", help="Explicitly generate an unencrypted private key.")
    ] = False,
    copy: Annotated[bool, typer.Option("--copy")] = False,
    yes: Yes = False,
    dry_run: DryRun = False,
) -> None:
    """Back up and rotate the GitHub identity, retaining the old key and remote key."""
    create_key(
        replace=True,
        key_name=key_name,
        email=email,
        github=github,
        no_passphrase=no_passphrase,
        copy=copy,
        yes=yes,
        dry_run=dry_run,
    )


@app.command("switch", rich_help_panel="Accounts")
@guarded
def switch_account(
    key_name: Annotated[
        str | None, typer.Argument(metavar="NAME", help="Saved account nickname, e.g. arsam or arvand.")
    ] = None,
    back: Annotated[
        bool, typer.Option("--back", help="Restore a legacy temporary switch; named accounts do not need this.", rich_help_panel="Legacy temporary switches")
    ] = False,
    create: Annotated[
        bool, typer.Option("--create", help="Legacy: create a temporary key. Prefer ghssh add NAME.", rich_help_panel="Legacy temporary switches")
    ] = False,
    email: Annotated[str, typer.Option("--email", "--comment", help="Legacy new-key comment; requires --create.", rich_help_panel="Legacy temporary switches")] = "",
    git_name: Annotated[
        str | None,
        typer.Option("--git-name", help="Legacy temporary switch: commit author name.", rich_help_panel="Legacy temporary switches"),
    ] = None,
    git_email: Annotated[
        str | None,
        typer.Option("--git-email", help="Legacy temporary switch: verified commit email.", rich_help_panel="Legacy temporary switches"),
    ] = None,
    repository: Annotated[
        Path | None,
        typer.Option("--repo", help="Git working repository; defaults to the current directory."),
    ] = None,
    ssh_only: Annotated[
        bool, typer.Option("--ssh-only", help="Switch SSH without changing commit author settings.")
    ] = False,
    no_passphrase: Annotated[
        bool, typer.Option("--no-passphrase", help="Legacy --create: generate an unencrypted key.", rich_help_panel="Legacy temporary switches")
    ] = False,
    copy: Annotated[
        bool, typer.Option("--copy", help="Legacy --create: copy the public key.", rich_help_panel="Legacy temporary switches")
    ] = False,
    yes: Yes = False,
    dry_run: DryRun = False,
) -> None:
    """Select a saved SSH key and author name/email for future commits in this project.

    Examples: ghssh switch arsam; ghssh switch arvand; ghssh switch work.

    Add accounts once with ghssh add NAME. Switch directly between any number of
    saved accounts; no --back or repeated commit details are needed. --dry-run
    previews changes. --repo PATH selects another project. Outside a Git project,
    use --ssh-only to leave commit settings alone.

    SSH selection applies to github.com connections from all repositories; commit
    settings apply only to the selected project. Use an SSH Git remote. Browser,
    HTTPS credentials and gh CLI logins are separate. Existing commits stay intact.
    """
    manager = Manager()
    accounts = AccountManager(manager)
    if key_name and not back and key_name.lower() in accounts.read():
        if create or email or no_passphrase or copy or git_name is not None or git_email is not None:
            raise typer.BadParameter("This is a saved account. Use ghssh switch NAME; its key and commit details are already saved.")
        switch_named(accounts, key_name, repository=repository, ssh_only=ssh_only, dry_run=dry_run)
        return
    switcher = SwitchManager(manager)
    if back:
        if (
            key_name
            or create
            or email
            or no_passphrase
            or copy
            or git_name is not None
            or git_email is not None
            or repository is not None
            or ssh_only
        ):
            raise typer.BadParameter("Use --back without a key name or key-creation options.")
        session, config, _, previous_key = switcher.plan_back()
        table(
            "Dry run: return" if dry_run else "Return to previous GitHub account",
            [
                ("Current key", str(session.active_key)),
                ("Return to", str(previous_key or "original default SSH configuration")),
                ("Configuration", "Restore the saved github.com section; preserve other hosts"),
                ("Key files / SSH agent", "Retain both keys; agent unchanged"),
                ("Safety backup", f"Create under {manager.layout.backups}"),
                ("Return point", f"Clear {manager.layout.switch_state} after successful return"),
                (
                    "Commit identity",
                    f"Restore previous user.name/user.email in {session.git_identity.repository}"
                    if session.git_identity
                    else "unchanged (saved switch was SSH-only)",
                ),
            ],
        )
        if dry_run:
            return
        confirm("Return to the previous GitHub SSH setup?", yes)
        change = switcher.back(expected_config=config.text, expected_session=session)
        summary(change, "Previous GitHub SSH configuration restored")
        console.print("Returned to your previous SSH setup. Both key files were retained.")
        if session.git_identity:
            console.print(
                f"Previous repository commit settings restored in {session.git_identity.repository}."
            )
        return
    if not key_name:
        raise typer.BadParameter("Choose a saved name: ghssh switch arsam. See ghssh accounts; add one with ghssh add NAME.")
    # Old key-basename commands remain available for existing scripts and return points.
    if not manager.ssh.key_path(key_name).exists() and not create:
        raise GhsshError(f"No saved account named '{key_name}'. Run ghssh accounts or ghssh add {key_name}.")
    if not create and (email or no_passphrase or copy):
        raise typer.BadParameter("--email, --no-passphrase and --copy require --create.")
    if any(char in email for char in ("\n", "\r", "\x00")):
        raise GhsshError("Email/comment must be one line.")
    if ssh_only:
        if git_name is not None or git_email is not None or repository is not None:
            raise typer.BadParameter(
                "--ssh-only cannot be combined with --git-name, --git-email or --repo."
            )
    else:
        manager.git.discover(repository or Path.cwd())
        if git_name is None or git_email is None:
            if yes or dry_run:
                raise GhsshError(
                    "Supply --git-name and --git-email for --yes/--dry-run, or explicitly choose --ssh-only."
                )
            console.print(
                "GitHub matches commit authors using their email. Enter the identity for the OTHER account."
            )
            git_name = git_name if git_name is not None else typer.prompt("Commit name")
            git_email = (
                git_email
                if git_email is not None
                else typer.prompt("Verified GitHub commit email (or noreply address)")
            )
    plan = switcher.plan(
        key_name, create=create, git_name=git_name, git_email=git_email, repository=repository
    )
    table(
        "Dry run: temporary switch" if dry_run else "Temporary GitHub account switch",
        [
            ("Previous key", str(plan.old_key or "original default SSH configuration")),
            ("Alternate key", str(plan.key)),
            (
                "Key files",
                f"Generate {plan.key} ({permission_label(0o600)}), {plan.key}.pub ({permission_label(0o644)})"
                if create
                else "Use existing pair; contents and permissions unchanged",
            ),
            ("Fingerprint", plan.fingerprint or "calculated after key generation"),
            (
                "Configuration",
                "Select alternate key for github.com with IdentitiesOnly yes and AddKeysToAgent no",
            ),
            (
                "Return point",
                f"Save previous GitHub section in {manager.layout.switch_state} ({permission_label(0o600)})",
            ),
            ("Safety backup", f"Create under {manager.layout.backups}"),
            ("SSH agent", "unchanged; SSH may prompt for the alternate key's passphrase"),
            (
                "Commit repository",
                str(plan.git_identity.identity.repository)
                if plan.git_identity
                else "unchanged (--ssh-only)",
            ),
            (
                "Commit identity",
                f"{git_name} <{git_email}>; save previous local values for --back"
                if plan.git_identity
                else "unchanged (--ssh-only)",
            ),
            ("Global Git identity", "unchanged"),
            (
                "GitHub",
                "Register a newly generated public key on your SECOND account"
                if create
                else "unchanged; the key must already belong to your other account",
            ),
        ],
    )
    if dry_run:
        return
    if create and not no_passphrase and not sys.stdin.isatty():
        raise GhsshError("Passphrase entry requires a terminal or explicit --no-passphrase.")
    confirm("Temporarily switch github.com to this identity?", yes)
    change = switcher.start(
        key_name,
        create=create,
        comment=email,
        no_passphrase=no_passphrase,
        expected=plan,
        git_name=git_name,
        git_email=git_email,
        repository=repository,
    )
    summary(change, "Alternate key selected; previous configuration saved")
    if create:
        publish(manager, plan.key, False, copy, interactive_test=True)
        console.print("Add that public key while signed into your SECOND GitHub account.")
    console.print(
        "Test interactively: ssh -T git@github.com (check the username in GitHub's reply)."
    )
    console.print("Return whenever ready: ghssh switch --back")
    if plan.git_identity:
        console.print(
            f"Future commits in {plan.git_identity.identity.repository}: {git_name} <{git_email}>."
        )
        console.print(
            "Existing commits are unchanged. The email must belong to your other GitHub account."
        )
    else:
        console.print("Commit author settings were not changed (--ssh-only).")
    console.print("This selects Git SSH authentication; browser and gh CLI logins are separate.")


def switch_named(
    accounts: AccountManager, name: str, *, repository: Path | None,
    ssh_only: bool, dry_run: bool,
) -> None:
    plan = accounts.plan_switch(name, repository=repository, ssh_only=ssh_only)
    table(
        "Dry run: switch account" if dry_run else "Switch GitHub account",
        [
            ("Account", plan.name),
            ("SSH key", str(accounts.manager.ssh.key_path(plan.account.key))),
            ("Commit identity", f"{plan.account.git_name} <{plan.account.git_email}>" if plan.git else "unchanged (--ssh-only)"),
            ("Git project", str(plan.git.identity.repository) if plan.git else "none (--ssh-only)"),
            ("SSH scope", "github.com connections from all projects using this SSH config"),
            ("Safety backup", str(accounts.layout.backups)),
        ],
    )
    if dry_run:
        return
    change = accounts.switch(name, repository=repository, ssh_only=ssh_only, expected=plan)
    console.print(f"Switched to '{plan.name}'.")
    if plan.git:
        console.print(f"Future commits in {plan.git.identity.repository}: {plan.account.git_name} <{plan.account.git_email}>.")
    console.print(f"Safety backup: {change.backup}")
    console.print("Check the GitHub username: ssh -T git@github.com")
    console.print("Choose another saved account anytime: ghssh switch NAME (see ghssh accounts).")


@app.command(rich_help_panel="Accounts")
@guarded
def accounts() -> None:
    """Show saved account nicknames, SSH keys and commit details. Marks the selected SSH key."""
    manager = Manager()
    saved = AccountManager(manager).read()
    if not saved:
        console.print("No saved accounts yet. Start with: ghssh add arsam")
        console.print("Reuse your existing key during setup, then add your other accounts the same way.")
        return
    config = manager.config.read()
    config.validate_edit()
    current = config.identity(manager.layout)
    result = Table(title="Saved GitHub accounts")
    for column in ("Name", "SSH selection", "Key", "Commit name", "Commit email"):
        result.add_column(column)
    for name, account in sorted(saved.items()):
        result.add_row(*map(Text, (
            name, "selected" if manager.ssh.key_path(account.key) == current else "",
            account.key, account.git_name, account.git_email,
        )))
    console.print(result)
    console.print("From your Git project: ghssh switch NAME")
    console.print("The selection column shows SSH only. Check this project's commit settings with ghssh status.")


@app.command(rich_help_panel="Accounts")
@guarded
def forget(
    name: Annotated[str, typer.Argument(metavar="NAME", help="Saved nickname to forget.")],
    yes: Yes = False, dry_run: DryRun = False,
) -> None:
    """Forget a saved nickname. Keeps its SSH keys, current SSH/Git settings and backups.

    To correct saved commit details: ghssh forget NAME, then ghssh add NAME --key
    EXISTING_KEY and enter the corrected name/email. No key recreation is needed.
    """
    manager = Manager()
    profiles = AccountManager(manager)
    name = account_name(name)
    saved = profiles.read()
    if name not in saved:
        raise GhsshError(f"No saved account named '{name}'. Run ghssh accounts.")
    console.print(f"Forget '{name}'; keep its SSH key, active SSH/Git settings and backups.")
    if dry_run:
        return
    confirm("Forget this nickname?", yes)
    profiles.forget(name, expected=saved[name])
    console.print(f"Forgot '{name}'. Key files were retained.")


@app.command(rich_help_panel="Key maintenance and recovery")
@guarded
def remove(
    config_only: Annotated[
        bool, typer.Option(help="Remove GitHub configuration and retain key files.")
    ] = False,
    delete_key: Annotated[
        bool, typer.Option(help="Also delete a fingerprint-verified ghssh-owned key pair.")
    ] = False,
    no_backup: Annotated[
        bool, typer.Option("--no-backup", help="Explicitly skip the pre-removal backup.")
    ] = False,
    github: GitHub = False,
    yes: Yes = False,
    dry_run: DryRun = False,
) -> None:
    """Remove the GitHub Host block; key file deletion requires an explicit choice."""
    if config_only and delete_key:
        raise typer.BadParameter("Choose either --config-only or --delete-key.")
    manager = Manager()
    config, key = manager.inspect()
    if not config.target():
        raise GhsshError("No explicit GitHub Host block exists; nothing to remove.")
    if not config_only and not delete_key and not yes and not dry_run:
        delete_key = typer.confirm(
            "Also delete ghssh-owned key files? (No keeps files)", default=False
        )
    if delete_key:
        if not key:
            raise GhsshError("No GitHub identity is configured; use --config-only.")
        manager.can_delete(config, key)
    remote = []
    account = None
    if github:
        account = manager.github.require_account()
        if not key:
            raise GhsshError("No configured key to fingerprint; remote deletion refused.")
        fingerprint = manager.ssh.identity_fingerprint(key)
        remote = manager.github.matching(fingerprint, manager.ssh)
        for match in remote:
            console.print(
                f"Remote GitHub account {account}: key ID {match.id}, title {match.title!r}, {match.fingerprint}"
            )
        if not remote:
            console.print(
                "No remote GitHub key matches this fingerprint; remote keys will be retained."
            )
    table(
        "Dry run: planned removal" if dry_run else "Planned removal",
        [
            (
                "Configuration",
                f"Remove only the explicit github.com Host block in {manager.layout.config}",
            ),
            ("Key files", f"Delete {key} and {key}.pub" if delete_key else "retain all key files"),
            ("SSH agent", "Unload current identity if loaded and not referenced by another host"),
            (
                "Backup",
                "skipped (--no-backup)"
                if no_backup
                else f"Create beneath {manager.layout.backups}",
            ),
            (
                "GitHub",
                f"Delete {len(remote)} fingerprint-matched key(s) from {account}"
                if github
                else "unchanged",
            ),
        ],
    )
    if dry_run:
        return
    confirm("Apply this local removal?", yes)
    # --yes deliberately does not bypass the separate remote deletion confirmation.
    if remote and not typer.confirm(
        "Delete the remote account keys shown above as well?", default=False
    ):
        raise typer.Abort()
    change = manager.remove(
        delete_files=delete_key, no_backup=no_backup, expected_config=config.text
    )
    summary(change)
    for match in remote:
        try:
            manager.github.delete(match, manager.ssh, account=account)
            console.print(f"Deleted GitHub authentication key ID {match.id} from {account}.")
        except GhsshError as exc:
            raise GhsshError(
                f"Local removal completed; remote deletion failed. Backup: {change.backup}. {exc}"
            ) from exc


@app.command("list", rich_help_panel="Inspect and check")
@guarded
def list_keys() -> None:
    """List detected key pairs, their fingerprints, configuration roles and agent state."""
    manager = Manager()
    config = manager.config.read()
    try:
        config.validate_edit()
        current = config.identity(manager.layout)
    except GhsshError as exc:
        console.print(f"GitHub configuration needs attention: {exc}")
        current = None
    running, loaded = manager.ssh.agent_fingerprints()
    result = Table(title="SSH keys")
    for column in ("Key", "Public", "Role", "Fingerprint", "Agent"):
        result.add_column(column)
    for key in manager.ssh.discover():
        path = key.private or Path(str(key.public)[:-4])
        role = (
            "GitHub"
            if current == path
            else (
                "Other SSH host"
                if config.references_elsewhere(path, manager.layout, conservative=False)
                else "Unknown / unconfigured"
            )
        )
        result.add_row(
            *[
                Text(value)
                for value in (
                    str(key.private or key.public),
                    "yes" if key.public else "missing",
                    role,
                    key.fingerprint or "unknown",
                    "loaded" if key.fingerprint in loaded else "no",
                )
            ]
        )
    console.print(result)
    console.print("Agent: " + ("running" if running else "unavailable"))
    console.print("Loaded identities: " + (", ".join(sorted(loaded)) or "none"))


@app.command(rich_help_panel="Inspect and check")
@guarded
def status(
    repository: Annotated[
        Path | None, typer.Option("--repo", help="Repository whose commit identity to inspect.")
    ] = None,
) -> None:
    """Inspect local configuration, ssh-agent and GitHub CLI authentication."""
    manager = Manager()
    manager.layout.checked(manager.layout.config)
    config = manager.config.read()
    key = None
    issue = None
    try:
        config.validate_edit()
        key = config.identity(manager.layout)
    except GhsshError as exc:
        issue = str(exc)
    running, loaded = manager.ssh.agent_fingerprints()
    public = Path(str(key) + ".pub") if key else None
    fingerprint = None
    if key and (key.exists() or public.exists()):
        try:
            fingerprint = manager.ssh.identity_fingerprint(key)
        except GhsshError as exc:
            issue = str(exc)
    installed = manager.github.installed()
    try:
        authenticated = manager.github.authenticated() if installed else False
    except GhsshError as exc:
        authenticated = False
        console.print(f"GitHub authentication could not be checked: {exc}")
    saved = AccountManager(manager).read()
    selected_names = [name for name, account in saved.items() if manager.ssh.key_path(account.key) == key]
    if any(saved[name].fingerprint != fingerprint for name in selected_names):
        issue = "Selected key no longer matches its saved account fingerprint"
    table(
        "GitHub SSH Status",
        [
            ("Saved account", ", ".join(selected_names) or "none selected; see ghssh accounts"),
            (
                "SSH directory",
                f"{'✓' if manager.layout.ssh.is_dir() else '✗'} {manager.layout.ssh}",
            ),
            ("SSH agent", "✓ Running" if running else "✗ Unavailable"),
            (
                "GitHub config",
                "Needs attention: " + issue
                if issue
                else ("✓ Configured" if config.target() else "✗ Missing"),
            ),
            ("Identity", str(key or "none")),
            ("Key exists", "✓" if key and key.is_file() else "✗"),
            ("Public key exists", "✓" if public and public.is_file() else "✗"),
            ("Fingerprint", fingerprint or "unknown"),
            ("Loaded in agent", "✓" if fingerprint and fingerprint in loaded else "✗"),
            ("GitHub CLI", "✓ Installed" if installed else "✗ Not installed"),
            ("GitHub authentication", "✓ Logged in" if authenticated else "✗ Not authenticated"),
        ],
    )
    if manager.layout.checked(manager.layout.switch_state).exists():
        console.print("Temporary account switch active. Return with: ghssh switch --back")
    try:
        session = SwitchManager(manager).read()
        selected = repository or (
            session.git_identity.repository if session and session.git_identity else Path.cwd()
        )
        root, _ = manager.git.discover(selected)
        table(
            "Git commit identity",
            [
                ("Repository", str(root)),
                ("Commit name", manager.git.effective(root, "user.name") or "not configured"),
                ("Commit email", manager.git.effective(root, "user.email") or "not configured"),
                (
                    "Saved return point",
                    "SSH + commit identity"
                    if session and session.git_identity
                    else "SSH-only"
                    if session
                    else "none",
                ),
            ],
        )
    except GhsshError as exc:
        console.print(f"Commit identity: {exc}")


@app.command("test", rich_help_panel="Inspect and check")
@guarded
def test_connection() -> None:
    """Test GitHub authentication; its successful no-shell response usually exits 1."""
    success, output = Manager().ssh.test_github()
    if output:
        console.print(output)
    if success:
        console.print("✓ GitHub SSH authentication succeeded.")
    else:
        console.print(
            "✗ GitHub SSH authentication failed. Check network access, your public key registration, and trusted GitHub host keys."
        )
        raise typer.Exit(1)


@app.command(rich_help_panel="Key maintenance and recovery")
@guarded
def backup(dry_run: DryRun = False) -> None:
    """Snapshot the current GitHub key pair, config, and metadata in a private directory."""
    from .utils import locked

    manager = Manager()
    config, key = manager.inspect()
    if dry_run:
        table(
            "Dry run: backup",
            [
                ("Directory", str(manager.layout.backups) + f"/<UTC-timestamp>_<id> ({permission_label(0o700)})"),
                (
                    "Contents",
                    f"Key pair for {key or 'none'}, GitHub section, config snapshot, metadata ({permission_label(0o600)})",
                ),
            ],
        )
        return
    with locked(manager.layout):
        if manager.config.read().text != config.text:
            raise GhsshError("SSH config changed; run backup again.")
        directory = manager.backups.create(config)
    console.print(f"Backup created: {directory}")


@app.command(rich_help_panel="Key maintenance and recovery")
@guarded
def restore(
    backup_id: Annotated[
        str | None, typer.Argument(help="Backup ID (not a filesystem path).")
    ] = None,
    list_only: Annotated[
        bool, typer.Option("--list", help="List existing backup IDs without changes.")
    ] = False,
    yes: Yes = False,
    dry_run: DryRun = False,
) -> None:
    """Restore one validated GitHub backup, preserving current unrelated Host blocks."""
    manager = Manager()
    available = manager.backups.list()
    if not available:
        console.print("No ghssh backups found.")
        if backup_id:
            raise typer.Exit(1)
        return
    if list_only or backup_id is None:
        for path in available:
            console.print(path.name)
        if list_only or dry_run:
            return
        if yes:
            raise GhsshError("Specify a backup ID; --yes never selects a backup for you.")
        backup_id = typer.prompt("Backup ID to restore")
    source = manager.backups.validate(manager.backups.select(backup_id))
    config, current = manager.inspect()
    if source.identity and config.references_elsewhere(source.identity, manager.layout):
        raise GhsshError(
            "Restore target is referenced by another SSH host; restore manually to a new path."
        )
    section = source.files["github.conf"].decode()
    if section:
        config.restore_section(section)
    else:
        config.remove()
    table(
        "Dry run: restore" if dry_run else "Planned restore",
        [
            ("Source", str(source.directory)),
            (
                "Current GitHub key",
                f"{current or 'none'} (files retained unless this is the restore target)",
            ),
            (
                "Restore target",
                str(
                    source.identity
                    or (
                        "no key files; restore GitHub section"
                        if section
                        else "no key; remove GitHub configuration"
                    )
                ),
            ),
            (
                "Overwrite",
                "Existing target key files will be backed up, then replaced with the selected backup",
            ),
            ("Safety backup", str(manager.layout.backups) + "/<new UTC-timestamp>_<id>"),
            (
                "Configuration",
                f"Restore only github.com section; config {permission_label(0o600)}, private {permission_label(0o600)}, public {permission_label(0o644)}",
            ),
            ("Agent / GitHub", "unchanged; register the restored public key if needed"),
        ],
    )
    if dry_run:
        return
    confirm("Restore this backup, including any displayed target file overwrites?", yes)
    change = manager.restore(backup_id, expected_config=config.text, expected_backup=source)
    summary(
        change, "github.com Host block restored" if section else "github.com Host block removed"
    )
    if change.new_key:
        console.print(
            "Load the restored identity when ready: " + command_text(["ssh-add", str(change.new_key)])
        )
    console.print(
        "Agent and remote GitHub keys were retained. Run ghssh test after checking registration."
    )
