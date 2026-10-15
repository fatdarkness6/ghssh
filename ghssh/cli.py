"""Typer commands and human confirmations for ghssh."""

from __future__ import annotations

import shlex
import shutil
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
from .manager import Change, Manager
from .switch_manager import SwitchManager
from .utils import GhsshError

app = typer.Typer(
    no_args_is_help=True,
    invoke_without_command=True,
    help="Safely manage GitHub SSH identities on Linux.",
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
    version: Annotated[bool, typer.Option("--version", is_eager=True)] = False,
) -> None:
    """Manage GitHub SSH keys without disturbing unrelated SSH identities."""
    global VERBOSE
    VERBOSE = verbose
    if version:
        console.print(f"ghssh {__version__}")
        raise typer.Exit()
    if sys.platform != "linux":
        raise typer.BadParameter("This release supports Linux only.")


def confirm(message: str, yes: bool) -> None:
    if not yes and not typer.confirm(message, default=False):
        raise typer.Abort()


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
                + shlex.join(["gh", "ssh-key", "add", str(public), "--type", "authentication"])
            )
            raise GhsshError(str(exc)) from exc
        console.print("Public key uploaded to GitHub. Run ghssh test to check authentication.")
    else:
        console.print("GitHub still needs this PUBLIC key: https://github.com/settings/ssh/new")
        check = "ssh -T git@github.com" if interactive_test else "ghssh test"
        console.print(f"Choose New SSH key → Authentication Key, paste it, then run {check}.")


def copy_public(text: str) -> None:
    from .utils import Runner

    candidates = [
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
            ("SSH directory", f"Create if absent; set {manager.layout.ssh} to 700"),
            ("Old key", f"{current} (retained)" if current else "none"),
            (
                "Backup",
                f"New UTC timestamped directory under {manager.layout.backups}"
                if config.target()
                else "no existing GitHub block",
            ),
            ("Generate", f"Ed25519 pair: {key} (600), {key}.pub (644)"),
            (
                "Passphrase",
                "empty (explicit --no-passphrase)"
                if no_passphrase
                else "ssh-keygen will prompt securely",
            ),
            ("SSH agent", f"Use existing agent or start one, then ssh-add {key}"),
            (
                "Configuration",
                f"{manager.layout.config} (600): GitHub IdentityFile → {key}, User git, IdentitiesOnly yes",
            ),
            ("Ownership record", f"Record new key fingerprint in {manager.layout.state} (600)"),
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


@app.command()
@guarded
def add(
    key_name: Annotated[str | None, typer.Option(help="New basename inside ~/.ssh.")] = None,
    email: Annotated[
        str, typer.Option("--email", "--comment", help="Optional public key comment.")
    ] = "",
    github: GitHub = False,
    no_passphrase: Annotated[
        bool, typer.Option(help="Explicitly generate an unencrypted private key.")
    ] = False,
    copy: Annotated[
        bool, typer.Option("--copy", help="Copy the public key to the clipboard.")
    ] = False,
    yes: Yes = False,
    dry_run: DryRun = False,
) -> None:
    """Generate, load and configure a new GitHub-specific Ed25519 key."""
    create_key(
        replace=False,
        key_name=key_name,
        email=email,
        github=github,
        no_passphrase=no_passphrase,
        copy=copy,
        yes=yes,
        dry_run=dry_run,
    )


@app.command()
@guarded
def replace(
    key_name: Annotated[
        str | None, typer.Option(help="New basename; never the old key's name.")
    ] = None,
    email: Annotated[str, typer.Option("--email", "--comment")] = "",
    github: GitHub = False,
    no_passphrase: Annotated[
        bool, typer.Option(help="Explicitly generate an unencrypted private key.")
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


@app.command("switch")
@guarded
def switch_account(
    key_name: Annotated[
        str | None, typer.Argument(help="Alternate private-key basename inside ~/.ssh.")
    ] = None,
    back: Annotated[
        bool, typer.Option("--back", help="Return to the SSH configuration saved before switching.")
    ] = False,
    create: Annotated[
        bool, typer.Option("--create", help="Generate a new key for the other account first.")
    ] = False,
    email: Annotated[str, typer.Option("--email", "--comment")] = "",
    no_passphrase: Annotated[
        bool, typer.Option(help="Explicitly generate an unencrypted private key.")
    ] = False,
    copy: Annotated[
        bool, typer.Option("--copy", help="Copy the newly created public key.")
    ] = False,
    yes: Yes = False,
    dry_run: DryRun = False,
) -> None:
    """Temporarily select another account's key; --back returns to the previous setup."""
    manager = Manager()
    switcher = SwitchManager(manager)
    if back:
        if key_name or create or email or no_passphrase or copy:
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
            ],
        )
        if dry_run:
            return
        confirm("Return to the previous GitHub SSH setup?", yes)
        change = switcher.back(expected_config=config.text, expected_session=session)
        summary(change, "Previous GitHub SSH configuration restored")
        console.print("Returned to your previous SSH setup. Both key files were retained.")
        return
    if not key_name:
        raise typer.BadParameter("Provide a key name, or use ghssh switch --back.")
    if not create and (email or no_passphrase or copy):
        raise typer.BadParameter("--email, --no-passphrase and --copy require --create.")
    if any(char in email for char in ("\n", "\r", "\x00")):
        raise GhsshError("Email/comment must be one line.")
    plan = switcher.plan(key_name, create=create)
    table(
        "Dry run: temporary switch" if dry_run else "Temporary GitHub account switch",
        [
            ("Previous key", str(plan.old_key or "original default SSH configuration")),
            ("Alternate key", str(plan.key)),
            (
                "Key files",
                f"Generate {plan.key} (600), {plan.key}.pub (644)"
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
                f"Save previous GitHub section in {manager.layout.switch_state} (600)",
            ),
            ("Safety backup", f"Create under {manager.layout.backups}"),
            ("SSH agent", "unchanged; SSH may prompt for the alternate key's passphrase"),
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
        key_name, create=create, comment=email, no_passphrase=no_passphrase, expected=plan
    )
    summary(change, "Alternate key selected; previous configuration saved")
    if create:
        publish(manager, plan.key, False, copy, interactive_test=True)
        console.print("Add that public key while signed into your SECOND GitHub account.")
    console.print(
        "Test interactively: ssh -T git@github.com (check the username in GitHub's reply)."
    )
    console.print("Return whenever ready: ghssh switch --back")
    console.print("This selects Git SSH authentication; browser and gh CLI logins are separate.")


@app.command()
@guarded
def remove(
    config_only: Annotated[
        bool, typer.Option(help="Remove GitHub configuration and retain key files.")
    ] = False,
    delete_key: Annotated[
        bool, typer.Option(help="Also delete a fingerprint-verified ghssh-owned key pair.")
    ] = False,
    no_backup: Annotated[
        bool, typer.Option(help="Explicitly skip the pre-removal backup.")
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


@app.command("list")
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


@app.command()
@guarded
def status() -> None:
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
    table(
        "GitHub SSH Status",
        [
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


@app.command("test")
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


@app.command()
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
                ("Directory", str(manager.layout.backups) + "/<UTC-timestamp>_<id> (700)"),
                (
                    "Contents",
                    f"Key pair for {key or 'none'}, GitHub section, config snapshot, metadata (600)",
                ),
            ],
        )
        return
    with locked(manager.layout):
        if manager.config.read().text != config.text:
            raise GhsshError("SSH config changed; run backup again.")
        directory = manager.backups.create(config)
    console.print(f"Backup created: {directory}")


@app.command()
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
                "Restore only github.com section; config mode 600, private 600, public 644",
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
            "Load the restored identity when ready: " + shlex.join(["ssh-add", str(change.new_key)])
        )
    console.print(
        "Agent and remote GitHub keys were retained. Run ghssh test after checking registration."
    )
