"""Exercise token lifetime on any OS with a handle that only supports Close()."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def backend(monkeypatch):
    closed = []
    handle = SimpleNamespace(Close=lambda: closed.append(True))
    security = SimpleNamespace(
        OpenProcessToken=lambda *args: handle,
        GetTokenInformation=lambda token, kind: ("user-sid", 0) if kind == 1 else "owner-sid",
        TokenUser=1,
        TokenOwner=4,
    )
    for name, module in {
        "msvcrt": SimpleNamespace(),
        "ntsecuritycon": SimpleNamespace(),
        "pywintypes": SimpleNamespace(error=OSError),
        "win32api": SimpleNamespace(GetCurrentProcess=lambda: -1),
        "win32con": SimpleNamespace(TOKEN_QUERY=8),
        "win32file": SimpleNamespace(),
        "win32security": security,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    path = Path(__file__).parents[1] / "ghssh/_windows.py"
    spec = importlib.util.spec_from_file_location("windows_handle_regression", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, security, closed


def test_user_sid_closes_plain_handle(backend):
    module, _, closed = backend
    assert module.user_sid() == "user-sid"
    assert closed == [True]


@pytest.mark.parametrize("sid,expected", [("user-sid", True), ("owner-sid", True), ("other", False)])
def test_owner_check_closes_plain_handle(backend, sid, expected):
    module, _, closed = backend
    assert module.caller_owns(sid) is expected
    assert closed == [True]


@pytest.mark.parametrize("function,args", [("user_sid", ()), ("caller_owns", ("user-sid",))])
def test_handle_closed_when_token_query_fails(backend, function, args):
    module, security, closed = backend

    def fail(*args):
        raise RuntimeError("token query failed")

    security.GetTokenInformation = fail
    with pytest.raises(RuntimeError, match="token query failed"):
        getattr(module, function)(*args)
    assert closed == [True]
