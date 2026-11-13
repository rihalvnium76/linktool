import os

from pathlib import Path

from linktool import EXIT_OK, log, plan, state
from linktool.commands import shared
from linktool.config import Config
from linktool.fs import Kind, Prober, existing_ancestor, move_file, move_link, remove_path
from linktool.paths import plain


def run(config: Config) -> int:
    '''Move back everything the record's staging areas hold, one area at a time.'''
    owner = config.state
    folder = state.workdir(owner)
    store = state.StateStore(folder)
    plan_path = folder / 'plan-restore.json'
    args = shared.snapshot(config)
    ask = plan.Interrogator(plan.answers(plan_path, 'restore', args))

    areas = _areas(store, owner)
    items = sorted(
        ((area, held, item) for area, held in areas for item in held.staged()),
        key=lambda found: found[2].target,
    )
    if not items:
        log.note('没有待还原的条目')
        return EXIT_OK

    actions: list[tuple[state.StagingStore, Path, state.StagedItem, Path, bool]] = []
    blocked: list[str] = []
    prober = Prober()
    for area, held, item in items:
        source = state.item_path(area, item)
        if not os.path.lexists(source):
            log.warn(f'暂存区 {area} 里缺少 {item.staged}，跳过')
            continue
        target = Path(item.target)
        if (
            item.kind is not Kind.FILE
            and not prober.capabilities(existing_ancestor(target.parent)).symlink
        ):
            blocked.append(item.target)
            continue
        replace = False
        if os.path.lexists(target):
            choice = ask.choose(
                plan.short_id('clash', item.target),
                f'原位置已经有 {item.target}。\n'
                '填 replace 覆盖它，或 skip 保留它并让这条留在暂存区。',
                (
                    plan.Candidate(index=1, value='replace', label='replace 覆盖原位置上的文件'),
                    plan.Candidate(index=2, value='skip', label='skip 保留原位置上的文件'),
                ),
            )
            if choice is None or choice == 'skip':
                continue
            replace = True
        actions.append((held, source, item, target, replace))

    if blocked:
        shared.confirm_unavailable(ask, blocked, verb='还原', keeping='留在暂存区')
    if ask.pending:
        return shared.await_answers(plan_path, 'restore', args, ask)

    for held, source, item, target, replace in actions:
        target.parent.mkdir(parents=True, exist_ok=True)
        if replace:
            remove_path(target)
        if item.kind is Kind.FILE:
            move_file(source, target)
        else:
            move_link(source, target)
        held.record(item.target, state.RESTORED)
    for _, held in areas:
        held.flush()
    plan.discard(plan_path)
    log.note(f'已还原 {len(actions)} 条路径')
    return EXIT_OK


def _areas(store: state.StateStore, owner: Path) -> list[tuple[Path, state.StagingStore]]:
    '''Every staging area the record names, refusing a record it cannot account for.'''
    if not store.staging:
        return []
    missing = [
        area for area in store.staging if not state.workdir(area).joinpath('staging.json').exists()
    ]
    if missing:
        raise state.RecordError(
            '记录里的暂存区找不到清单：\n'
            + '\n'.join(f'  {area}' for area in missing)
            + '\n把这些目录接回原来的位置后重跑；确实不再需要这些条目时，'
            '才删掉记录里的 staging 段。'
        )
    found: list[tuple[Path, state.StagingStore]] = []
    for area in store.staging:
        held = state.StagingStore(state.workdir(area))
        if held.owner is None or plain(held.owner) != plain(owner):
            raise state.RecordError(
                f'暂存区 {area} 的清单属于 {held.owner}，不属于 {owner}。\n'
                '用 --state 指定这份暂存区所属的状态目录后重跑。'
            )
        found.append((area, held))
    return found
