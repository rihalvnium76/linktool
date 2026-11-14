import argparse
import sys

from collections.abc import Sequence
from importlib import metadata
from pathlib import Path
from typing import Any, cast

from linktool import EXIT_ERROR, config, log, source_digest
from linktool.commands import dedupe, fix, rebuild, restore, status
from linktool.errors import ToolError

STATE_HELP = '记录所在目录，记录写在 <state>/.linktool/；缺省时取唯一的 --scan 路径'
BASE_HELP = '记录与链接的相对基准，缺省时取 --state'


class _Version(argparse.Action):
    '''Prints the version and the source digest, which only this option pays for.'''

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace | None,
        values: str | Sequence[Any] | None,  # Any: argparse declares its own values so
        option_string: str | None = None,
    ) -> None:
        sys.stdout.write(f'{_identity()}\n')
        parser.exit()


def _identity() -> str:
    '''The installed version and the source digest; a copy run from `src` has no metadata.'''
    try:
        installed = metadata.version('linktool')
    except metadata.PackageNotFoundError:
        return f'linktool ({source_digest()})'
    return f'linktool {installed} ({source_digest()})'


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _dispatch(args)
    except ToolError as error:
        log.note(str(error))
        return EXIT_ERROR


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == 'status':
        return status.run(_container(args.container))
    settings = _settings(args)
    match args.command:
        case 'remove':
            return dedupe.run(settings, stage=False)
        case 'stage':
            return dedupe.run(settings, stage=True)
        case 'rebuild':
            return rebuild.run(settings)
        case 'restore':
            return restore.run(settings)
        case 'fix':
            return fix.run(settings)
        case _:
            raise ToolError(f'未知命令 {args.command}')


def _container(text: str | None) -> Path:
    '''The one container a status run reads, defaulting to the working directory.'''
    return Path.cwd() if text is None else Path(text).absolute()


def _settings(args: argparse.Namespace) -> config.Config:
    return config.resolve(
        state=_one(args, 'state'),
        base=_one(args, 'base'),
        scan=_many(args, 'scan'),
        staging=_many(args, 'staging'),
        prefer=_many(args, 'prefer'),
        rel_mode=_one(args, 'rel_mode'),
        pick=_one(args, 'pick'),
    )


def _one(args: argparse.Namespace, name: str) -> str | None:
    value = getattr(args, name, None)
    return value if isinstance(value, str) else None


def _many(args: argparse.Namespace, name: str) -> tuple[str, ...]:
    value = getattr(args, name, None)
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in cast(list[object], value))


def _addressing(*, state_required: bool, base: bool) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('-S', '--state', metavar='PATH', required=state_required, help=STATE_HELP)
    if base:
        parser.add_argument('-b', '--base', metavar='PATH', help=BASE_HELP)
    return parser


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='linktool',
        description='移除、暂存、重建、还原重复文件与链接，并修复失效的符号链接。',
    )
    parser.add_argument('-V', '--version', action=_Version, nargs=0, help='版本与源码摘要')
    commands = parser.add_subparsers(dest='command', required=True)

    scanning = argparse.ArgumentParser(add_help=False)
    scanning.add_argument(
        '-s',
        '--scan',
        action='extend',
        nargs='+',
        required=True,
        default=[],
        metavar='PATH',
        help='扫描路径，可给多个或重复给出',
    )

    sources = argparse.ArgumentParser(add_help=False)
    sources.add_argument(
        '-p',
        '--prefer',
        action='extend',
        nargs='+',
        default=[],
        metavar='DIR',
        help='源偏好目录，可给多个或重复给出',
    )
    sources.add_argument(
        '--pick',
        choices=['ask', 'first'],
        help='没有规则能定源时：ask 逐组提问，first 按路径排序取最前',
    )

    linking = argparse.ArgumentParser(add_help=False)
    linking.add_argument(
        '-r',
        '--rel-mode',
        choices=['base', 'all', 'off'],
        help='链接指向的写法：base 只把基准目录内的转相对，all 全部尝试，off 保留绝对',
    )

    staging = argparse.ArgumentParser(add_help=False)
    staging.add_argument(
        '-x',
        '--staging',
        action='extend',
        nargs='+',
        required=True,
        default=[],
        metavar='PATH',
        help='暂存区目录，可给多个；成员优先放进与它同卷的那个',
    )

    commands.add_parser(
        'remove',
        parents=[_addressing(state_required=False, base=True), scanning, sources],
        help='移除重复文件与链接，保留每组的源',
    )
    commands.add_parser(
        'stage',
        parents=[_addressing(state_required=False, base=True), scanning, sources, staging],
        help='把重复文件与链接移入暂存区',
    )
    commands.add_parser(
        'rebuild',
        parents=[_addressing(state_required=True, base=True), linking],
        help='按记录重建被移除的文件与链接',
    )
    commands.add_parser(
        'restore',
        parents=[_addressing(state_required=True, base=False)],
        help='把暂存区里的条目移回原位',
    )
    commands.add_parser(
        'fix',
        parents=[_addressing(state_required=False, base=True), scanning, linking],
        help='修复因源被移动而失效的符号链接',
    )
    status_parser = commands.add_parser(
        'status',
        help='报出状态区或暂存区里还剩什么',
    )
    status_parser.add_argument(
        'container',
        nargs='?',
        metavar='DIR',
        help='状态区或暂存区的容器目录，缺省时取当前工作目录',
    )
    return parser
