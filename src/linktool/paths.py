import os
import sys

from enum import StrEnum
from pathlib import Path

VERBATIM = '\\\\?\\'
VERBATIM_UNC = '\\\\?\\UNC\\'


class RelMode(StrEnum):
    BASE = 'base'
    ALL = 'all'
    OFF = 'off'


def strip_verbatim(text: str) -> str:
    '''Drop the extended-length prefix Windows adds to absolute symlink targets.'''
    if text.startswith(VERBATIM_UNC):
        return '\\\\' + text[len(VERBATIM_UNC) :]
    if text.startswith(VERBATIM):
        return text[len(VERBATIM) :]
    return text


def verbatim(path: Path) -> str:
    '''The extended-length form of an absolute path, which lifts the length limit.'''
    text = os.path.abspath(path)
    if text.startswith(VERBATIM):
        return text
    if text.startswith('\\\\'):
        return VERBATIM_UNC + text[2:]
    return VERBATIM + text


def read_link(path: Path) -> str:
    return strip_verbatim(os.readlink(path))


def plain(path: Path) -> str:
    '''Comparison key for a path: absolute, case-folded where the platform folds case.'''
    return os.path.normcase(os.path.abspath(path))


def is_within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath([plain(path), plain(root)]) == plain(root)
    except ValueError:
        return False


def volume_key(path: Path) -> str:
    '''A key naming the volume that holds `path`, so two paths can be compared.

    Windows reports the same volume number for every drive's own root, so a drive letter
    names the volume instead.
    '''
    if sys.platform == 'win32':
        return os.path.splitdrive(plain(path))[0]
    return str(os.lstat(path).st_dev)


def same_volume(first: Path, second: Path) -> bool:
    '''Whether both paths sit on one volume, where a rename and a relative path work.'''
    try:
        return volume_key(first) == volume_key(second)
    except OSError:
        return False


def to_rel(path: Path, root: Path) -> str:
    '''Relative path with forward slashes, so a recorded path survives a move to POSIX.'''
    return os.path.relpath(path, root).replace(os.sep, '/')


def from_rel(root: Path, rel: str) -> Path:
    '''The path a recorded relative path names, with its steps above the root collapsed.'''
    return Path(os.path.normpath(root.joinpath(*rel.split('/'))))


def link_text(target: Path, link: Path, base_dir: Path, mode: RelMode) -> str:
    if mode is RelMode.OFF:
        return str(target)
    if mode is RelMode.BASE and not (is_within(target, base_dir) and is_within(link, base_dir)):
        return str(target)
    try:
        return os.path.relpath(target, link.parent)
    except ValueError:
        return str(target)
