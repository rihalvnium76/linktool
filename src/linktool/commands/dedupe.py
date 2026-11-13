from dataclasses import dataclass, field, replace
from pathlib import Path

from linktool import EXIT_OK, errors, identify, log, plan, scan, state
from linktool.commands import shared
from linktool.config import Config, ConfigError, Pick
from linktool.fs import (
    Kind,
    Prober,
    existing_ancestor,
    move_file,
    move_link,
    remove_path,
)
from linktool.paths import is_within, plain, to_rel


def run(config: Config, *, stage: bool) -> int:
    command = 'stage' if stage else 'remove'
    owner = config.state
    folder = state.workdir(owner)
    plan_path = folder / f'plan-{command}.json'
    args = shared.snapshot(config)
    areas = config.required_stagings() if stage else ()
    _require_owners(areas, owner)

    store = state.StateStore(folder)
    ask = plan.Interrogator(plan.answers(plan_path, command, args))
    base = shared.effective_base(store, config, ask)
    gate = shared.scan_gate(ask, config.scan, base if base is not None else config.base)
    if base is None or gate is None:
        return shared.await_answers(plan_path, command, args, ask)
    if not gate:
        raise ConfigError('基准目录之外的扫描路径未获确认，本次未执行')
    _create(areas)

    groups = identify.build(scan.collect(config.scan, [folder, *areas]).entries)
    groups.sort(key=lambda group: group.key)

    decider = Decider(
        ask=ask, owner=owner, base=base, areas=areas, prefer=config.prefer, pick=config.pick
    )
    records = [record for group in groups if (record := decider.decide(group)) is not None]
    if decider.blocked:
        shared.confirm_unavailable(ask, decider.blocked, verb='暂存', keeping='留在原处')
    if ask.pending:
        return shared.await_answers(plan_path, command, args, ask, decider.decisions)
    if not records:
        log.note(
            '没有可处理的重复文件或链接'
            + (
                f'，跳过 {len(decider.blocked)} 条目标卷不支持符号链接的链接'
                if decider.blocked
                else ''
            )
        )
        plan.discard(plan_path)
        return EXIT_OK
    released = _ask_for_the_plan(ask, records, decider.decisions, plan_path)
    if released is None:
        return shared.await_answers(plan_path, command, args, ask, decider.decisions)
    if not released:
        raise errors.ToolError(
            f'这份计划未获确认，磁盘上没有任何改动。'
            f'把 {plan.sheet(plan_path)} 里 plan- 开头的题改成 yes 再重跑，可重新放行。'
        )

    _apply(records, owner, base, areas)
    plan.discard(plan_path)
    total = sum(len(record.members) for record in records)
    log.note(f'{"已暂存" if stage else "已移除"} {total} 条路径，共 {len(records)} 组')
    return EXIT_OK


@dataclass(slots=True)
class Decider:
    '''What every group of one run is decided against.'''

    ask: plan.Interrogator
    owner: Path
    base: Path
    areas: tuple[Path, ...]
    prefer: tuple[Path, ...]
    pick: Pick
    prober: Prober = field(default_factory=Prober)
    decisions: list[dict[str, object]] = field(default_factory=list[dict[str, object]])
    blocked: list[str] = field(default_factory=list[str])

    def decide(self, group: identify.Group) -> state.GroupRecord | None:
        source = identify.auto_source(group, self.prefer, self.pick)
        reason = source.reason if source is not None else ''
        decided = source.path if source is not None else None
        answered = False
        if decided is None:
            choice = self.ask.choose(
                plan.short_id('source', identify.signature(group)),
                f'这一组 {_describe(group)} 有 {len(group.members)} 条路径，规则定不出唯一的源。\n'
                '请填候选序号、候选值，或 skip 跳过该组。',
                _candidates(group, self.base),
            )
            if choice is None or choice == 'skip':
                return None
            decided = identify.resolve(group, Path(choice))
            reason = 'yours'
            answered = True

        if not is_within(decided, self.base) and not answered:
            gate = self.ask.confirm(
                plan.short_id('outside', identify.signature(group)),
                f'这一组的源在基准目录之外：{decided}\n'
                '它在记录里会写成绝对路径，换一份记录或换一台机器后可能指向不存在的位置。'
                '确认以它作为源吗？（yes/no）',
            )
            if gate is None or not gate:
                return None

        rest = [item for item in group.members if plain(item.entry.path) != plain(decided)]
        if not rest:
            return None
        record = self._record(group, decided, rest)
        if self.areas:
            record = self._without_unstaged(record)
            if record is None:
                return None
        self.decisions.append(
            {
                'source': record.source.label if record.source is not None else '',
                'reason': _REASONS[reason],
                'identity': _identity_note(group),
                'paths': [member.label for member in record.members],
            }
        )
        return record

    def _without_unstaged(self, record: state.GroupRecord) -> state.GroupRecord | None:
        '''Drop the links the staging volume cannot hold, naming them for one confirmation.'''
        refused = {
            member.label
            for member in record.members
            if member.kind is not Kind.FILE
            and not self.prober.capabilities(
                existing_ancestor(
                    shared.staged_path(self.owner, self.base, self.areas, member).parent
                )
            ).symlink
        }
        if not refused:
            return record
        self.blocked.extend(sorted(refused))
        kept = [member for member in record.members if member.label not in refused]
        return replace(record, members=kept) if kept else None

    def _record(
        self, group: identify.Group, source: Path, members: list[identify.Participant]
    ) -> state.GroupRecord:
        ordered = sorted(members, key=lambda item: plain(item.entry.path))
        return state.GroupRecord(
            key=group.key,
            identity=group.identity,
            dir_target=str(group.dir_target) if group.dir_target is not None else None,
            source=shared.marker(self.base, source),
            members=[self._member(item) for item in ordered],
        )

    def _member(self, member: identify.Participant) -> state.MemberRecord:
        rel, absolute = shared.record_place(self.base, member.entry.path)
        return state.MemberRecord(
            rel=rel,
            abs=absolute,
            kind=member.entry.kind,
            mtime_ns=member.entry.mtime_ns,
            birth_ns=member.entry.birth_ns,
            target_is_dir=member.entry.target_is_dir,
            status=state.PENDING,
        )


_REASONS = {
    'link': '符号链接的 realpath 唯一',
    'prefer': '偏好目录下唯一',
    'fallback': '无规则可依，按路径排序取最前',
    'yours': '由用户在本文件中指定',
}


def _identity_note(group: identify.Group) -> str:
    if group.identity is None:
        return '目录链接'
    return f'{group.identity.size} 字节 {group.identity.digest[:12]}'


def _ask_for_the_plan(
    ask: plan.Interrogator,
    records: list[state.GroupRecord],
    decisions: list[dict[str, object]],
    plan_path: Path,
) -> bool | None:
    '''One gate for the whole plan: the rules proposed every source, the user releases them.'''
    fallback = sum(1 for item in decisions if item['reason'] == _REASONS['fallback'])
    total = sum(len(record.members) for record in records)
    extra = f'，其中 {fallback} 组由排序兜底选定' if fallback else ''
    seed = (
        f'{record.key}|{record.source.label if record.source is not None else ""}'
        for record in records
    )
    return ask.confirm(
        plan.short_id('plan', *seed),
        f'{plan_path.name} 的 decisions 段列出这一轮的全部决定：{len(records)} 组，'
        f'{total} 条路径会被移除或暂存{extra}。\n'
        '确认之前磁盘上不会有任何改动。确认执行吗？（yes/no）',
    )


def _describe(group: identify.Group) -> str:
    if group.identity is not None:
        return f'（{group.identity.size} 字节）'
    return '（目录链接）'


def _candidates(group: identify.Group, base: Path) -> list[plan.Candidate]:
    ordered = sorted(group.members, key=lambda member: plain(member.entry.path))
    return [
        plan.Candidate(index=index, value=str(member.entry.path), label=_label(member, base))
        for index, member in enumerate(ordered, start=1)
    ]


def _label(member: identify.Participant, base: Path) -> str:
    name = shared.record_label(base, member.entry.path)
    if member.entry.kind is Kind.FILE:
        return name
    return f'{name}  ->  {member.real}'


def _require_owners(areas: tuple[Path, ...], owner: Path) -> None:
    '''One staging area holds the entries of one tree, so a shared area cannot mix them.'''
    for area in areas:
        held = state.StagingStore(state.workdir(area))
        if held.owner is not None and plain(held.owner) != plain(owner):
            raise ConfigError(
                f'暂存区 {area} 属于 {held.owner}，不能同时服务 {owner}。\n'
                '换一个暂存区，或把这个暂存区里的条目还原干净后删掉它的 .linktool 目录。'
            )


def _create(areas: tuple[Path, ...]) -> None:
    for area in areas:
        try:
            area.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise ConfigError(f'无法建立暂存区 {area}: {error.strerror}') from error


def _apply(
    records: list[state.GroupRecord], owner: Path, base: Path, areas: tuple[Path, ...]
) -> None:
    store = state.StateStore(state.workdir(owner))
    store.state = owner
    store.base = base
    if not areas:
        store.add(records)
        for record in records:
            for member in record.members:
                remove_path(shared.member_path(base, member))
        return

    held, used = _hold(records, owner, base, areas)
    store.add(records, used)
    for record in records:
        for member in record.members:
            path = shared.member_path(base, member)
            destination = shared.staged_path(owner, base, areas, member)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if member.kind is Kind.FILE:
                move_file(path, destination)
            else:
                move_link(path, destination)
    for item in held:
        item.flush()


def _hold(
    records: list[state.GroupRecord], owner: Path, base: Path, areas: tuple[Path, ...]
) -> tuple[list[state.StagingStore], list[Path]]:
    '''Write the manifest of every area that takes a member, before any member moves.'''
    found: dict[str, list[state.StagedItem]] = {plain(area): [] for area in areas}
    for record in records:
        for member in record.members:
            target = shared.member_path(base, member)
            area = shared.staging_area(areas, target)
            found[plain(area)].append(
                state.StagedItem(
                    target=str(target),
                    staged=to_rel(state.staged_location(owner, area, target), area),
                    kind=member.kind,
                    mtime_ns=member.mtime_ns,
                    birth_ns=member.birth_ns,
                    target_is_dir=member.target_is_dir,
                    status=state.STAGED,
                )
            )
    stores: list[state.StagingStore] = []
    used: list[Path] = []
    for area in areas:
        items = found[plain(area)]
        if not items:
            continue
        store = state.StagingStore(state.workdir(area))
        store.owner = owner
        store.add(items)
        stores.append(store)
        used.append(area)
    return stores, used
