import os

from collections.abc import Mapping, Sequence
from pathlib import Path

from linktool import EXIT_PENDING, errors, log, plan, state
from linktool.config import Config
from linktool.paths import from_rel, is_within, plain, to_rel


def snapshot(config: Config) -> dict[str, str]:
    '''Every setting that decides a plan, so a stale plan cannot apply to other settings.'''
    return {
        'state': str(config.state),
        'base': str(config.base),
        'scan': ';'.join(str(path) for path in config.scan),
        'staging': ';'.join(str(path) for path in config.staging),
        'prefer': ';'.join(str(path) for path in config.prefer),
        'rel_mode': str(config.rel_mode),
        'pick': str(config.pick),
    }


def record_place(base: Path, path: Path) -> tuple[str | None, str | None]:
    '''How the record holds one path: relative to the base, or absolute.'''
    if is_within(path, base):
        return to_rel(path, base), None
    return None, str(path)


def record_label(base: Path, path: Path) -> str:
    rel, absolute = record_place(base, path)
    return rel if rel is not None else (absolute or '')


def marker(base: Path, path: Path) -> state.Marker:
    rel, absolute = record_place(base, path)
    return state.Marker(rel=rel, abs=absolute)


def member_path(base: Path, member: state.MemberRecord) -> Path:
    if member.rel is not None:
        return from_rel(base, member.rel)
    if member.abs is None:
        raise state.RecordError(f'记录 {member.label} 既没有相对路径也没有绝对路径')
    return Path(member.abs)


def source_path(base: Path, group: state.GroupRecord) -> Path | None:
    source = group.source
    if source is None:
        return None
    if source.rel is not None:
        return from_rel(base, source.rel)
    return Path(source.abs) if source.abs is not None else None


def effective_base(store: state.StateStore, config: Config, ask: plan.Interrogator) -> Path | None:
    '''The base this run writes and reads record paths against.

    A record written with its base equal to its state directory follows whichever state
    directory this run names, since its relative paths never depended on a parent. Any
    other record keeps its own base unless this run's base is the same; when the two
    differ, only the user can say which one the relative paths were written against.
    '''
    recorded = store.base
    if recorded is None:
        return config.base
    if store.state is not None and plain(store.state) == plain(recorded):
        if plain(config.base) != plain(config.state):
            log.note(
                f'记录以状态目录 {recorded} 为基准，按本机的 {config.state} 解析记录；'
                f'--base {config.base} 只影响新写入的路径与链接文本'
            )
        return config.state
    if plain(recorded) == plain(config.base):
        return recorded
    choice = ask.choose(
        plan.short_id('base', str(recorded), str(config.base)),
        f'这份记录写于基准 {recorded}，本次推导出的基准是 {config.base}。\n'
        '相对路径按哪个基准解析？',
        (
            plan.Candidate(index=1, value='recorded', label=f'{recorded}  记录里的基准'),
            plan.Candidate(index=2, value='given', label=f'{config.base}  本次的基准'),
        ),
    )
    if choice is None:
        return None
    base = recorded if choice == 'recorded' else config.base
    if not base.is_dir():
        raise state.RecordError(
            f'作为基准的目录不存在：{base}\n'
            f'记录里写的是 {recorded}，本次推导出的是 {config.base}。\n'
            '用 --base 指定本机对应的基准后重跑。'
        )
    return base


def scan_gate(ask: plan.Interrogator, scans: Sequence[Path], base: Path) -> bool | None:
    '''Ask before scanning paths the record would hold as absolute paths.'''
    outside = [path for path in scans if not is_within(path, base)]
    if not outside:
        return True
    return ask.confirm(
        plan.short_id('scan', *(str(path) for path in outside)),
        '这些扫描路径在基准目录之外：\n'
        + '\n'.join(f'  {path}' for path in outside)
        + '\n它们名下的路径在记录里会写成绝对路径，换一份记录或换一台机器后可能指向'
        '不存在的位置。确认扫描吗？（yes/no）',
    )


def staging_area(areas: Sequence[Path], target: Path) -> Path:
    '''The area that holds `target`: the first on its volume, else the first given.'''
    device = _device(target)
    if device is not None:
        for area in areas:
            if _device(area) == device:
                return area
    return areas[0]


def staged_path(owner: Path, base: Path, areas: Sequence[Path], member: state.MemberRecord) -> Path:
    target = member_path(base, member)
    area = staging_area(areas, target)
    return state.staged_location(owner, area, target)


def _device(path: Path) -> int | None:
    try:
        return os.stat(path).st_dev
    except OSError:
        return None


def confirm_unavailable(
    ask: plan.Interrogator, labels: Sequence[str], *, verb: str, keeping: str
) -> None:
    '''Ask whether the members whose volume refuses links may be skipped.

    A link has no form that a volume without symlink support can hold, so the members
    either stay where they are or the whole run stops.
    '''
    gate = ask.confirm(
        plan.short_id('unavailable', *labels),
        f'{len(labels)} 条链接的目标卷不支持符号链接，无法{verb}：\n'
        + '\n'.join(labels)
        + f'\n这些成员会{keeping}，其余成员继续。确认吗？（yes/no）',
    )
    if gate is False:
        raise errors.ToolError('跳过未获确认，本次未执行')


def await_answers(
    plan_path: Path,
    command: str,
    args: Mapping[str, str],
    ask: plan.Interrogator,
    decisions: Sequence[Mapping[str, object]] = (),
) -> int:
    plan.save(plan_path, command, args, ask, decisions)
    log.note(f'有 {len(ask.pending)} 项需要确认，答案写在 {plan.sheet(plan_path)}')
    log.note('填好每一题后，用同样的参数重新运行这条命令。')
    return EXIT_PENDING
