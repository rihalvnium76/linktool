import os
import stat

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from linktool import log
from linktool.fs import Kind, birth_time, link_is_dir
from linktool.paths import is_within, plain, read_link

SKIP_NAME = '.linktool'


@dataclass(frozen=True, slots=True)
class Entry:
    path: Path
    kind: Kind
    mtime_ns: int
    birth_ns: int | None
    link_text: str | None
    target_is_dir: bool | None


@dataclass(slots=True)
class ScanResult:
    entries: list[Entry] = field(default_factory=list[Entry])
    dirs: list[Path] = field(default_factory=list[Path])


def collect(roots: Sequence[Path], exclude: Sequence[Path]) -> ScanResult:
    '''Walk every root, yielding files and links. Directories are traversed, links are not.'''
    found = ScanResult()
    seen: set[str] = set()
    stack = list(roots)
    while stack:
        path = stack.pop()
        key = plain(path)
        if key in seen:
            continue
        seen.add(key)
        if any(is_within(path, stop) for stop in exclude):
            continue
        try:
            info = os.lstat(path)
        except OSError as error:
            log.warn(f'跳过无法读取的路径 {path}: {error.strerror}')
            continue
        if stat.S_ISLNK(info.st_mode):
            found.entries.append(_entry(path, Kind.SYMLINK, info))
            continue
        if os.path.isjunction(path):
            found.entries.append(_entry(path, Kind.JUNCTION, info))
            continue
        if stat.S_ISDIR(info.st_mode):
            if path.name == SKIP_NAME:
                continue
            found.dirs.append(path)
            try:
                with os.scandir(path) as listing:
                    stack.extend(Path(item.path) for item in listing)
            except OSError as error:
                log.warn(f'跳过无法列出的目录 {path}: {error.strerror}')
            continue
        if stat.S_ISREG(info.st_mode):
            found.entries.append(_entry(path, Kind.FILE, info))
    return found


def _entry(path: Path, kind: Kind, info: os.stat_result) -> Entry:
    is_link = kind is not Kind.FILE
    return Entry(
        path=path,
        kind=kind,
        mtime_ns=info.st_mtime_ns,
        birth_ns=birth_time(info),
        link_text=read_link(path) if is_link else None,
        target_is_dir=link_is_dir(path) if is_link else None,
    )
