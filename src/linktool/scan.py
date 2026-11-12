import os
import stat

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from linktool import log
from linktool.fs import Kind, birth_time, link_is_dir
from linktool.paths import is_within, plain, read_link, to_rel

SKIP_NAME = '.linktool'


@dataclass(frozen=True, slots=True)
class Entry:
    path: Path
    rel: str | None
    kind: Kind
    mtime_ns: int
    birth_ns: int | None
    link_text: str | None
    target_is_dir: bool | None

    @property
    def label(self) -> str:
        return self.rel if self.rel is not None else str(self.path)


@dataclass(slots=True)
class ScanResult:
    entries: list[Entry] = field(default_factory=list[Entry])
    dirs: list[Path] = field(default_factory=list[Path])


def collect(roots: Sequence[Path], root: Path | None, exclude: Sequence[Path]) -> ScanResult:
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
            found.entries.append(_entry(path, root, Kind.SYMLINK, info))
            continue
        if os.path.isjunction(path):
            found.entries.append(_entry(path, root, Kind.JUNCTION, info))
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
            found.entries.append(_entry(path, root, Kind.FILE, info))
    return found


def _entry(path: Path, root: Path | None, kind: Kind, info: os.stat_result) -> Entry:
    is_link = kind is not Kind.FILE
    return Entry(
        path=path,
        rel=to_rel(path, root) if root is not None and is_within(path, root) else None,
        kind=kind,
        mtime_ns=info.st_mtime_ns,
        birth_ns=birth_time(info),
        link_text=read_link(path) if is_link else None,
        target_is_dir=link_is_dir(path) if is_link else None,
    )
