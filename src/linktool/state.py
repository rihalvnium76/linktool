import json

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from linktool import jsonio, log
from linktool.errors import ToolError
from linktool.fs import Kind
from linktool.hashing import Identity

WORK_DIR = '.linktool'
PENDING = 'pending'
REBUILT = 'rebuilt'
FAILED = 'failed'
STAGED = 'staged'
RESTORED = 'restored'


class RecordError(ToolError):
    '''A stored record cannot be resolved to a path.'''


@dataclass(slots=True)
class MemberRecord:
    '''One path the tool removed, staged or rebuilt, and what it must become again.'''

    rel: str | None
    abs: str | None
    kind: Kind
    mtime_ns: int
    link_text: str | None
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
            'link_text': self.link_text,
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
            link_text=jsonio.maybe_text(source, 'link_text'),
            target_is_dir=jsonio.flag(source, 'target_is_dir'),
            status=jsonio.text(source, 'status'),
            built=jsonio.maybe_text(source, 'built'),
        )


@dataclass(slots=True)
class GroupRecord:
    '''One duplicate group: what it held, which path stayed, and what must return.'''

    key: str
    identity: Identity | None
    dir_target: str | None
    source_rel: str | None
    source_abs: str | None
    members: list[MemberRecord] = field(default_factory=list[MemberRecord])

    def to_json(self) -> dict[str, object]:
        identity: dict[str, object] | None = None
        if self.identity is not None:
            identity = {'size': self.identity.size, 'digest': self.identity.digest}
        return {
            'key': self.key,
            'identity': identity,
            'dir_target': self.dir_target,
            'source_rel': self.source_rel,
            'source_abs': self.source_abs,
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
        raw_members = jsonio.sequence(source.get('members'), 'members')
        return GroupRecord(
            key=jsonio.text(source, 'key'),
            identity=identity,
            dir_target=jsonio.maybe_text(source, 'dir_target'),
            source_rel=jsonio.maybe_text(source, 'source_rel'),
            source_abs=jsonio.maybe_text(source, 'source_abs'),
            members=[MemberRecord.parse(item) for item in raw_members],
        )


def workdir(root: Path) -> Path:
    return root / WORK_DIR


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
    '''`<root>/.linktool/state.json`: every path the tool took away or put back.'''

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.path = folder / 'state.json'
        self._journal = Journal(folder / 'state.jsonl')
        self.groups: list[GroupRecord] = []
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            payload = jsonio.mapping(jsonio.read(self.path), 'state.json')
            self.groups = [
                GroupRecord.parse(item) for item in jsonio.sequence(payload.get('groups'), 'groups')
            ]
        if self._journal.path.exists():
            for entry in self._journal.entries():
                self._apply(entry)
            self.save()

    def save(self) -> None:
        jsonio.write(
            self.path, {'version': 1, 'groups': [group.to_json() for group in self.groups]}
        )

    def add(self, groups: Sequence[GroupRecord]) -> None:
        known = {group.key: group for group in self.groups}
        for group in groups:
            known[group.key] = group
        self.groups = list(known.values())
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

    def relink(self, label: str, text: str) -> None:
        for group in self.groups:
            for member in group.members:
                if member.label == label:
                    member.link_text = text
                    self._journal.append({'key': group.key, 'member': label, 'link_text': text})
                    return

    def flush(self) -> None:
        if not self._journal.path.exists():
            return
        self.save()
        self._journal.drop()

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
                text = jsonio.maybe_text(entry, 'link_text')
                if text is not None:
                    record.link_text = text
                return


class StagingStore:
    '''`<staging>/.linktool/staging.json`: what the staging area holds and where it belongs.'''

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.path = folder / 'staging.json'
        self._journal = Journal(folder / 'staging.jsonl')
        self.root: Path | None = None
        self.items: dict[str, MemberRecord] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            payload = jsonio.mapping(jsonio.read(self.path), 'staging.json')
            root = jsonio.maybe_text(payload, 'root')
            self.root = Path(root) if root is not None else None
            for item in jsonio.sequence(payload.get('members'), 'members'):
                member = MemberRecord.parse(item)
                self.items[member.label] = member
        if self._journal.path.exists():
            for entry in self._journal.entries():
                label = jsonio.maybe_text(entry, 'member')
                status = jsonio.maybe_text(entry, 'status')
                if label in self.items and status is not None:
                    self.items[label].status = status
            self.save()

    def save(self) -> None:
        jsonio.write(
            self.path,
            {
                'version': 1,
                'root': str(self.root) if self.root is not None else None,
                'members': [member.to_json() for member in self.items.values()],
            },
        )

    def add(self, members: Iterable[MemberRecord]) -> None:
        for member in members:
            self.items[member.label] = member
        self.save()

    def record(self, label: str, status: str) -> None:
        member = self.items.get(label)
        if member is not None:
            member.status = status
            self._journal.append({'member': label, 'status': status})

    def flush(self) -> None:
        if not self._journal.path.exists():
            return
        self.save()
        self._journal.drop()

    def staged(self) -> list[MemberRecord]:
        return [member for member in self.items.values() if member.status == STAGED]
