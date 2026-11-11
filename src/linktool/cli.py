import argparse

from collections.abc import Sequence
from typing import cast

from linktool import EXIT_ERROR, config, log
from linktool.commands import dedupe, fix, rebuild, restore
from linktool.errors import ToolError


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
        case _:
            raise ToolError(f'未知命令 {args.command}')


def _settings(args: argparse.Namespace) -> config.Config:
    return config.build(
        root=_one(args, 'root'),
        staging=_one(args, 'staging'),
        scan=_many(args, 'scan'),
        prefer=_many(args, 'prefer'),
        base_dir=_one(args, 'base_dir'),
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='linktool',
        description='移除、暂存、重建、还原重复文件与链接，并修复失效的符号链接。',
    )
    parser.add_argument('--version', action='version', version='linktool 0.1.0')
    commands = parser.add_subparsers(dest='command', required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--root', metavar='PATH', help='受管树的根，状态文件写在 <root>/.linktool/')

    scanning = argparse.ArgumentParser(add_help=False)
    scanning.add_argument(
        '--scan',
        action='extend',
        nargs='+',
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
    linking.add_argument('--base-dir', metavar='PATH', help='相对路径转换的边界目录')
    linking.add_argument(
        '--rel-mode',
        choices=['base', 'all', 'off'],
        help='链接指向的写法：base 只把 base_dir 内的转相对，all 全部尝试，off 保留绝对',
    )

    staging = argparse.ArgumentParser(add_help=False)
    staging.add_argument('--staging', metavar='PATH', help='暂存区目录，必须在受管树之外')

    commands.add_parser(
        'remove', parents=[common, scanning, sources], help='移除重复文件与链接，保留每组的源'
    )
    commands.add_parser(
        'stage', parents=[common, scanning, sources, staging], help='把重复文件与链接移入暂存区'
    )
    commands.add_parser(
        'rebuild', parents=[common, linking], help='按状态文件重建被移除的文件与链接'
    )
    commands.add_parser('restore', parents=[common, staging], help='把暂存区里的条目移回原位')
    commands.add_parser(
        'fix', parents=[common, scanning, linking], help='修复因源被移动而失效的符号链接'
    )
    return parser
