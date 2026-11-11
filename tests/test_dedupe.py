import json
import os

from pathlib import Path

import pytest

from linktool.cli import main
from linktool.fs import Capabilities, Prober

MTIME_NEW = 1_600_000_000_000_000_000
MTIME_OLD = 1_500_000_000_000_000_000


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


def _answer(plan_path: Path, kind: str, value: str) -> None:
    payload = json.loads(plan_path.read_text(encoding='utf-8'))
    for question in payload['questions']:
        if question['kind'] == kind:
            question['answer'] = value
    plan_path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')


def _no_links(self: Prober, folder: Path) -> Capabilities:
    return Capabilities(hardlink=False, symlink=False)


def test_remove_then_rebuild_restores_a_hardlink(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'keep' / 'a.bin', b'payload')
    _write(root / 'other' / 'b.bin', b'payload')

    assert call('remove', '--root', str(root), '--prefer', str(root / 'keep')) == 0
    assert not (root / 'other' / 'b.bin').exists()
    assert (root / 'keep' / 'a.bin').exists()

    assert call('rebuild', '--root', str(root)) == 0
    rebuilt = root / 'other' / 'b.bin'
    assert rebuilt.exists()
    assert not rebuilt.is_symlink()
    assert rebuilt.stat().st_ino == (root / 'keep' / 'a.bin').stat().st_ino


def test_ambiguous_group_waits_for_an_answer(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'a.bin', b'payload')
    _write(root / 'b.bin', b'payload')

    assert call('remove', '--root', str(root)) == 3
    plan_path = root / '.linktool' / 'plan-remove.json'
    assert plan_path.exists()
    assert sorted(path.name for path in root.glob('*.bin')) == ['a.bin', 'b.bin']

    _answer(plan_path, 'choice', '1')
    assert call('remove', '--root', str(root)) == 0
    assert [path.name for path in root.glob('*.bin')] == ['a.bin']
    assert not plan_path.exists()


def test_plan_for_other_arguments_is_refused(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'a.bin', b'payload')
    _write(root / 'b.bin', b'payload')
    assert call('remove', '--root', str(root)) == 3
    assert call('remove', '--root', str(root), '--prefer', str(root)) == 1


def test_invalid_answer_keeps_the_question(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'a.bin', b'payload')
    _write(root / 'b.bin', b'payload')
    assert call('remove', '--root', str(root)) == 3
    plan_path = root / '.linktool' / 'plan-remove.json'
    _answer(plan_path, 'choice', '99')
    assert call('remove', '--root', str(root)) == 3
    payload = json.loads(plan_path.read_text(encoding='utf-8'))
    assert payload['questions'][0]['error'] is not None
    assert [path.name for path in root.glob('*.bin')] == ['a.bin', 'b.bin']


def test_stage_then_restore(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    staging = tmp_path / 'stage'
    _write(root / 'keep' / 'a.bin', b'payload')
    _write(root / 'other' / 'b.bin', b'payload')

    args = ('--root', str(root), '--staging', str(staging), '--prefer', str(root / 'keep'))
    assert call('stage', *args) == 0
    assert not (root / 'other' / 'b.bin').exists()
    assert (staging / 'files' / 'other' / 'b.bin').exists()

    assert call('restore', '--staging', str(staging)) == 0
    assert (root / 'other' / 'b.bin').exists()
    assert not (staging / 'files' / 'other' / 'b.bin').exists()


def test_staging_inside_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'a.bin', b'payload')
    assert call('stage', '--root', str(root), '--staging', str(root / 'stage')) == 1


def test_rebuild_keeps_a_link_a_link(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'a.bin', b'payload')
    _symlink(root / 'a.bin', root / 'link.lnk')

    assert call('remove', '--root', str(root)) == 0
    assert not (root / 'link.lnk').exists()

    assert call('rebuild', '--root', str(root)) == 0
    rebuilt = root / 'link.lnk'
    assert rebuilt.is_symlink()
    assert rebuilt.resolve() == (root / 'a.bin').resolve()


def test_copy_fallback_confirms_size_and_keeps_mtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Prober, 'capabilities', _no_links)
    root = tmp_path / 'tree'
    _write(root / 'keep' / 'a.bin', b'payload' * 100, MTIME_NEW)
    _write(root / 'other' / 'b.bin', b'payload' * 100, MTIME_OLD)

    assert call('remove', '--root', str(root), '--prefer', str(root / 'keep')) == 0
    plan_path = root / '.linktool' / 'plan-rebuild.json'
    assert call('rebuild', '--root', str(root)) == 3
    _answer(plan_path, 'confirm', 'yes')
    assert call('rebuild', '--root', str(root)) == 0

    rebuilt = root / 'other' / 'b.bin'
    assert not rebuilt.is_symlink()
    assert rebuilt.read_bytes() == b'payload' * 100
    assert rebuilt.stat().st_mtime_ns == MTIME_OLD


def test_relative_links_respect_base_dir(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'deep' / 'a.bin', b'payload')
    _symlink(root / 'deep' / 'a.bin', root / 'deep' / 'link.lnk')

    assert call('remove', '--root', str(root)) == 0
    assert call('rebuild', '--root', str(root), '--rel-mode', 'off') == 0
    assert os.readlink(root / 'deep' / 'link.lnk').endswith('a.bin')

    assert call('remove', '--root', str(root)) == 0
    assert call('rebuild', '--root', str(root)) == 0
    assert os.readlink(root / 'deep' / 'link.lnk') == 'a.bin'


def test_scan_outside_root_asks_first(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    outside = tmp_path / 'outside'
    _write(root / 'y.bin', b'payload')
    _write(outside / 'x.bin', b'payload')

    args = ('remove', '--root', str(root), '--scan', str(root), '--scan', str(outside))
    assert call(*args) == 3
    plan_path = root / '.linktool' / 'plan-remove.json'
    payload = json.loads(plan_path.read_text(encoding='utf-8'))
    assert [question['kind'] for question in payload['questions']] == ['confirm']

    _answer(plan_path, 'confirm', 'yes')
    assert call(*args) == 3
    payload = json.loads(plan_path.read_text(encoding='utf-8'))
    assert payload['questions'][0]['kind'] == 'choice'


def test_init_config_refuses_to_overwrite(tmp_path: Path) -> None:
    target = tmp_path / 'linktool.toml'
    assert call('init-config', '--path', str(target)) == 0
    assert 'root' in target.read_text(encoding='utf-8')
    assert call('init-config', '--path', str(target)) == 1


def test_missing_os_link_falls_back_to_a_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = tmp_path / 'probe.lnk'
    try:
        os.symlink(tmp_path, probe, target_is_directory=True)
    except OSError as error:
        pytest.skip(f'本机不能创建符号链接：{error}')
    probe.unlink()
    monkeypatch.delattr(os, 'link', raising=False)

    root = tmp_path / 'tree'
    _write(root / 'keep' / 'a.bin', b'payload')
    _write(root / 'other' / 'b.bin', b'payload')
    assert call('remove', '--root', str(root), '--prefer', str(root / 'keep')) == 0
    assert call('rebuild', '--root', str(root)) == 0
    assert (root / 'other' / 'b.bin').is_symlink()
