import json
import os

from pathlib import Path

import pytest

from linktool.cli import main
from linktool.fs import Capabilities, Prober

MTIME_NEW = 1_600_000_000_000_000_000


def call(*args: str) -> int:
    return main(list(args))


def _write(path: Path, content: bytes, mtime_ns: int = MTIME_NEW) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, ns=(mtime_ns, mtime_ns))


def _symlink(source: Path, target: Path, *, is_dir: bool = False) -> None:
    try:
        os.symlink(source, target, target_is_directory=is_dir)
    except OSError as error:
        pytest.skip(f'本机不能创建符号链接：{error}')


def _answer(plan_path: Path, value: str) -> None:
    payload = json.loads(plan_path.read_text(encoding='utf-8'))
    payload['questions'][0]['answer'] = value
    plan_path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')


def _no_links(self: Prober, folder: Path) -> Capabilities:
    return Capabilities(hardlink=False, symlink=True)


def test_fix_follows_the_content_recorded_in_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Prober, 'capabilities', _no_links)
    root = tmp_path / 'tree'
    _write(root / 'keep' / 'a.bin', b'payload')
    _write(root / 'other' / 'b.bin', b'payload')

    assert call('remove', '--root', str(root), '--prefer', str(root / 'keep')) == 0
    assert call('rebuild', '--root', str(root)) == 0
    link = root / 'other' / 'b.bin'
    assert link.is_symlink()

    (root / 'keep' / 'a.bin').rename(root / 'keep' / 'moved.bin')
    assert not link.exists()

    assert call('fix', '--root', str(root)) == 0
    assert link.exists()
    assert link.resolve() == (root / 'keep' / 'moved.bin').resolve()


def test_fix_offers_a_same_name_candidate(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'store' / 'moved.bin', b'payload')
    _symlink(root / 'gone' / 'moved.bin', root / 'broken.lnk')

    plan_path = root / '.linktool' / 'plan-fix.json'
    assert call('fix', '--root', str(root)) == 3
    payload = json.loads(plan_path.read_text(encoding='utf-8'))
    assert payload['questions'][0]['kind'] == 'fill'

    _answer(plan_path, '1')
    assert call('fix', '--root', str(root)) == 0
    assert (root / 'broken.lnk').resolve() == (root / 'store' / 'moved.bin').resolve()


def test_fix_reports_nothing_to_do(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'a.bin', b'payload')
    assert call('fix', '--root', str(root)) == 0
