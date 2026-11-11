from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from linktool.errors import ToolError
from linktool.paths import RelMode


class ConfigError(ToolError):
    '''A required setting is missing or unusable.'''


class Pick(StrEnum):
    '''What to do when no rule settles a group's source.'''

    ASK = 'ask'
    FIRST = 'first'


@dataclass(frozen=True, slots=True)
class Config:
    root: Path | None = None
    staging: Path | None = None
    scan: tuple[Path, ...] = ()
    prefer: tuple[Path, ...] = ()
    base_dir: Path | None = None
    rel_mode: RelMode = RelMode.BASE
    pick: Pick = Pick.ASK

    def required_root(self) -> Path:
        if self.root is None:
            raise ConfigError('缺少受管树的根：用 --root 指定')
        return self.root

    def required_staging(self) -> Path:
        if self.staging is None:
            raise ConfigError('缺少暂存区：用 --staging 指定')
        return self.staging

    def scans(self) -> tuple[Path, ...]:
        return self.scan or (self.required_root(),)

    def anchors(self) -> Path:
        return self.base_dir if self.base_dir is not None else self.required_root()


def build(
    *,
    root: str | None,
    staging: str | None,
    scan: Sequence[str],
    prefer: Sequence[str],
    base_dir: str | None,
    rel_mode: str | None,
    pick: str | None,
) -> Config:
    '''Resolve the settings of one run, with relative paths against the working directory.'''
    return Config(
        root=_absolute(root),
        staging=_absolute(staging),
        scan=tuple(Path(item).absolute() for item in scan),
        prefer=tuple(Path(item).absolute() for item in prefer),
        base_dir=_absolute(base_dir),
        rel_mode=RelMode(rel_mode) if rel_mode is not None else RelMode.BASE,
        pick=Pick(pick) if pick is not None else Pick.ASK,
    )


def _absolute(text: str | None) -> Path | None:
    return Path(text).absolute() if text is not None else None
