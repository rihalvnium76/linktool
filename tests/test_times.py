import json
import os
import sys

from pathlib import Path
from typing import cast

import pytest

from linktool.cli import main
from linktool.fs import Capabilities, Kind, Prober, birth_time, set_times

MTIME = 1_500_000_000_123_456_700
CREATED = 1_400_000_000_765_432_100
LINK_NAME = 'link.lnk'
WINDOWS = sys.platform == 'win32'


def call(*args: str) -> int:
    return main(list(args))


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _symlink(source: Path, target: Path) -> None:
    try:
        os.symlink(source, target)
    except OSError as error:
        pytest.skip(f'本机不能创建符号链接：{error}')


def _stamp(path: Path, *, kind: Kind, mtime_ns: int = MTIME, birth_ns: int = CREATED) -> None:
    set_times(path, mtime_ns, follow=kind is Kind.FILE, birth_ns=birth_ns if WINDOWS else None)


def _assert_stamped(
    path: Path, *, kind: Kind, mtime_ns: int = MTIME, birth_ns: int = CREATED
) -> None:
    info = os.stat(path) if kind is Kind.FILE else os.lstat(path)
    assert info.st_mtime_ns == mtime_ns
    if WINDOWS:
        assert birth_time(info) == birth_ns


def _answer_all(plan_path: Path, value: str) -> None:
    payload = cast(dict[str, object], json.loads(plan_path.read_text(encoding='utf-8')))
    questions = cast(list[dict[str, object]], payload['questions'])
    for question in questions:
        question['answer'] = value
    plan_path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')


def _capabilities(**flags: bool) -> object:
    def answer(self: Prober, folder: Path) -> Capabilities:
        return Capabilities(**flags)

    return answer


def _remove_and_rebuild(root: Path, monkeypatch: pytest.MonkeyPatch, **flags: bool) -> Path:
    monkeypatch.setattr(Prober, 'capabilities', _capabilities(**flags))
    args = ('remove', '--scan', str(root), '--prefer', str(root / 'keep'))
    assert call(*args) == 3
    _answer_all(root / '.linktool' / 'plan-remove.json', 'yes')
    assert call(*args) == 0

    if call('rebuild', '--state', str(root)) == 3:
        _answer_all(root / '.linktool' / 'plan-rebuild.json', 'yes')
        assert call('rebuild', '--state', str(root)) == 0
    return root / 'other' / 'b.bin'


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / 'tree'
    _write(root / 'keep' / 'a.bin', b'payload')
    _write(root / 'other' / 'b.bin', b'payload')
    os.utime(root / 'other' / 'b.bin', ns=(MTIME, MTIME))
    _stamp(root / 'other' / 'b.bin', kind=Kind.FILE)
    return root


def test_a_rebuilt_link_carries_the_removed_links_times(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _tree(tmp_path)
    rebuilt = _remove_and_rebuild(root, monkeypatch, hardlink=False, symlink=True)
    assert rebuilt.is_symlink()
    _assert_stamped(rebuilt, kind=Kind.SYMLINK)


def test_a_rebuilt_copy_carries_the_removed_files_times(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _tree(tmp_path)
    rebuilt = _remove_and_rebuild(root, monkeypatch, hardlink=False, symlink=False)
    assert not rebuilt.is_symlink()
    _assert_stamped(rebuilt, kind=Kind.FILE)


def test_stage_then_restore_keeps_a_files_times(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    staging = tmp_path / 'stage'
    _stage(root, staging)
    _assert_stamped(staging / 'files' / 'other' / 'b.bin', kind=Kind.FILE)

    assert call('restore', '--state', str(root)) == 0
    _assert_stamped(root / 'other' / 'b.bin', kind=Kind.FILE)


def test_stage_then_restore_keeps_a_links_times(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    staging = tmp_path / 'stage'
    _write(root / 'keep' / 'a.bin', b'payload')
    _symlink(root / 'keep' / 'a.bin', root / LINK_NAME)
    _stamp(root / LINK_NAME, kind=Kind.SYMLINK)
    _stage(root, staging)
    _assert_stamped(staging / 'files' / LINK_NAME, kind=Kind.SYMLINK)

    assert call('restore', '--state', str(root)) == 0
    _assert_stamped(root / LINK_NAME, kind=Kind.SYMLINK)


def _stage(root: Path, staging: Path) -> None:
    args = (
        'stage',
        '--scan',
        str(root),
        '--staging',
        str(staging),
        '--prefer',
        str(root / 'keep'),
    )
    assert call(*args) == 3
    _answer_all(root / '.linktool' / 'plan-stage.json', 'yes')
    assert call(*args) == 0
