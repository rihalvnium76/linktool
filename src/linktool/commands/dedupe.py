from pathlib import Path

from linktool import EXIT_OK, errors, identify, log, plan, scan, state
from linktool.commands import shared
from linktool.config import Config, ConfigError
from linktool.fs import Kind, move_file, move_link, remove_path
from linktool.paths import is_within, plain, to_rel


def run(config: Config, *, stage: bool) -> int:
    root = config.required_root()
    folder = state.workdir(root)
    command = 'stage' if stage else 'remove'
    plan_path = folder / f'plan-{command}.json'
    args = shared.snapshot(config)
    staging = config.required_staging() if stage else None
    if staging is not None:
        if is_within(staging, root):
            raise ConfigError('暂存区不能位于受管树内，否则它会被复制工具一起搬走')
        try:
            staging.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise ConfigError(f'无法建立暂存区 {staging}: {error.strerror}') from error

    ask = plan.Interrogator(plan.answers(plan_path, command, args))

    scans = config.scans()
    outside = [path for path in scans if not is_within(path, root)]
    if outside:
        gate = ask.confirm(
            plan.short_id('scan', *(str(path) for path in outside)),
            '这些扫描路径在受管树之外：\n'
            + '\n'.join(f'  {path}' for path in outside)
            + '\n它们会被登记进状态文件。确认扫描吗？（yes/no）',
        )
        if gate is None:
            return shared.await_answers(plan_path, command, args, ask)
        if not gate:
            raise ConfigError('你否决了受管树之外的扫描路径，本次未执行')

    exclude = [folder]
    if staging is not None:
        exclude.append(staging)
    groups = identify.build(scan.collect(scans, root, exclude).entries)
    groups.sort(key=lambda group: group.key)

    decisions: list[dict[str, object]] = []
    records = [
        record
        for group in groups
        if (record := _decide(group, root, config, ask, decisions)) is not None
    ]
    if ask.questions:
        return shared.await_answers(plan_path, command, args, ask, decisions)
    if not records:
        log.note('没有找到可处理的重复文件或链接')
        plan.discard(plan_path)
        return EXIT_OK
    released = _ask_for_the_plan(ask, records, decisions)
    if released is None:
        return shared.await_answers(plan_path, command, args, ask, decisions)
    if not released:
        raise errors.ToolError(
            f'你否决了这份计划，磁盘上没有任何改动。'
            f'把 {plan_path} 里 plan- 开头的题改成 yes 可重新放行。'
        )

    _apply(records, root, folder, staging)
    plan.discard(plan_path)
    total = sum(len(record.members) for record in records)
    log.note(f'{"已暂存" if stage else "已移除"} {total} 条路径，共 {len(records)} 组')
    return EXIT_OK


def _decide(
    group: identify.Group,
    root: Path,
    config: Config,
    ask: plan.Interrogator,
    decisions: list[dict[str, object]],
) -> state.GroupRecord | None:
    source = identify.auto_source(group, config.prefer, config.pick)
    reason = source.reason if source is not None else ''
    decided = source.path if source is not None else None
    answered = False
    if decided is None:
        choice = ask.choose(
            plan.short_id('source', identify.signature(group)),
            f'这一组 {_describe(group)} 的源无法自动确定：符号链接的 realpath 不唯一，'
            '偏好目录下的成员也不唯一。\n请填候选序号、候选值，或 skip 跳过该组。',
            _candidates(group),
        )
        if choice is None or choice == 'skip':
            return None
        decided = identify.resolve(group, Path(choice))
        reason = 'yours'
        answered = True

    if not is_within(decided, root) and not answered:
        gate = ask.confirm(
            plan.short_id('outside', identify.signature(group)),
            f'这一组的源在受管树之外：{decided}\n'
            '目标机上这个路径可能不存在，重建出的链接会在那里失效。确认以它作为源吗？（yes/no）',
        )
        if gate is None or not gate:
            return None

    members = [member for member in group.members if plain(member.entry.path) != plain(decided)]
    if not members:
        return None
    record = _record(root, group, decided, members)
    decisions.append(
        {
            'source': record.source_rel or record.source_abs,
            'reason': _REASONS[reason],
            'identity': _identity_note(group),
            'paths': [member.label for member in record.members],
        }
    )
    return record


_REASONS = {
    'link': '符号链接的 realpath 唯一',
    'prefer': '偏好目录下唯一',
    'fallback': '无规则可依，按路径排序取最前',
    'yours': '你在本文件中指定',
}


def _identity_note(group: identify.Group) -> str:
    if group.identity is None:
        return '目录链接'
    return f'{group.identity.size} 字节 {group.identity.digest[:12]}'


def _ask_for_the_plan(
    ask: plan.Interrogator, records: list[state.GroupRecord], decisions: list[dict[str, object]]
) -> bool | None:
    '''One gate for the whole plan: the rules proposed every source, the user releases them.'''
    fallback = sum(1 for item in decisions if item['reason'] == _REASONS['fallback'])
    total = sum(len(record.members) for record in records)
    extra = f'，其中 {fallback} 组由排序兜底选定' if fallback else ''
    seed = (f'{record.key}|{record.source_rel or record.source_abs}' for record in records)
    return ask.confirm(
        plan.short_id('plan', *seed),
        f'本文件的 decisions 段列出这一轮的全部决定：{len(records)} 组，'
        f'{total} 条路径会被移除或暂存{extra}。\n'
        '确认之前磁盘上不会有任何改动。确认执行吗？（yes/no）',
    )


def _describe(group: identify.Group) -> str:
    if group.identity is not None:
        return f'（{group.identity.size} 字节）'
    return '（目录链接）'


def _candidates(group: identify.Group) -> list[plan.Candidate]:
    ordered = sorted(group.members, key=lambda member: member.entry.label)
    return [
        plan.Candidate(index=index, value=str(member.entry.path), label=_label(member))
        for index, member in enumerate(ordered, start=1)
    ]


def _label(member: identify.Participant) -> str:
    if member.entry.kind is Kind.FILE:
        return member.entry.label
    return f'{member.entry.label}  ->  {member.real}'


def _record(
    root: Path, group: identify.Group, source: Path, members: list[identify.Participant]
) -> state.GroupRecord:
    source_rel = None
    source_abs = None
    if is_within(source, root):
        source_rel = to_rel(source, root)
    else:
        source_abs = str(source)
    return state.GroupRecord(
        key=group.key,
        identity=group.identity,
        dir_target=str(group.dir_target) if group.dir_target is not None else None,
        source_rel=source_rel,
        source_abs=source_abs,
        members=[
            state.MemberRecord(
                rel=member.entry.rel,
                abs=None if member.entry.rel is not None else str(member.entry.path),
                kind=member.entry.kind,
                mtime_ns=member.entry.mtime_ns,
                link_text=member.entry.link_text,
                target_is_dir=member.entry.target_is_dir,
                status=state.PENDING,
            )
            for member in sorted(members, key=lambda participant: participant.entry.label)
        ],
    )


def _apply(
    records: list[state.GroupRecord], root: Path, folder: Path, staging: Path | None
) -> None:
    store = state.StateStore(folder)
    store.add(records)
    staging_store: state.StagingStore | None = None
    if staging is not None:
        staging_store = state.StagingStore(state.workdir(staging))
        staging_store.root = root
        staging_store.add(
            state.MemberRecord(
                rel=member.rel,
                abs=member.abs,
                kind=member.kind,
                mtime_ns=member.mtime_ns,
                link_text=member.link_text,
                target_is_dir=member.target_is_dir,
                status=state.STAGED,
            )
            for record in records
            for member in record.members
        )

    for record in records:
        for member in record.members:
            path = shared.member_path(root, member)
            if staging is None:
                remove_path(path)
                continue
            destination = shared.staged_path(staging, member)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if member.kind is Kind.FILE:
                move_file(path, destination)
            else:
                move_link(path, destination)
    if staging_store is not None:
        staging_store.flush()
