"""Native Windows handles and protected ACLs (imported only on Windows)."""

from __future__ import annotations

import msvcrt
import os
from functools import wraps
from pathlib import Path

import ntsecuritycon
import pywintypes
import win32api
import win32con
import win32file
import win32security


def checked_api(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except pywintypes.error as exc:
            if exc.winerror in (80, 183):
                raise FileExistsError(str(exc)) from exc
            if exc.winerror in (2, 3):
                raise FileNotFoundError(str(exc)) from exc
            raise OSError(f"Windows security operation failed: {exc}") from exc

    return call


def user_sid():
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        return win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()


def caller_owns(sid) -> bool:
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        # Elevated Windows tokens may create files owned by their default owner
        # group. Accept only this token's owner, never arbitrary administrator SIDs.
        return sid in (
            win32security.GetTokenInformation(token, win32security.TokenUser)[0],
            win32security.GetTokenInformation(token, win32security.TokenOwner),
        )
    finally:
        token.Close()


@checked_api
def owned(path: Path) -> bool:
    info = win32security.GetNamedSecurityInfo(
        str(path), win32security.SE_FILE_OBJECT, win32security.OWNER_SECURITY_INFORMATION
    )
    return caller_owns(info.GetSecurityDescriptorOwner())


@checked_api
def fd_owned(fd: int) -> bool:
    info = win32security.GetSecurityInfo(
        msvcrt.get_osfhandle(fd),
        win32security.SE_FILE_OBJECT,
        win32security.OWNER_SECURITY_INFORMATION,
    )
    return caller_owns(info.GetSecurityDescriptorOwner())


@checked_api
def permissions(path: Path) -> str:
    flags = win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION
    descriptor = win32security.GetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT, flags)
    return win32security.ConvertSecurityDescriptorToStringSecurityDescriptor(
        descriptor, win32security.SDDL_REVISION_1, flags
    )


@checked_api
def restore_permissions(path: Path, saved: str) -> None:
    descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        saved, win32security.SDDL_REVISION_1
    )
    protection = (
        win32security.PROTECTED_DACL_SECURITY_INFORMATION
        if descriptor.GetSecurityDescriptorControl()[0] & win32security.SE_DACL_PROTECTED
        else win32security.UNPROTECTED_DACL_SECURITY_INFORMATION
    )
    win32security.SetNamedSecurityInfo(
        str(path), win32security.SE_FILE_OBJECT,
        win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION | protection,
        descriptor.GetSecurityDescriptorOwner(), None, descriptor.GetSecurityDescriptorDacl(), None,
    )


@checked_api
def secure(path: Path, *, directory: bool, fd: int | None = None) -> None:
    # No inherited grants to Users/Everyone: chmod on Windows cannot protect a key.
    if not (fd_owned(fd) if fd is not None else owned(path)):
        raise OSError(f"Refusing an unowned path: {path}")
    dacl = win32security.ACL()
    inheritance = win32con.OBJECT_INHERIT_ACE | win32con.CONTAINER_INHERIT_ACE if directory else 0
    dacl.AddAccessAllowedAceEx(win32security.ACL_REVISION, inheritance, ntsecuritycon.FILE_ALL_ACCESS, user_sid())
    flags = (
        win32security.OWNER_SECURITY_INFORMATION
        | win32security.DACL_SECURITY_INFORMATION
        | win32security.PROTECTED_DACL_SECURITY_INFORMATION
    )
    # CRT-created temporary handles do not carry WRITE_DAC. Check their identity
    # before setting permissions through the name while the descriptor stays open.
    if fd is not None and os.fstat(fd).st_ino != path.stat().st_ino:
        raise OSError(f"File changed before permissions were set: {path}")
    win32security.SetNamedSecurityInfo(
        str(path), win32security.SE_FILE_OBJECT, flags, user_sid(), None, dacl, None
    )


@checked_api
def open_checked(path: Path, flags: int) -> int:
    access = win32con.GENERIC_READ | win32con.READ_CONTROL
    if flags & (os.O_WRONLY | os.O_RDWR):
        access |= win32con.GENERIC_WRITE | win32con.WRITE_DAC
    creation = win32con.OPEN_ALWAYS if flags & os.O_CREAT else win32con.OPEN_EXISTING
    if flags & os.O_EXCL:
        creation = win32con.CREATE_NEW
    handle = win32file.CreateFile(
        str(path),
        access,
        win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE,
        None,
        creation,
        win32file.FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    try:
        info = win32file.GetFileInformationByHandle(handle)
        if info[0] & (win32con.FILE_ATTRIBUTE_REPARSE_POINT | win32con.FILE_ATTRIBUTE_DIRECTORY):
            raise OSError(f"Refusing a symlink, reparse point or special file: {path}")
        if info[7] != 1:
            raise OSError(f"Refusing a hard-linked file: {path}")
        owner = win32security.GetSecurityInfo(
            handle, win32security.SE_FILE_OBJECT, win32security.OWNER_SECURITY_INFORMATION
        ).GetSecurityDescriptorOwner()
        if not caller_owns(owner):
            raise OSError(f"Refusing an unowned file: {path}")
        # open_osfhandle takes ownership of the native handle after a successful call.
        fd = msvcrt.open_osfhandle(int(handle), (flags & (os.O_WRONLY | os.O_RDWR)) | os.O_BINARY)
        handle.Detach()
        handle = None
        return fd
    finally:
        if handle is not None:
            handle.Close()


def lock_file(fd: int) -> None:
    # Windows byte locks may extend beyond EOF; the file may remain empty.
    os.lseek(fd, 0, os.SEEK_SET)
    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
