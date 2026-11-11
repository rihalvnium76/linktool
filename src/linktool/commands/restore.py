import os

from pathlib import Path

from linktool import EXIT_OK, errors, log, plan, state
from linktool.commands import shared
from linktool.config import Config
from linktool.fs import Kind, Prober, existing_ancestor, move_file, move_link, remove_path


def run(config: Config) -> int:
    staging = config.required_staging()
    folder = state.workdir(staging)
    store = state.StagingStore(folder)
    root = config.root or store.root
    if root is None:
        raise errors.ToolError('暂存清单没有记录原始根目录，请用 --root 指定')
    plan_path = folder / 'plan-restore.json'
    args = shared.snapshot(config)
    ask = plan.Interrogator(plan.answers(plan_path, 'restore', args))

    items = sorted(store.staged(), key=lambda member: member.label)
    if not items:
        log.note('暂存区没有待还原的条目')
        return EXIT_OK

    actions: list[tuple[state.MemberRecord, Path, Path, bool]] = []
    blocked: list[str] = []
    prober = Prober()
    for item in items:
        source = shared.staged_path(staging, item)
        if not os.path.lexists(source):
            log.warn(f'暂存区里缺少 {item.label}，跳过')
            continue
        target = shared.member_path(root, item)
        if (
            item.kind is not Kind.FILE
            and not prober.capabilities(existing_ancestor(target.parent)).symlink
        ):
            blocked.append(item.label)
            continue
        replace = False
        if os.path.lexists(target):
            choice = ask.choose(
                plan.short_id('clash', item.label),
                f'原位置已经有 {item.label}。填 replace 覆盖它，或 skip 保留它并让这条留在暂存区。',
                (
                    plan.Candidate(index=1, value='replace', label='replace 覆盖原位置上的文件'),
                    plan.Candidate(index=2, value='skip', label='skip 保留原位置上的文件'),
                ),
            )
            if choice is None or choice == 'skip':
                continue
            replace = True
        actions.append((item, source, target, replace))

    if blocked:
        shared.confirm_unavailable(ask, blocked, verb='还原', keeping='留在暂存区')
    if ask.questions:
        return shared.await_answers(plan_path, 'restore', args, ask)

    for item, source, target, replace in actions:
        target.parent.mkdir(parents=True, exist_ok=True)
        if replace:
            remove_path(target)
        if item.kind is Kind.FILE:
            move_file(source, target)
        else:
            move_link(source, target)
        store.record(item.label, state.RESTORED)
    store.flush()
    plan.discard(plan_path)
    log.note(f'已还原 {len(actions)} 条路径')
    return EXIT_OK
