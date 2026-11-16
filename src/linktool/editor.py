'''Opening the answer sheet in the editor the environment names.'''

import os
import shlex
import subprocess
import sys

from pathlib import Path

from linktool import log
from linktool.config import OpenMode


def open_sheet(sheet: Path, mode: OpenMode, *, first_time: bool) -> None:
    '''Open the sheet beside the plan, unless the mode or the surroundings say otherwise.

    A sheet someone has to fill is worth putting in front of them, while a piped run, a
    machine without a named editor, or an unasked mode gets only the printed path.
    '''
    if mode is OpenMode.NEVER or (mode is OpenMode.ONCE and not first_time):
        return
    if not sys.stdout.isatty():
        return
    command = os.environ.get('VISUAL') or os.environ.get('EDITOR')
    if not command:
        return
    try:
        subprocess.Popen(  # noqa: S603  the environment names the program to run
            [*split_command(command), str(sheet)],
        )
    except OSError as error:
        log.warn(f'编辑器没能打开：{error}')


def split_command(command: str) -> list[str]:
    '''The editor command split the way its own platform writes command lines.'''
    if os.name == 'nt':
        return [_bare(part) for part in shlex.split(command, posix=False)]
    return shlex.split(command)


def _bare(part: str) -> str:
    '''A token shlex kept the quotes around, which the command line needed and None does not.'''
    if len(part) > 1 and part[0] == part[-1] == '"':
        return part[1:-1]
    return part
