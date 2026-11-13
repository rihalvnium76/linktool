import os
import stat

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from linktool import hashing, log
from linktool.config import Pick
from linktool.fs import Kind
from linktool.hashing import SAMPLE_MIN, Identity
from linktool.paths import is_within, plain
from linktool.scan import Entry


@dataclass(frozen=True, slots=True)
class Source:
    '''The path a group's links should point at, and the rule that named it.'''

    path: Path
    reason: str


@dataclass(frozen=True, slots=True)
class Participant:
    entry: Entry
    real: Path


@dataclass(slots=True)
class Group:
    key: str
    identity: Identity | None
    dir_target: Path | None
    members: list[Participant] = field(default_factory=list[Participant])

    @property
    def is_dir(self) -> bool:
        return self.dir_target is not None


@dataclass(slots=True)
class _Real:
    path: Path
    size: int
    entries: list[Entry] = field(default_factory=list[Entry])


def build(entries: Sequence[Entry]) -> list[Group]:
    '''Group entries whose content is the same, by size, then sample, then full digest.'''
    files: dict[str, _Real] = {}
    dirs: dict[str, _Real] = {}
    for entry in entries:
        if entry.kind is Kind.FILE:
            real = entry.path
            try:
                info = os.stat(real)
            except OSError as error:
                log.warn(f'跳过无法读取的文件 {real}: {error.strerror}')
                continue
            if stat.S_ISREG(info.st_mode):
                _remember(files, _file_key(real, info), real, info.st_size, entry)
            continue
        real = Path(os.path.realpath(entry.path))
        try:
            info = os.stat(real)
        except OSError as error:
            log.warn(f'跳过无法解析目标的链接 {entry.path}: {error.strerror}')
            continue
        if stat.S_ISDIR(info.st_mode):
            _remember(dirs, plain(real), real, 0, entry)
        elif stat.S_ISREG(info.st_mode):
            _remember(files, _file_key(real, info), real, info.st_size, entry)

    return [*_file_groups(files), *_dir_groups(dirs)]


def _file_key(real: Path, info: os.stat_result) -> str:
    '''One key per file object, so links to one inode have their content read once.

    A filesystem that fabricates inodes reports zero for every file, and keying by that
    would collapse them all, so only a numbered inode of a linked file shares its key.
    '''
    if info.st_nlink > 1 and info.st_ino != 0:
        return f'ino:{info.st_dev}:{info.st_ino}'
    return plain(real)


def _remember(bucket: dict[str, _Real], key: str, real: Path, size: int, entry: Entry) -> None:
    existing = bucket.get(key)
    if existing is None:
        bucket[key] = _Real(path=real, size=size, entries=[entry])
    else:
        existing.entries.append(entry)


def _file_groups(files: dict[str, _Real]) -> list[Group]:
    by_size: dict[int, list[_Real]] = {}
    for real in files.values():
        by_size.setdefault(real.size, []).append(real)

    identities: dict[Identity, list[_Real]] = {}
    for size, bucket in by_size.items():
        for part in _split(size, bucket):
            for real in part:
                if len(part) == 1 and len(real.entries) == 1:
                    continue
                try:
                    identity = hashing.full(size, real.path)
                except OSError as error:
                    log.warn(f'跳过无法读取的文件 {real.path}: {error.strerror}')
                    continue
                identities.setdefault(identity, []).append(real)

    groups: list[Group] = []
    for identity, reals in identities.items():
        members = [
            Participant(entry=entry, real=entry.path if entry.kind is Kind.FILE else real.path)
            for real in reals
            for entry in real.entries
        ]
        if _distinct(members) < 2:
            continue
        key = f'f:{identity.size}:{identity.digest}'
        groups.append(Group(key=key, identity=identity, dir_target=None, members=members))
    return groups


def _split(size: int, bucket: list[_Real]) -> list[list[_Real]]:
    if len(bucket) < 2 or size < SAMPLE_MIN:
        return [bucket]
    by_sample: dict[str, list[_Real]] = {}
    for real in bucket:
        try:
            digest = hashing.quick(size, real.path)
        except OSError as error:
            log.warn(f'跳过无法读取的文件 {real.path}: {error.strerror}')
            continue
        by_sample.setdefault(digest, []).append(real)
    return list(by_sample.values())


def _dir_groups(dirs: dict[str, _Real]) -> list[Group]:
    groups: list[Group] = []
    for key, real in dirs.items():
        members = [Participant(entry=entry, real=real.path) for entry in real.entries]
        if _distinct(members) < 2:
            continue
        groups.append(Group(key=f'd:{key}', identity=None, dir_target=real.path, members=members))
    return groups


def _distinct(members: Sequence[Participant]) -> int:
    return len({plain(member.entry.path) for member in members})


def auto_source(group: Group, prefer: Sequence[Path], pick: Pick) -> Source | None:
    '''The source the rules settle without asking, or None when they leave it open.

    A group of directory links has one target by construction. For files, a single
    realpath among the link members wins, then a single object under a preferred
    directory. Two rules that disagree, or no rule at all, leave the choice to the user
    unless `pick` names a fallback; either way the tool never decides silently.
    '''
    if group.dir_target is not None:
        return Source(group.dir_target, 'link')
    link_reals = {
        plain(member.real): member.real
        for member in group.members
        if member.entry.kind is not Kind.FILE
    }
    from_links = next(iter(link_reals.values())) if len(link_reals) == 1 else None
    under = [
        member
        for member in group.members
        if any(is_within(member.entry.path, folder) for folder in prefer)
    ]
    preferred = _single_object(under)
    if from_links is not None and preferred is not None:
        if plain(from_links) == plain(preferred) or _same_object(from_links, preferred):
            return Source(from_links, 'link')
        return None
    if from_links is not None:
        return Source(from_links, 'link')
    if preferred is not None:
        return Source(preferred, 'prefer')
    if pick is Pick.FIRST:
        first = min(group.members, key=lambda member: plain(member.entry.path))
        return Source(first.real, 'fallback')
    return None


def _single_object(members: Sequence[Participant]) -> Path | None:
    '''The one place the preferred members resolve to, counting hardlinks as one place.'''
    if not members:
        return None
    if len({_object_key(member) for member in members}) != 1:
        return None
    first = min(members, key=lambda member: plain(member.entry.path))
    return first.real


def _object_key(member: Participant) -> str:
    if member.entry.kind is Kind.FILE:
        try:
            info = os.lstat(member.entry.path)
        except OSError:
            return f'path:{plain(member.entry.path)}'
        return f'ino:{info.st_dev}:{info.st_ino}'
    return f'path:{plain(member.real)}'


def _same_object(first: Path, second: Path) -> bool:
    try:
        left = os.stat(first)
        right = os.stat(second)
    except OSError:
        return False
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def resolve(group: Group, path: Path) -> Path:
    '''Map a chosen member path to the object new links should point at.'''
    for member in group.members:
        if plain(member.entry.path) == plain(path):
            return member.real
    return path


def signature(group: Group) -> str:
    '''A stable label for the group, used to key its questions across runs.'''
    return '|'.join(sorted(plain(member.entry.path) for member in group.members))
