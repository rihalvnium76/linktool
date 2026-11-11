import os

from dataclasses import dataclass
from pathlib import Path

from linktool import EXIT_OK, errors, hashing, log, plan, state
from linktool.commands import shared
from linktool.config import Config
from linktool.fs import (
    Built,
    Kind,
    Prober,
    copy_file,
    create_hardlink,
    create_symlink,
    remove_path,
    set_times,
)
from linktool.paths import is_within, link_text, plain


@dataclass(frozen=True, slots=True)
class Action:
    group: state.GroupRecord
    member: state.MemberRecord
    source: Path
    target: Path
    method: Built | None
    replace: bool = False


def run(config: Config) -> int:
    root = config.required_root()
    folder = state.workdir(root)
    plan_path = folder / 'plan-rebuild.json'
    args = shared.snapshot(config)
    store = state.StateStore(folder)
    pending = store.pending()
    if not pending:
        log.note('没有待重建的条目')
        return EXIT_OK

    ask = plan.Interrogator(plan.answers(plan_path, 'rebuild', args))
    prober = Prober()
    actions: list[Action] = []
    for group in pending:
        actions.extend(_plan_group(group, root, config, ask, prober))

    copies = [action for action in actions if action.method is Built.COPY]
    if copies:
        size = sum(_size(action.source) for action in copies)
        gate = ask.confirm(
            plan.short_id('copy', *(str(action.target) for action in copies)),
            f'{len(copies)} 个成员无法用硬链接或符号链接重建，要改写成副本。\n'
            f'预估总大小 {size} 字节。确认吗？（yes/no）',
        )
        if gate is False:
            raise errors.ToolError('创建副本未获确认，本次未执行')

    if ask.questions:
        return shared.await_answers(plan_path, 'rebuild', args, ask)
    if not actions:
        log.note('没有可重建的条目')
        plan.discard(plan_path)
        return EXIT_OK

    created = _apply(actions, config, store)
    store.flush()
    plan.discard(plan_path)
    log.note(f'已重建 {created} 条路径')
    return EXIT_OK


def _plan_group(
    group: state.GroupRecord, root: Path, config: Config, ask: plan.Interrogator, prober: Prober
) -> list[Action]:
    members = [member for member in group.members if member.status == state.PENDING]
    if not members:
        return []
    source = shared.source_path(root, group)
    if source is None or not source.exists():
        answer = ask.fill(
            plan.short_id('missing', group.key),
            f'这一组的源已不存在：{group.source_rel or group.source_abs}\n'
            f'待重建 {len(members)} 条路径。请填一个本机路径作为替代源，或 skip 跳过该组。',
            (),
        )
        if answer is None or answer == 'skip':
            return []
        source = Path(answer)
        if not source.exists():
            log.warn(f'替代源不存在：{source}')
            return []

    if not is_within(source, root):
        gate = ask.confirm(
            plan.short_id('outside', group.key),
            f'重建的源在受管树之外：{source}\n确认以它作为源吗？（yes/no）',
        )
        if gate is None or not gate:
            return []

    actions: list[Action] = []
    for member in members:
        target = shared.member_path(root, member)
        method = _method(group, member, source, target, prober)
        if not os.path.lexists(target):
            actions.append(Action(group, member, source, target, method))
            continue
        if _satisfied(target, source, group):
            actions.append(Action(group, member, source, target, None))
            continue
        choice = ask.choose(
            plan.short_id('clash', member.label),
            f'重建目标已存在且内容不同：{member.label}\n'
            f'现内容与源 {source} 不一致。填 replace 覆盖它，或 skip 保留它。',
            (
                plan.Candidate(index=1, value='replace', label='replace 覆盖现有文件'),
                plan.Candidate(index=2, value='skip', label='skip 保留现有文件'),
            ),
        )
        if choice is None or choice == 'skip':
            continue
        actions.append(Action(group, member, source, target, method, replace=True))
    return actions


def _method(
    group: state.GroupRecord,
    member: state.MemberRecord,
    source: Path,
    target: Path,
    prober: Prober,
) -> Built:
    if group.dir_target is not None or member.kind is not Kind.FILE:
        return Built.SYMLINK
    folder = _nearest(target.parent)
    capabilities = prober.capabilities(folder)
    if capabilities.hardlink and _same_volume(source, folder):
        return Built.HARDLINK
    if capabilities.symlink:
        return Built.SYMLINK
    return Built.COPY


def _nearest(path: Path) -> Path:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def _same_volume(first: Path, second: Path) -> bool:
    try:
        return os.stat(first).st_dev == os.stat(second).st_dev
    except OSError:
        return False


def _satisfied(target: Path, source: Path, group: state.GroupRecord) -> bool:
    if target.is_symlink() or os.path.isjunction(target):
        resolved = Path(os.path.realpath(target))
        return plain(resolved) == plain(Path(os.path.realpath(source)))
    if group.identity is None:
        return False
    try:
        info = os.stat(target)
    except OSError:
        return False
    if info.st_size != group.identity.size:
        return False
    return hashing.full(info.st_size, target) == group.identity


def _size(source: Path) -> int:
    try:
        return os.stat(source).st_size
    except OSError:
        return 0


def _apply(actions: list[Action], config: Config, store: state.StateStore) -> int:
    created = 0
    for action in actions:
        member = action.member
        if action.method is None:
            store.record(action.group.key, member.label, state.REBUILT, None)
            continue
        action.target.parent.mkdir(parents=True, exist_ok=True)
        if action.replace:
            remove_path(action.target)
        match action.method:
            case Built.HARDLINK:
                create_hardlink(action.source, action.target)
            case Built.SYMLINK:
                text = link_text(action.source, action.target, config.anchors(), config.rel_mode)
                create_symlink(text, action.target, target_is_dir=_is_dir(action))
                set_times(action.target, member.mtime_ns, follow=False)
            case Built.COPY:
                copy_file(action.source, action.target, member.mtime_ns)
        store.record(action.group.key, member.label, state.REBUILT, str(action.method))
        created += 1
    return created


def _is_dir(action: Action) -> bool:
    return action.group.dir_target is not None or action.member.target_is_dir is True
