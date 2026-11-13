from pathlib import Path

from linktool import EXIT_OK, log, state
from linktool.commands import shared
from linktool.config import Config
from linktool.paths import plain


def run(config: Config) -> int:
    '''Report what each record still holds, which decides whether its folder may go.'''
    owner = config.state
    folder = state.workdir(owner)
    store = state.StateStore(folder)
    _report_pending(folder, store)
    if not store.staging:
        log.note('这份记录没有暂存区')
        return EXIT_OK
    for area in store.staging:
        if not state.workdir(area).joinpath('staging.json').exists():
            log.note(f'{area}: 暂存区缺失，清单不在这个位置')
            continue
        held = state.StagingStore(state.workdir(area))
        if held.owner is None or plain(held.owner) != plain(owner):
            log.note(f'{area}: 清单属于 {held.owner}，不属于 {owner}')
            continue
        _report_staged(area, held)
    return EXIT_OK


def _report_pending(folder: Path, store: state.StateStore) -> None:
    pending = store.pending()
    members = sorted(
        (member for group in pending for member in group.members if member.status == state.PENDING),
        key=lambda member: _place(store, member),
    )
    if not members:
        log.note(f'{folder}: 没有待重建的条目')
        return
    log.note(f'{folder}: {len(members)} 条待重建，分布在 {len(pending)} 组')
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
        log.note(f'  {item.target}  {item.kind}')
