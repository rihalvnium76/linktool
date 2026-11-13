import os

from pathlib import Path

from linktool import EXIT_OK, log, state
from linktool.commands import shared
from linktool.errors import ToolError
from linktool.paths import plain


def run(container: Path) -> int:
    '''Report what one container still holds, which decides whether it may go.

    A state directory reports the members waiting for a rebuild and each area it recorded;
    a staging area reports the tree it belongs to and the members waiting for a restore.
    '''
    folder = state.workdir(container)
    record = folder / 'state.json'
    manifest = folder / 'staging.json'
    if not record.exists() and not manifest.exists():
        raise ToolError(f'{folder} 里没有状态记录：状态区要有 state.json，暂存区要有 staging.json')
    if record.exists():
        _report_state(container, state.StateStore(folder))
    if manifest.exists():
        held = state.StagingStore(folder)
        log.note(f'{container}: 暂存区，属于 {held.owner}')
        _report_staged(container, held)
    return EXIT_OK


def _report_state(container: Path, store: state.StateStore) -> None:
    _report_pending(container, store)
    if not store.staging:
        log.note('这份记录没有暂存区')
        return
    for area in store.staging:
        if not state.workdir(area).joinpath('staging.json').exists():
            log.note(f'{area}: 暂存区缺失，清单不在这个位置')
            continue
        held = state.StagingStore(state.workdir(area))
        if held.owner is None or plain(held.owner) != plain(container):
            log.note(f'{area}: 清单属于 {held.owner}，不属于 {container}')
            continue
        _report_staged(area, held)


def _report_pending(container: Path, store: state.StateStore) -> None:
    pending = store.pending()
    members = sorted(
        (member for group in pending for member in group.members if member.status == state.PENDING),
        key=lambda member: _place(store, member),
    )
    if not members:
        log.note(f'{container}: 没有待重建的条目')
        return
    log.note(f'{container}: {len(members)} 条待重建，分布在 {len(pending)} 组')
    for member in members:
        log.note(f'  {_place(store, member)}  {member.kind}')


def _place(store: state.StateStore, member: state.MemberRecord) -> str:
    '''The path a person can act on, resolved against the record's own base.'''
    if store.base is None:
        return member.label
    return str(shared.member_path(store.base, member))


def _report_staged(area: Path, held: state.StagingStore) -> None:
    staged = sorted(held.staged(), key=lambda item: item.target)
    if not staged:
        log.note(f'{area}: 没有待还原的条目')
        return
    log.note(f'{area}: {len(staged)} 条待还原')
    for item in staged:
        note = '' if os.path.lexists(state.item_path(area, item)) else '  在暂存区里找不到'
        log.note(f'  {item.target}  {item.kind}{note}')
