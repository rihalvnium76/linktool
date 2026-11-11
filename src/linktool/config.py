import tomllib

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from linktool import jsonio
from linktool.errors import ToolError
from linktool.paths import RelMode

DEFAULT_FILE = 'linktool.toml'


class ConfigError(ToolError):
    '''A required setting is missing or unusable.'''


@dataclass(frozen=True, slots=True)
class Config:
    root: Path | None = None
    staging: Path | None = None
    scan: tuple[Path, ...] = ()
    prefer: tuple[Path, ...] = ()
    base_dir: Path | None = None
    rel_mode: RelMode = RelMode.BASE

    def required_root(self) -> Path:
        if self.root is None:
            raise ConfigError(f'缺少受管树的根：用 --root 指定，或写进 {DEFAULT_FILE} 的 root')
        return self.root

    def required_staging(self) -> Path:
        if self.staging is None:
            raise ConfigError(f'缺少暂存区：用 --staging 指定，或写进 {DEFAULT_FILE} 的 staging')
        return self.staging

    def scans(self) -> tuple[Path, ...]:
        return self.scan or (self.required_root(),)

    def anchors(self) -> Path:
        return self.base_dir if self.base_dir is not None else self.required_root()


def load_file(path: Path) -> Config:
    try:
        raw = path.read_text(encoding='utf-8')
    except OSError as error:
        raise ConfigError(f'读不到配置文件 {path}: {error.strerror}') from error
    source = jsonio.mapping(tomllib.loads(raw), str(path))
    return Config(
        root=_maybe_path(source, 'root'),
        staging=_maybe_path(source, 'staging'),
        scan=_paths(source, 'scan'),
        prefer=_paths(source, 'prefer'),
        base_dir=_maybe_path(source, 'base_dir'),
        rel_mode=_rel_mode(source),
    )


def build(
    config_path: Path | None,
    *,
    root: str | None,
    staging: str | None,
    scan: Sequence[str],
    prefer: Sequence[str],
    base_dir: str | None,
    rel_mode: str | None,
) -> Config:
    if config_path is None:
        guess = Path(DEFAULT_FILE)
        base = load_file(guess) if guess.exists() else Config()
    else:
        base = load_file(config_path)

    chosen = base
    if root is not None:
        chosen = replace(chosen, root=Path(root))
    if staging is not None:
        chosen = replace(chosen, staging=Path(staging))
    if scan:
        chosen = replace(chosen, scan=tuple(Path(item) for item in scan))
    if prefer:
        chosen = replace(chosen, prefer=tuple(Path(item) for item in prefer))
    if base_dir is not None:
        chosen = replace(chosen, base_dir=Path(base_dir))
    if rel_mode is not None:
        chosen = replace(chosen, rel_mode=RelMode(rel_mode))

    return Config(
        root=_absolute(chosen.root),
        staging=_absolute(chosen.staging),
        scan=tuple(item.absolute() for item in chosen.scan),
        prefer=tuple(item.absolute() for item in chosen.prefer),
        base_dir=_absolute(chosen.base_dir),
        rel_mode=chosen.rel_mode,
    )


def _absolute(path: Path | None) -> Path | None:
    return path.absolute() if path is not None else None


def _maybe_path(source: dict[str, object], key: str) -> Path | None:
    value = jsonio.maybe_text(source, key)
    return Path(value) if value is not None else None


def _paths(source: dict[str, object], key: str) -> tuple[Path, ...]:
    value = source.get(key)
    if value is None:
        return ()
    return tuple(Path(str(item)) for item in jsonio.sequence(value, key))


def _rel_mode(source: dict[str, object]) -> RelMode:
    value = jsonio.maybe_text(source, 'rel_mode')
    if value is None:
        return RelMode.BASE
    try:
        return RelMode(value)
    except ValueError as error:
        raise ConfigError('配置项 rel_mode 只能是 base、all 或 off') from error


TEMPLATE = '''\
# linktool 配置模板。命令行参数会覆盖这里的同名设置。
# 相对路径按运行时的当前工作目录解析。

# 受管树的根。状态文件写在 <root>/.linktool/，记录的路径都相对于它。
# root = 'D:/tree'

# 暂存区。必须位于 root 之外，否则它会被复制工具一起搬走。
# staging = 'H:/stage'

# 扫描路径，可以是文件。留空表示就是 root。
# scan = ['D:/tree']

# 源偏好目录：同一组里落在这些目录下且唯一的那一个会成为源。
# prefer = ['D:/lib', 'D:/media']

# 相对路径转换的边界目录，默认等于 root。
# base_dir = 'D:/'

# 链接指向的写法：base 只在 base_dir 内转相对路径，all 全部尝试，off 保留绝对路径。
# rel_mode = 'base'
'''
