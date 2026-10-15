import pytest

from ghssh.config_manager import SSHConfig
from ghssh.utils import GhsshError, Layout

UNRELATED = "# GitLab comment\nHost gitlab.com\n\tUser git\n\tIdentityFile ~/.ssh/gitlab_key\n"
GITHUB = "Host github.com\n    HostName github.com\n    User git\n    IdentityFile ~/.ssh/github_old\n    IdentitiesOnly yes\n"


def test_parse_explicit_identity(tmp_path):
    config = SSHConfig("# Header\n" + GITHUB + "\n" + UNRELATED)
    assert config.identity(Layout(tmp_path)) == tmp_path / ".ssh/github_old"
    assert config.section() == GITHUB


def test_insert_preserves_unrelated_bytes():
    result = SSHConfig(UNRELATED).upsert("~/.ssh/github_new")
    assert UNRELATED in result
    assert result.index("Host github.com") < result.index("Host gitlab.com")
    assert result.count("Host github.com") == 1


def test_insertion_keeps_global_directive_scope():
    result = SSHConfig("# global\nServerAliveInterval 10\n" + UNRELATED).upsert("~/.ssh/github_new")
    assert result.startswith("# global\nServerAliveInterval 10\nHost github.com\n")


def test_replace_preserves_comments_formatting_and_unrelated_settings():
    text = "# start\n" + GITHUB.replace(
        "    IdentityFile ~/.ssh/github_old", "\tIdentityFile ~/.ssh/github_old # identity"
    )
    text += "    Port 22\n    # keep this\n\n" + UNRELATED
    result = SSHConfig(text).upsert("~/.ssh/github_new")
    assert "\tIdentityFile ~/.ssh/github_new  # identity\n" in result
    assert "    Port 22\n    # keep this\n" in result
    assert result.endswith(UNRELATED)
    assert result.startswith("# start\n")


def test_remove_preserves_unrelated_bytes_and_comments():
    result = SSHConfig("# intro\n" + GITHUB + "    # keep this\n" + UNRELATED).remove()
    assert "Host github.com" not in result
    assert "# intro\n" in result and "    # keep this\n" in result
    assert result.endswith(UNRELATED)


def test_case_equals_quotes_and_crlf(tmp_path):
    config = SSHConfig('hOsT=github.com\r\n\tIdentityFile="~/.ssh/a b" # comment\r\n')
    assert config.identity(Layout(tmp_path)) == tmp_path / ".ssh/a b"
    result = config.upsert('"~/.ssh/new key"')
    assert "\r\n" in result and "\n" not in result.replace("\r\n", "")


@pytest.mark.parametrize(
    "text",
    [
        GITHUB + GITHUB,
        "Host github.com gitlab.com\n IdentityFile ~/.ssh/shared\n",
        'Host github.com\n IdentityFile "unclosed\n',
        "IdentityFile ~/.ssh/global\n" + GITHUB,
        "Host *\n IdentityFile ~/.ssh/shared\n" + GITHUB,
        "Include ~/.ssh/conf.d/*\n" + GITHUB,
        GITHUB + "Match host github.com\n User git\n",
        GITHUB + " IdentityAgent /tmp/custom\n",
    ],
)
def test_ambiguous_and_unsupported_config_refused(text):
    with pytest.raises(GhsshError):
        SSHConfig(text).upsert("~/.ssh/new")


def test_multiple_identityfiles_refused(tmp_path):
    with pytest.raises(GhsshError, match="Multiple GitHub"):
        SSHConfig(GITHUB + " IdentityFile ~/.ssh/another\n").identity(Layout(tmp_path))


def test_duplicate_other_managed_directive_refused():
    with pytest.raises(GhsshError, match="Duplicate user"):
        SSHConfig(GITHUB + " User another\n").upsert("~/.ssh/new")


def test_wildcard_negation_allowed():
    config = SSHConfig("Host * !github.com\n IdentityFile ~/.ssh/other\n" + GITHUB)
    assert "IdentityFile ~/.ssh/new" in config.upsert("~/.ssh/new")


def test_shared_reference_detected(tmp_path):
    layout = Layout(tmp_path)
    config = SSHConfig(GITHUB + "Host example.org\n IdentityFile ~/.ssh/github_old\n")
    assert config.references_elsewhere(tmp_path / ".ssh/github_old", layout)


@pytest.mark.parametrize(
    "value",
    [
        "relative/key",
        "~other/key",
        "~/.ssh/../secret",
        "/tmp/foreign",
        "~/.ssh/%h",
        "~/.ssh/${USER}",
    ],
)
def test_unsafe_identity_paths(tmp_path, value):
    with pytest.raises(GhsshError):
        Layout(tmp_path).identity_path(value)


def test_section_restore_preserves_current_other_hosts():
    current = SSHConfig(GITHUB + UNRELATED + "Host new-server\n Port 2222\n")
    source = GITHUB.replace("github_old", "restored")
    result = current.restore_section(source)
    assert "github_old" not in result
    assert result.endswith(UNRELATED + "Host new-server\n Port 2222\n")


def test_empty_missing_newline():
    assert SSHConfig("").upsert("~/.ssh/key").startswith("Host github.com\n")
    result = SSHConfig("ServerAliveInterval 10").upsert("~/.ssh/key")
    assert result.startswith("ServerAliveInterval 10\nHost github.com\n")


def test_openssh_wildcards_do_not_treat_brackets_as_character_classes():
    config = SSHConfig("Host * !github[.]com\n IdentityFile ~/.ssh/shared\n" + GITHUB)
    with pytest.raises(GhsshError, match="wildcard/shared"):
        config.validate_edit()


def test_global_certificate_or_agent_settings_refused():
    for setting in ("CertificateFile ~/.ssh/cert", "IdentityAgent /tmp/socket"):
        with pytest.raises(GhsshError, match="Global identity"):
            SSHConfig(setting + "\n" + GITHUB).validate_edit()
