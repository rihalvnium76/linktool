import contextlib
import os
import shutil
import sys

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from linktool import log, winstamp
from linktool.paths import read_link

# Windows 把「链接自身指向目录」记在链接的属性位上，目标是否还在都不影响这一位。
FILE_ATTRIBUTE_DIRECTORY = 0x10


class Kind(StrEnum):
    FILE = 'file'
    SYMLINK = 'symlink'
    JUNCTION = 'junction'


class Built(StrEnum):
    HARDLINK = 'hardlink'
    SYMLINK = 'symlink'
    COPY = 'copy'


@dataclass(frozen=True, slots=True)
class Capabilities:
    hardlink: bool
    symlink: bool


def path_kind(path: Path) -> Kind:
    if os.path.isjunction(path):
        return Kind.JUNCTION
    if path.is_symlink():
        return Kind.SYMLINK
    return Kind.FILE


def link_is_dir(path: Path) -> bool:
    '''Whether a link points at a directory, judged without following it.

    Windows records the type in the link itself. POSIX records nothing and os.symlink
    takes no type argument, so the answer is only used on Windows.
    '''
    if os.name != 'nt':
        return False
    attributes = getattr(os.lstat(path), 'st_file_attributes', 0)
    return bool(attributes & FILE_ATTRIBUTE_DIRECTORY)


def supports_hardlink() -> bool:
    '''CPython on Android is built without link(), so the attribute itself is absent.'''
    return hasattr(os, 'link')


def create_hardlink(source: Path, path: Path) -> None:
    os.link(source, path)


def create_symlink(text: str, path: Path, *, target_is_dir: bool) -> None:
    os.symlink(text, path, target_is_directory=target_is_dir)


def remove_path(path: Path) -> None:
    os.unlink(path)


def existing_ancestor(path: Path) -> Path:
    '''The nearest directory at or above path that exists, whose volume a probe can read.'''
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def move_link(source: Path, path: Path) -> None:
    '''Recreate a link at path, then drop the original.

    os.rename, os.replace and shutil.move act on a link's target on Windows, so a link
    moves by being rebuilt from the target text already recorded in the state file.
    '''
    text = read_link(source)
    kind = path_kind(source)
    if kind is Kind.JUNCTION:
        log.warn(f'junction 重建为目录符号链接 {path}')
    mtime_ns, birth_ns = times_of(source)
    create_symlink(text, path, target_is_dir=link_is_dir(source))
    set_times(path, mtime_ns, follow=False, birth_ns=birth_ns)
    os.unlink(source)


def set_times(path: Path, mtime_ns: int, *, follow: bool, birth_ns: int | None = None) -> None:
    '''Give a freshly created path the times the original had.

    Windows writes both times through a handle, because os.utime reaches neither on a
    link. Elsewhere the filesystem offers no way to write a creation time, so only the
    modification time comes back.
    '''
    try:
        if sys.platform == 'win32':
            winstamp.stamp(path, mtime_ns=mtime_ns, birth_ns=birth_ns, follow=follow)
        else:
            os.utime(path, ns=(mtime_ns, mtime_ns), follow_symlinks=follow)
    except NotImplementedError:
        log.warn(f'本平台无法设置链接自身的修改时间 {path}')
    except OSError as error:
        log.warn(f'设置时间失败 {path}: {error.strerror}')


def birth_time(info: os.stat_result) -> int | None:
    '''Creation time, which Windows reports and can write; POSIX gives neither.'''
    return info.st_birthtime_ns if sys.platform == 'win32' else None


def times_of(path: Path) -> tuple[int, int | None]:
    '''The modification and creation times a copy of this path must carry.'''
    info = os.lstat(path)
    return info.st_mtime_ns, birth_time(info)


def copy_file(
    source: Path, path: Path, *, mtime_ns: int | None = None, birth_ns: int | None = None
) -> None:
    '''Copy a file and date the copy from the record, or from the source when unrecorded.'''
    if mtime_ns is None:
        mtime_ns, birth_ns = times_of(source)
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, path)
    set_times(path, mtime_ns, follow=True, birth_ns=birth_ns)


def move_file(source: Path, path: Path) -> None:
    '''Move a regular file, copying across volumes. The source goes only after the copy lands.'''
    copy_file(source, path)
    os.unlink(source)


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f'{path.name}.tmp')
    staging.write_text(text, encoding='utf-8')
    os.replace(staging, path)


class Prober:
    '''Answers whether a directory's volume accepts a hardlink and a symlink.

    Each answer comes from creating one hidden probe and deleting it again, which is the
    only portable way to learn the capability: sdcardfs and FAT refuse both.
    '''

    def __init__(self) -> None:
        self._cache: dict[int, Capabilities] = {}

    def capabilities(self, directory: Path) -> Capabilities:
        device = os.stat(directory).st_dev
        cached = self._cache.get(device)
        if cached is not None:
            return cached
        found = self._probe(directory)
        self._cache[device] = found
        return found

    def _probe(self, directory: Path) -> Capabilities:
        stem = directory / f'.linktool-probe-{os.getpid()}'
        body = stem.with_name(f'{stem.name}-body')
        twin = stem.with_name(f'{stem.name}-twin')
        link = stem.with_name(f'{stem.name}-link')
        try:
            body.write_bytes(b'')
        except OSError:
            return Capabilities(hardlink=False, symlink=False)
        try:
            hardlink = supports_hardlink() and _attempt(lambda: os.link(body, twin))
            symlink = _attempt(lambda: os.symlink('probe', link))
        finally:
            for probe in (body, twin, link):
                with contextlib.suppress(OSError):
                    probe.unlink()
        return Capabilities(hardlink=hardlink, symlink=symlink)


def _attempt(action: Callable[[], object]) -> bool:
    try:
        action()
    except OSError:
        return False
    return True
