import argparse

from collections.abc import Sequence
from typing import cast

from linktool import EXIT_ERROR, config, log
from linktool.commands import dedupe, fix, rebuild, restore, status
from linktool.errors import ToolError

STATE_HELP = '记录所在目录，记录写在 <state>/.linktool/；缺省时取唯一的 --scan 路径'
BASE_HELP = '记录与链接的相对基准，缺省时取 --state'


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _dispatch(args)
    except ToolError as error:
        log.note(str(error))
        return EXIT_ERROR


def _dispatch(args: argparse.Namespace) -> int:
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
        case 'status':
            return status.run(settings)
        case _:
            raise ToolError(f'未知命令 {args.command}')


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
    parser.add_argument('--state', metavar='PATH', required=state_required, help=STATE_HELP)
    if base:
        parser.add_argument('--base', metavar='PATH', help=BASE_HELP)
    return parser


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='linktool',
        description='移除、暂存、重建、还原重复文件与链接，并修复失效的符号链接。',
    )
    parser.add_argument('--version', action='version', version='linktool 0.1.0')
    commands = parser.add_subparsers(dest='command', required=True)

    scanning = argparse.ArgumentParser(add_help=False)
    scanning.add_argument(
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
        '--rel-mode',
        choices=['base', 'all', 'off'],
        help='链接指向的写法：base 只把基准目录内的转相对，all 全部尝试，off 保留绝对',
    )

    staging = argparse.ArgumentParser(add_help=False)
    staging.add_argument(
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
    commands.add_parser(
        'status',
        parents=[_addressing(state_required=True, base=False)],
        help='列出待重建与待还原的条目',
    )
    return parser
