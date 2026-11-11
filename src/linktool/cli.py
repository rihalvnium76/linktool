import argparse

from collections.abc import Sequence
from pathlib import Path

from linktool import EXIT_ERROR, config, log
from linktool.commands import dedupe, fix, initconfig, rebuild, restore
from linktool.errors import ToolError


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _dispatch(args)
    except ToolError as error:
        log.note(str(error))
        return EXIT_ERROR


def _dispatch(args: argparse.Namespace) -> int:
    command = args.command
    if command == 'init-config':
        return initconfig.run(Path(args.path) if args.path else None)
    settings = config.build(
        Path(args.config) if args.config else None,
        root=args.root,
        staging=getattr(args, 'staging', None),
        scan=args.scan,
        prefer=args.prefer,
        base_dir=args.base_dir,
        rel_mode=args.rel_mode,
    )
    match command:
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
            raise ToolError(f'未知命令 {command}')


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='linktool',
        description='移除、暂存、重建、还原重复文件与链接，并修复失效的符号链接。',
    )
    parser.add_argument('--version', action='version', version='linktool 0.1.0')
    commands = parser.add_subparsers(dest='command', required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--config', metavar='PATH', help='配置文件，默认工作目录下的 linktool.toml')
    common.add_argument('--root', metavar='PATH', help='受管树的根，状态文件写在 <root>/.linktool/')
    common.add_argument(
        '--scan', action='append', default=[], metavar='PATH', help='扫描路径，可重复'
    )
    common.add_argument(
        '--prefer', action='append', default=[], metavar='DIR', help='源偏好目录，可重复'
    )
    common.add_argument('--base-dir', metavar='PATH', help='相对路径转换的边界目录')
    common.add_argument(
        '--rel-mode',
        choices=['base', 'all', 'off'],
        help='链接指向的写法：base 只把 base_dir 内的转相对，all 全部尝试，off 保留绝对',
    )

    staging_option = argparse.ArgumentParser(add_help=False)
    staging_option.add_argument('--staging', metavar='PATH', help='暂存区目录，必须在受管树之外')

    commands.add_parser('remove', parents=[common], help='移除重复文件与链接，保留每组的源')
    commands.add_parser(
        'stage', parents=[common, staging_option], help='把重复文件与链接移入暂存区'
    )
    commands.add_parser('rebuild', parents=[common], help='按状态文件重建被移除的文件与链接')
    commands.add_parser(
        'restore', parents=[common, staging_option], help='把暂存区里的条目移回原位'
    )

    commands.add_parser('fix', parents=[common], help='修复因源被移动而失效的符号链接')

    template = commands.add_parser('init-config', help='写出带注释的配置模板')
    template.add_argument('--path', metavar='PATH', help='模板路径，默认 linktool.toml')
    return parser
