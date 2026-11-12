import ctypes
import sys

from pathlib import Path

from linktool.paths import verbatim

FILE_WRITE_ATTRIBUTES = 0x0100
FILE_SHARE_ALL = 0x0001 | 0x0002 | 0x0004
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_BASIC_INFO_CLASS = 0
FILETIME_EPOCH = 116_444_736_000_000_000
INVALID_HANDLE = ctypes.c_void_p(-1).value


class BasicInfo(ctypes.Structure):
    '''FILE_BASIC_INFO: a zero field is left unchanged by SetFileInformationByHandle.'''

    _fields_ = [
        ('creation', ctypes.c_int64),
        ('access', ctypes.c_int64),
        ('write', ctypes.c_int64),
        ('change', ctypes.c_int64),
        ('attributes', ctypes.c_uint32),
    ]


if sys.platform == 'win32':
    _KERNEL32 = ctypes.CDLL('kernel32', use_last_error=True)
    _KERNEL32.CreateFileW.restype = ctypes.c_void_p
    _KERNEL32.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    _KERNEL32.SetFileInformationByHandle.restype = ctypes.c_int
    _KERNEL32.SetFileInformationByHandle.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    _KERNEL32.CloseHandle.restype = ctypes.c_int
    _KERNEL32.CloseHandle.argtypes = [ctypes.c_void_p]


def stamp(path: Path, *, mtime_ns: int, birth_ns: int | None, follow: bool) -> None:
    '''Set a path's creation and modification time through a handle on the path itself.

    os.utime cannot reach a link on Windows, and no other route writes a creation time,
    so this opens the reparse point directly and hands the times to the kernel. A zero
    creation time leaves whatever the path already has.
    '''
    handle = _open(path, follow=follow)
    try:
        info = BasicInfo()
        info.creation = _filetime(birth_ns) if birth_ns is not None else 0
        info.access = _filetime(mtime_ns)
        info.write = _filetime(mtime_ns)
        applied = _KERNEL32.SetFileInformationByHandle(
            handle, FILE_BASIC_INFO_CLASS, ctypes.byref(info), ctypes.sizeof(info)
        )
        if not applied:
            raise _error()
    finally:
        _KERNEL32.CloseHandle(handle)


def _open(path: Path, *, follow: bool) -> int:
    flags = FILE_FLAG_BACKUP_SEMANTICS
    if not follow:
        flags |= FILE_FLAG_OPEN_REPARSE_POINT
    handle = _KERNEL32.CreateFileW(
        verbatim(path),
        FILE_WRITE_ATTRIBUTES,
        FILE_SHARE_ALL,
        None,
        OPEN_EXISTING,
        flags,
        None,
    )
    if handle is None or handle == INVALID_HANDLE:
        raise _error()
    return handle


def _filetime(ns: int) -> int:
    return ns // 100 + FILETIME_EPOCH


def _error() -> OSError:
    code = ctypes.get_last_error()
    return OSError(code, f'Win32 错误码 {code}')
