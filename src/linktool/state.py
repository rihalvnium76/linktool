import hashlib
import json

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from linktool import jsonio, log
from linktool.errors import ToolError
from linktool.fs import Kind
from linktool.hashing import Identity
from linktool.paths import from_rel, is_within, plain, to_rel

WORK_DIR = '.linktool'
VERSION = 3
PENDING = 'pending'
REBUILT = 'rebuilt'
STAGED = 'staged'
RESTORED = 'restored'


class RecordError(ToolError):
    '''A stored record cannot be resolved to a path.'''


@dataclass(slots=True)
class MemberRecord:
    '''One path the tool removed, staged or rebuilt, and what it must become again.

    `rel` is relative to the record's base and may climb above it with '..', so the member
    follows the base. `abs` holds a member on another volume, which no relative path
    reaches.
    '''

    rel: str | None
    abs: str | None
    kind: Kind
    mtime_ns: int
    birth_ns: int | None
    target_is_dir: bool | None
    status: str
    built: str | None = None

    @property
    def label(self) -> str:
        return self.rel if self.rel is not None else (self.abs or '')

    def to_json(self) -> dict[str, object]:
        return {
            'rel': self.rel,
            'abs': self.abs,
            'kind': str(self.kind),
            'mtime_ns': self.mtime_ns,
            'birth_ns': self.birth_ns,
            'target_is_dir': self.target_is_dir,
            'status': self.status,
            'built': self.built,
        }

    @staticmethod
    def parse(value: object) -> MemberRecord:
        source = jsonio.mapping(value, 'member')
        return MemberRecord(
            rel=jsonio.maybe_text(source, 'rel'),
            abs=jsonio.maybe_text(source, 'abs'),
            kind=Kind(jsonio.text(source, 'kind')),
            mtime_ns=jsonio.integer(source, 'mtime_ns'),
            birth_ns=jsonio.maybe_integer(source, 'birth_ns'),
            target_is_dir=jsonio.flag(source, 'target_is_dir'),
            status=jsonio.text(source, 'status'),
            built=jsonio.maybe_text(source, 'built'),
        )


@dataclass(frozen=True, slots=True)
class Marker:
    '''Where a group's source sits, in the same two forms as a member path.'''

    rel: str | None = None
    abs: str | None = None

    @property
    def label(self) -> str:
        return self.rel if self.rel is not None else (self.abs or '')

    def to_json(self) -> dict[str, object]:
        return {'rel': self.rel} if self.rel is not None else {'abs': self.abs}

    @staticmethod
    def parse(value: object) -> Marker:
        source = jsonio.mapping(value, 'marker')
        return Marker(rel=jsonio.maybe_text(source, 'rel'), abs=jsonio.maybe_text(source, 'abs'))


@dataclass(slots=True)
class GroupRecord:
    '''One duplicate group: what it held, which path stayed, and what must return.'''

    key: str
    identity: Identity | None
    dir_target: str | None
    source: Marker | None
    members: list[MemberRecord] = field(default_factory=list[MemberRecord])

    def to_json(self) -> dict[str, object]:
        identity: dict[str, object] | None = None
        if self.identity is not None:
            identity = {'size': self.identity.size, 'digest': self.identity.digest}
        return {
            'key': self.key,
            'identity': identity,
            'dir_target': self.dir_target,
            'source': self.source.to_json() if self.source is not None else None,
            'members': [member.to_json() for member in self.members],
        }

    @staticmethod
    def parse(value: object) -> GroupRecord:
        source = jsonio.mapping(value, 'group')
        identity = None
        raw_identity = source.get('identity')
        if raw_identity is not None:
            fields = jsonio.mapping(raw_identity, 'identity')
            identity = Identity(
                size=jsonio.integer(fields, 'size'), digest=jsonio.text(fields, 'digest')
            )
        raw_source = source.get('source')
        return GroupRecord(
            key=jsonio.text(source, 'key'),
            identity=identity,
            dir_target=jsonio.maybe_text(source, 'dir_target'),
            source=Marker.parse(raw_source) if raw_source is not None else None,
            members=[
                MemberRecord.parse(item)
                for item in jsonio.sequence(source.get('members'), 'members')
            ],
        )


@dataclass(slots=True)
class StagedItem:
    '''One path a staging area holds, and where it goes back to.'''

    target: str
    staged: str
    kind: Kind
    mtime_ns: int
    birth_ns: int | None
    target_is_dir: bool | None
    status: str

    @property
    def label(self) -> str:
        return self.target

    def to_json(self) -> dict[str, object]:
        return {
            'target': self.target,
            'staged': self.staged,
            'kind': str(self.kind),
            'mtime_ns': self.mtime_ns,
            'birth_ns': self.birth_ns,
            'target_is_dir': self.target_is_dir,
            'status': self.status,
        }

    @staticmethod
    def parse(value: object) -> StagedItem:
        source = jsonio.mapping(value, 'staged item')
        return StagedItem(
            target=jsonio.text(source, 'target'),
            staged=jsonio.text(source, 'staged'),
            kind=Kind(jsonio.text(source, 'kind')),
            mtime_ns=jsonio.integer(source, 'mtime_ns'),
            birth_ns=jsonio.maybe_integer(source, 'birth_ns'),
            target_is_dir=jsonio.flag(source, 'target_is_dir'),
            status=jsonio.text(source, 'status'),
        )


def workdir(container: Path) -> Path:
    return container / WORK_DIR


def staged_location(owner: Path, area: Path, target: Path) -> Path:
    '''Where an area holds the copy of `target`, and where a restore reads it back.

    A path inside the owning tree keeps its relative layout, which stays readable. A path
    outside it lands under a digest of itself, so two targets sharing a name keep their
    own directories, and staging the same target again lands on the same directory. A
    digest is not injective, so the caller checks that no two targets land on one place.
    '''
    if is_within(target, owner):
        return from_rel(area.joinpath('files'), to_rel(target, owner))
    handle = hashlib.blake2s(str(target).encode(), digest_size=8).hexdigest()
    return area / 'files' / 'external' / handle / target.name


def item_path(area: Path, item: StagedItem) -> Path:
    return from_rel(area, item.staged)


class Journal:
    '''Append-only per-record progress, folded into the JSON file when the run ends.

    A whole-file replacement keeps every reader on a complete state, but rewriting the
    file after each member would cost the size of the file per member. The journal makes
    one member's cost a single appended line; a crash leaves it to be replayed on start.
    '''

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, entry: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + '\n')

    def entries(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        found: list[dict[str, object]] = []
        for line in self.path.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            try:
                found.append(jsonio.mapping(json.loads(line), 'journal line'))
            except ValueError, jsonio.FormatError:
                log.warn(f'忽略无法解析的日志行：{line[:80]}')
        return found

    def drop(self) -> None:
        self.path.unlink(missing_ok=True)


class StateStore:
    '''`<state>/.linktool/state.json`: every path the tool took away, and the areas holding it.'''

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.path = folder / 'state.json'
        self._journal = Journal(folder / 'state.jsonl')
        self.state: Path | None = None
        self.base: Path | None = None
        self.staging: list[Path] = []
        self.groups: list[GroupRecord] = []
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            payload = jsonio.mapping(jsonio.read(self.path), 'state.json')
            version = jsonio.integer(payload, 'version')
            if version != VERSION:
                raise RecordError(
                    f'{self.path} 的记录版本是 {version}，本版本只读版本 {VERSION}。\n'
                    f'删掉 {self.folder} 后重新跑 remove 或 stage。'
                )
            state = jsonio.maybe_text(payload, 'state')
            base = jsonio.maybe_text(payload, 'base')
            self.state = Path(state) if state is not None else None
            self.base = Path(base) if base is not None else None
            self.staging = [
                Path(item) for item in jsonio.texts(payload.get('staging', []), 'staging')
            ]
            self.groups = [
                GroupRecord.parse(item) for item in jsonio.sequence(payload.get('groups'), 'groups')
            ]
        if self._journal.path.exists():
            for entry in self._journal.entries():
                self._apply(entry)
            self.save()

    def save(self) -> None:
        jsonio.write(
            self.path,
            {
                'version': VERSION,
                'state': str(self.state) if self.state is not None else None,
                'base': str(self.base) if self.base is not None else None,
                'staging': [str(area) for area in self.staging],
                'groups': [group.to_json() for group in self.groups],
            },
        )

    def add(self, groups: Sequence[GroupRecord], staging: Sequence[Path] = ()) -> None:
        '''Record the groups and the areas that will hold them, before any path moves.'''
        known = {group.key: group for group in self.groups}
        for group in groups:
            known[group.key] = group
        self.groups = list(known.values())
        for area in staging:
            if all(plain(area) != plain(held) for held in self.staging):
                self.staging.append(area)
        self.save()

    def record(self, key: str, member: str, status: str, built: str | None) -> None:
        entry: dict[str, object] = {
            'key': key,
            'member': member,
            'status': status,
            'built': built,
        }
        self._apply(entry)
        self._journal.append(entry)

    def flush(self) -> None:
        if not self._journal.path.exists():
            return
        self.save()
        self._journal.drop()

    def begin_relink(self, label: str, text: str, mtime_ns: int, birth_ns: int | None) -> None:
        '''Leave one link's coming replacement in the journal, which survives a crash.

        The line carries no key, so replaying the journal skips it and the run's own
        `flush` drops it. A crash leaves it as the only trace of the path being
        replaced, with the text and times a person needs to redo it.
        '''
        self._journal.append(
            {
                'phase': 'begin',
                'member': label,
                'link_text': text,
                'mtime_ns': mtime_ns,
                'birth_ns': birth_ns,
            }
        )

    def pending(self) -> list[GroupRecord]:
        return [group for group in self.groups if any(m.status == PENDING for m in group.members)]

    def find(self, label: str) -> tuple[GroupRecord, MemberRecord] | None:
        for group in self.groups:
            for member in group.members:
                if member.label == label:
                    return group, member
        return None

    def _apply(self, entry: dict[str, object]) -> None:
        key = jsonio.maybe_text(entry, 'key')
        member = jsonio.maybe_text(entry, 'member')
        if key is None or member is None:
            return
        for group in self.groups:
            if group.key != key:
                continue
            for record in group.members:
                if record.label != member:
                    continue
                status = jsonio.maybe_text(entry, 'status')
                if status is not None:
                    record.status = status
                built = jsonio.maybe_text(entry, 'built')
                if built is not None:
                    record.built = built
                return


class StagingStore:
    '''`<staging>/.linktool/staging.json`: what one area holds and where it belongs.'''

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.path = folder / 'staging.json'
        self._journal = Journal(folder / 'staging.jsonl')
        self.owner: Path | None = None
        self.items: dict[str, StagedItem] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            payload = jsonio.mapping(jsonio.read(self.path), 'staging.json')
            version = jsonio.integer(payload, 'version')
            if version != VERSION:
                raise RecordError(
                    f'{self.path} 的清单版本是 {version}，本版本只读版本 {VERSION}。\n'
                    '删掉暂存区里的 .linktool 目录，重新跑 stage。'
                )
            owner = jsonio.maybe_text(payload, 'owner')
            self.owner = Path(owner) if owner is not None else None
            for item in jsonio.sequence(payload.get('members'), 'members'):
                staged = StagedItem.parse(item)
                self.items[staged.target] = staged
        if self._journal.path.exists():
            for entry in self._journal.entries():
                target = jsonio.maybe_text(entry, 'member')
                status = jsonio.maybe_text(entry, 'status')
                if target in self.items and status is not None:
                    self.items[target].status = status
            self.save()

    def save(self) -> None:
        jsonio.write(
            self.path,
            {
                'version': VERSION,
                'owner': str(self.owner) if self.owner is not None else None,
                'members': [item.to_json() for item in self.items.values()],
            },
        )

    def add(self, items: Iterable[StagedItem]) -> None:
        for item in items:
            self.items[item.target] = item
        self.save()

    def record(self, target: str, status: str) -> None:
        item = self.items.get(target)
        if item is not None:
            item.status = status
            self._journal.append({'member': target, 'status': status})

    def flush(self) -> None:
        if not self._journal.path.exists():
            return
        self.save()
        self._journal.drop()

    def staged(self) -> list[StagedItem]:
        return [item for item in self.items.values() if item.status == STAGED]
