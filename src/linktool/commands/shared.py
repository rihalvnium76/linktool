from collections.abc import Mapping, Sequence
from pathlib import Path

from linktool import EXIT_PENDING, errors, log, plan, state
from linktool.config import Config
from linktool.paths import from_rel


def snapshot(config: Config) -> dict[str, str]:
    '''Every setting that decides a plan, so a stale plan cannot apply to other settings.'''
    return {
        'root': str(config.root or ''),
        'staging': str(config.staging or ''),
        'scan': ';'.join(str(path) for path in config.scan),
        'prefer': ';'.join(str(path) for path in config.prefer),
        'base_dir': str(config.base_dir or ''),
        'rel_mode': str(config.rel_mode),
        'pick': str(config.pick),
    }


def member_path(root: Path, member: state.MemberRecord) -> Path:
    if member.rel is not None:
        return from_rel(root, member.rel)
    if member.abs is None:
        raise state.RecordError(f'记录 {member.label} 既没有相对路径也没有绝对路径')
    return Path(member.abs)


def staged_path(staging: Path, member: state.MemberRecord) -> Path:
    bucket = staging / 'files'
    if member.rel is not None:
        return from_rel(bucket, member.rel)
    name = Path(member.abs or 'external').name
    return bucket / 'external' / plan.short_id(member.abs or '') / name


def source_path(root: Path, group: state.GroupRecord) -> Path | None:
    if group.source_rel is not None:
        return from_rel(root, group.source_rel)
    if group.source_abs is not None:
        return Path(group.source_abs)
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
    plan.save(plan_path, command, args, ask.questions, decisions, ask.settled)
    log.note(f'有 {len(ask.questions)} 项需要确认，已写入 {plan_path}')
    log.note('填好每一题的 answer 后，用同样的参数重新运行这条命令。')
    return EXIT_PENDING
