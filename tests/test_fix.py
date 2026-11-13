import json
import os
import re

from pathlib import Path

import pytest

from linktool.cli import main
from linktool.commands import fix
from linktool.fs import Capabilities, Prober
from sheet import questions as _questions

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


def _sheet(plan_path: Path) -> Path:
    return plan_path.with_suffix('.toml')


def _answer(plan_path: Path, value: str) -> None:
    sheet = _sheet(plan_path)
    lines = sheet.read_text(encoding='utf-8').splitlines()
    for index, line in enumerate(lines):
        found = re.match(r"^'([^']+)' = ", line)
        if found is not None:
            lines[index] = f"'{found.group(1)}' = '{value}'"
            break
    sheet.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def _no_links(self: Prober, folder: Path) -> Capabilities:
    return Capabilities(hardlink=False, symlink=True)


def test_fix_follows_the_content_recorded_in_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Prober, 'capabilities', _no_links)
    root = tmp_path / 'tree'
    _write(root / 'keep' / 'a.bin', b'payload')
    _write(root / 'other' / 'b.bin', b'payload')

    args = ('remove', '--scan', str(root), '--prefer', str(root / 'keep'))
    assert call(*args) == 3
    plan_path = root / '.linktool' / 'plan-remove.json'
    _answer(plan_path, 'yes')
    assert call(*args) == 0
    assert call('rebuild', '--state', str(root)) == 0
    link = root / 'other' / 'b.bin'
    assert link.is_symlink()

    (root / 'keep' / 'a.bin').rename(root / 'keep' / 'moved.bin')
    assert not link.exists()

    assert call('fix', '--scan', str(root)) == 0
    assert link.exists()
    assert link.resolve() == (root / 'keep' / 'moved.bin').resolve()


def test_fix_offers_a_same_name_candidate(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'store' / 'moved.bin', b'payload')
    _symlink(root / 'gone' / 'moved.bin', root / 'broken.lnk')

    plan_path = root / '.linktool' / 'plan-fix.json'
    assert call('fix', '--scan', str(root)) == 3
    payload = json.loads(plan_path.read_text(encoding='utf-8'))
    assert 'questions' not in payload
    assert [question.kind for question in _questions(plan_path)] == ['fill']

    _answer(plan_path, '1')
    assert call('fix', '--scan', str(root)) == 0
    assert (root / 'broken.lnk').resolve() == (root / 'store' / 'moved.bin').resolve()


def test_fix_reports_nothing_to_do(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'a.bin', b'payload')
    assert call('fix', '--scan', str(root)) == 0


def test_a_crash_mid_relink_leaves_the_path_in_the_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / 'tree'
    _write(root / 'store' / 'moved.bin', b'payload')
    _symlink(root / 'gone' / 'moved.bin', root / 'broken.lnk')
    journal = root / '.linktool' / 'state.jsonl'
    link_mtime = os.lstat(root / 'broken.lnk').st_mtime_ns

    plan_path = root / '.linktool' / 'plan-fix.json'
    assert call('fix', '--scan', str(root)) == 3
    _answer(plan_path, '1')

    failing = [True]
    original = fix.create_symlink

    def maybe_crash(text: str, path: Path, *, target_is_dir: bool) -> None:
        if failing[0]:
            raise OSError(5, '模拟中断')
        original(text, path, target_is_dir=target_is_dir)

    monkeypatch.setattr(fix, 'create_symlink', maybe_crash)
    with pytest.raises(OSError, match='模拟中断'):
        call('fix', '--scan', str(root))

    assert not os.path.lexists(root / 'broken.lnk')
    last = json.loads(journal.read_text(encoding='utf-8').splitlines()[-1])
    assert last['member'] == 'broken.lnk'
    assert last['link_text'] == os.path.join('store', 'moved.bin')
    assert last['mtime_ns'] == link_mtime

    failing[0] = False
    _symlink(root / 'gone' / 'moved.bin', root / 'broken.lnk')
    assert call('fix', '--scan', str(root)) == 0
    assert (root / 'broken.lnk').resolve() == (root / 'store' / 'moved.bin').resolve()
    assert not journal.exists()
