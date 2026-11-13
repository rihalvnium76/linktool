from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from linktool import log
from linktool.errors import ToolError
from linktool.paths import RelMode, is_within, plain


class ConfigError(ToolError):
    '''A required setting is missing or unusable.'''


class Pick(StrEnum):
    '''What to do when no rule settles a group's source.'''

    ASK = 'ask'
    FIRST = 'first'


@dataclass(frozen=True, slots=True)
class Config:
    '''One run's settings: where the record lives, and how paths are written into it.'''

    state: Path
    base: Path
    scan: tuple[Path, ...] = ()
    staging: tuple[Path, ...] = ()
    prefer: tuple[Path, ...] = ()
    rel_mode: RelMode = RelMode.BASE
    pick: Pick = Pick.ASK

    def required_stagings(self) -> tuple[Path, ...]:
        if not self.staging:
            raise ConfigError('缺少暂存区：用 --staging 指定')
        return self.staging


def resolve(
    *,
    state: str | None,
    base: str | None,
    scan: Sequence[str],
    staging: Sequence[str],
    prefer: Sequence[str],
    rel_mode: str | None,
    pick: str | None,
) -> Config:
    '''Resolve one run's settings, with relative paths against the working directory.

    The state directory holds the record; the base decides which paths the record holds
    relatively. A run that names one scan path needs neither, so both follow it.
    '''
    scans = _existing(scan, '扫描路径')
    state_dir = _state(state, scans)
    base_dir = _directory(base, '--base') if base is not None else state_dir
    areas = _named(staging)
    for area in areas:
        if plain(area) == plain(state_dir):
            raise ConfigError(f'暂存区不能就是状态目录：{area}')
        for path in scans:
            if is_within(area, path):
                raise ConfigError(f'暂存区不能位于扫描路径之内：{area} 在 {path} 里')
    return Config(
        state=state_dir,
        base=base_dir,
        scan=scans,
        staging=areas,
        prefer=_existing(prefer, '偏好目录'),
        rel_mode=RelMode(rel_mode) if rel_mode is not None else RelMode.BASE,
        pick=Pick(pick) if pick is not None else Pick.ASK,
    )


def _state(state: str | None, scans: Sequence[Path]) -> Path:
    if state is not None:
        return _directory(state, '--state')
    if len(scans) == 1:
        log.note(f'没有 --state，记录写在扫描路径 {scans[0]}')
        return scans[0]
    if not scans:
        raise ConfigError('缺少状态目录：用 --state 指定')
    raise ConfigError('给出了多个扫描路径，无法推断状态目录：用 --state 指定')


def _existing(items: Sequence[str], what: str) -> tuple[Path, ...]:
    found: list[Path] = []
    for item in items:
        path = _directory(item, what)
        if all(plain(path) != plain(known) for known in found):
            found.append(path)
    return tuple(found)


def _named(items: Sequence[str]) -> tuple[Path, ...]:
    '''Directories this run may create, so they only need to be distinct absolute paths.'''
    found: list[Path] = []
    for item in items:
        path = Path(item).absolute()
        if all(plain(path) != plain(known) for known in found):
            found.append(path)
    return tuple(found)


def _directory(text: str, what: str) -> Path:
    path = Path(text).absolute()
    if not path.is_dir():
        raise ConfigError(f'{what} 不是目录或不存在：{path}')
    return path
