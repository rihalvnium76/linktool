from linktool import EXIT_OK, log, state
from linktool.config import Config


def run(config: Config) -> int:
    '''Report what each record still holds, which decides whether its folder may go.'''
    root = config.required_root()
    folder = state.workdir(root)
    pending = state.StateStore(folder).pending()
    members = sorted(
        (member for group in pending for member in group.members if member.status == state.PENDING),
        key=lambda member: member.label,
    )
    if members:
        log.note(f'{folder}: {len(members)} 条待重建，分布在 {len(pending)} 组')
        for member in members:
            log.note(f'  {member.label}  {member.kind}')
    else:
        log.note(f'{folder}: 没有待重建的条目')

    if config.staging is not None:
        staging_folder = state.workdir(config.staging)
        staged = sorted(
            state.StagingStore(staging_folder).staged(), key=lambda member: member.label
        )
        if staged:
            log.note(f'{staging_folder}: {len(staged)} 条待还原')
            for member in staged:
                log.note(f'  {member.label}')
        else:
            log.note(f'{staging_folder}: 没有待还原的条目')
    return EXIT_OK
