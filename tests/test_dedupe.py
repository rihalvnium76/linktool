import json
import os

from pathlib import Path
from typing import cast

import pytest

from linktool.cli import main
from linktool.fs import Capabilities, Prober, supports_hardlink

MTIME_NEW = 1_600_000_000_000_000_000
MTIME_OLD = 1_500_000_000_000_000_000


def call(*args: str) -> int:
    return main(list(args))


def _write(path: Path, content: bytes, mtime_ns: int = MTIME_NEW) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, ns=(mtime_ns, mtime_ns))


def _symlink(source: Path | str, target: Path, *, is_dir: bool = False) -> None:
    try:
        os.symlink(source, target, target_is_directory=is_dir)
    except OSError as error:
        pytest.skip(f'本机不能创建符号链接：{error}')


def _plan(plan_path: Path) -> dict[str, object]:
    return cast(dict[str, object], json.loads(plan_path.read_text(encoding='utf-8')))


def _questions(plan_path: Path) -> list[dict[str, object]]:
    raw = _plan(plan_path)['questions']
    assert isinstance(raw, list)
    return [cast(dict[str, object], item) for item in cast(list[object], raw)]


def _answer(plan_path: Path, prefix: str, value: str) -> None:
    payload = _plan(plan_path)
    raw = payload['questions']
    assert isinstance(raw, list)
    for item in cast(list[object], raw):
        question = cast(dict[str, object], item)
        if str(question['id']).startswith(prefix):
            question['answer'] = value
    plan_path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')


def _accept(plan_path: Path) -> None:
    _answer(plan_path, 'plan-', 'yes')


def _decisions(plan_path: Path) -> list[dict[str, object]]:
    raw = _plan(plan_path)['decisions']
    assert isinstance(raw, list)
    return [cast(dict[str, object], item) for item in cast(list[object], raw)]


def _no_links(self: Prober, folder: Path) -> Capabilities:
    return Capabilities(hardlink=False, symlink=False)


def test_remove_then_rebuild_restores_a_hardlink(tmp_path: Path) -> None:
    if not supports_hardlink():
        pytest.skip('这台设备的 Python 没有 os.link')
    root = tmp_path / 'tree'
    _write(root / 'keep' / 'a.bin', b'payload')
    _write(root / 'other' / 'b.bin', b'payload')

    args = ('remove', '--root', str(root), '--prefer', str(root / 'keep'))
    assert call(*args) == 3
    plan_path = root / '.linktool' / 'plan-remove.json'
    assert all(str(question['id']).startswith('plan-') for question in _questions(plan_path))
    _accept(plan_path)

    assert call(*args) == 0
    assert not (root / 'other' / 'b.bin').exists()
    assert (root / 'keep' / 'a.bin').exists()

    assert call('rebuild', '--root', str(root)) == 0
    rebuilt = root / 'other' / 'b.bin'
    assert rebuilt.exists()
    assert not rebuilt.is_symlink()
    assert rebuilt.stat().st_ino == (root / 'keep' / 'a.bin').stat().st_ino


def test_plan_file_records_every_decision(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'keep' / 'a.bin', b'payload')
    _write(root / 'other' / 'b.bin', b'payload')
    assert call('remove', '--root', str(root), '--prefer', str(root / 'keep')) == 3
    decisions = _decisions(root / '.linktool' / 'plan-remove.json')
    assert decisions == [
        {
            'source': 'keep/a.bin',
            'reason': '偏好目录下唯一',
            'identity': decisions[0]['identity'],
            'paths': ['other/b.bin'],
        }
    ]


def test_ambiguous_group_waits_for_an_answer(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'a.bin', b'payload')
    _write(root / 'b.bin', b'payload')

    assert call('remove', '--root', str(root)) == 3
    plan_path = root / '.linktool' / 'plan-remove.json'
    assert all(str(question['id']).startswith('source-') for question in _questions(plan_path))
    assert sorted(path.name for path in root.glob('*.bin')) == ['a.bin', 'b.bin']

    _answer(plan_path, 'source-', '1')
    assert call('remove', '--root', str(root)) == 3
    _accept(plan_path)
    assert call('remove', '--root', str(root)) == 0
    assert [path.name for path in root.glob('*.bin')] == ['a.bin']
    assert not plan_path.exists()


def test_pick_first_proposes_a_source_for_one_confirmation(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'a.bin', b'payload')
    _write(root / 'b.bin', b'payload')
    args = ('remove', '--root', str(root), '--pick', 'first')

    assert call(*args) == 3
    plan_path = root / '.linktool' / 'plan-remove.json'
    assert all(str(question['id']).startswith('plan-') for question in _questions(plan_path))
    assert _decisions(plan_path)[0]['reason'] == '无规则可依，按路径排序取最前'
    assert _decisions(plan_path)[0]['source'] == 'a.bin'

    _accept(plan_path)
    assert call(*args) == 0
    assert [path.name for path in root.glob('*.bin')] == ['a.bin']


def test_declining_the_plan_leaves_the_tree_alone(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'keep' / 'a.bin', b'payload')
    _write(root / 'other' / 'b.bin', b'payload')
    args = ('remove', '--root', str(root), '--prefer', str(root / 'keep'))
    assert call(*args) == 3
    plan_path = root / '.linktool' / 'plan-remove.json'
    _answer(plan_path, 'plan-', 'no')
    assert call(*args) == 1
    assert (root / 'other' / 'b.bin').exists()


def test_plan_for_other_arguments_is_refused(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'a.bin', b'payload')
    _write(root / 'b.bin', b'payload')
    assert call('remove', '--root', str(root)) == 3
    assert call('remove', '--root', str(root), '--prefer', str(root)) == 1


def test_plan_for_another_pick_is_refused(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'a.bin', b'payload')
    _write(root / 'b.bin', b'payload')
    assert call('remove', '--root', str(root)) == 3
    assert call('remove', '--root', str(root), '--pick', 'first') == 1
    assert (root / 'b.bin').exists()


def test_invalid_answer_keeps_the_question(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'a.bin', b'payload')
    _write(root / 'b.bin', b'payload')
    assert call('remove', '--root', str(root)) == 3
    plan_path = root / '.linktool' / 'plan-remove.json'
    _answer(plan_path, 'source-', '99')
    assert call('remove', '--root', str(root)) == 3
    assert _questions(plan_path)[0]['error'] is not None
    assert [path.name for path in root.glob('*.bin')] == ['a.bin', 'b.bin']


def test_stage_then_restore(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    staging = tmp_path / 'stage'
    _write(root / 'keep' / 'a.bin', b'payload')
    _write(root / 'other' / 'b.bin', b'payload')

    args = ('--root', str(root), '--staging', str(staging), '--prefer', str(root / 'keep'))
    assert call('stage', *args) == 3
    _accept(root / '.linktool' / 'plan-stage.json')
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

    args = ('remove', '--root', str(root))
    assert call(*args) == 3
    _accept(root / '.linktool' / 'plan-remove.json')
    assert call(*args) == 0
    assert not (root / 'link.lnk').exists()

    assert call('rebuild', '--root', str(root)) == 0
    rebuilt = root / 'link.lnk'
    assert rebuilt.is_symlink()
    assert rebuilt.resolve() == (root / 'a.bin').resolve()


def test_links_whose_volume_refuses_symlinks_ask_before_skipping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / 'tree'
    _write(root / 'docs' / 'inner.bin', b'payload')
    _symlink('docs', root / 'link-a', is_dir=True)
    _symlink('docs', root / 'link-b', is_dir=True)

    args = ('remove', '--root', str(root))
    assert call(*args) == 3
    _accept(root / '.linktool' / 'plan-remove.json')
    assert call(*args) == 0
    assert not (root / 'link-a').exists()

    monkeypatch.setattr(Prober, 'capabilities', _no_links)
    plan_path = root / '.linktool' / 'plan-rebuild.json'
    assert call('rebuild', '--root', str(root)) == 3
    questions = _questions(plan_path)
    assert [str(question['id']).split('-')[0] for question in questions] == ['unavailable']
    assert 'link-a' in str(questions[0]['prompt'])

    _answer(plan_path, 'unavailable-', 'no')
    assert call('rebuild', '--root', str(root)) == 1
    assert not (root / 'link-a').exists()

    _answer(plan_path, 'unavailable-', 'yes')
    assert call('rebuild', '--root', str(root)) == 0
    assert not (root / 'link-a').exists()
    assert not (root / 'link-b').exists()


def test_stage_skips_links_the_staging_volume_cannot_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / 'tree'
    _write(root / 'docs' / 'inner.bin', b'payload')
    _symlink('docs', root / 'link-a', is_dir=True)
    _symlink('docs', root / 'link-b', is_dir=True)
    _write(root / 'keep' / 'a.bin', b'payload')
    _write(root / 'other' / 'b.bin', b'payload')
    staging = tmp_path / 'stage'

    monkeypatch.setattr(Prober, 'capabilities', _no_links)
    args = (
        'stage',
        '--root',
        str(root),
        '--staging',
        str(staging),
        '--prefer',
        str(root / 'keep'),
    )
    plan_path = root / '.linktool' / 'plan-stage.json'
    assert call(*args) == 3
    assert [str(question['id']).split('-')[0] for question in _questions(plan_path)] == [
        'unavailable'
    ]

    _answer(plan_path, 'unavailable-', 'yes')
    assert call(*args) == 3
    _accept(plan_path)
    assert call(*args) == 0

    assert os.path.lexists(root / 'link-a')
    assert os.path.lexists(root / 'link-b')
    assert not (root / 'other' / 'b.bin').exists()


def test_restore_keeps_a_link_staged_where_links_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / 'tree'
    _write(root / 'a.bin', b'payload')
    _symlink('a.bin', root / 'link.lnk')
    staging = tmp_path / 'stage'

    args = ('stage', '--root', str(root), '--staging', str(staging))
    plan_path = root / '.linktool' / 'plan-stage.json'
    assert call(*args) == 3
    _accept(plan_path)
    assert call(*args) == 0
    assert not os.path.lexists(root / 'link.lnk')

    monkeypatch.setattr(Prober, 'capabilities', _no_links)
    restore_plan = staging / '.linktool' / 'plan-restore.json'
    restore_args = ('restore', '--root', str(root), '--staging', str(staging))
    assert call(*restore_args) == 3
    assert [str(question['id']).split('-')[0] for question in _questions(restore_plan)] == [
        'unavailable'
    ]

    _answer(restore_plan, 'unavailable-', 'yes')
    assert call(*restore_args) == 0

    assert not os.path.lexists(root / 'link.lnk')
    assert (staging / 'files' / 'link.lnk').is_symlink()


def test_stage_says_so_when_every_member_was_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / 'tree'
    _write(root / 'docs' / 'inner.bin', b'payload')
    _symlink('docs', root / 'link-a', is_dir=True)
    _symlink('docs', root / 'link-b', is_dir=True)
    staging = tmp_path / 'stage'

    monkeypatch.setattr(Prober, 'capabilities', _no_links)
    args = ('stage', '--root', str(root), '--staging', str(staging))
    plan_path = root / '.linktool' / 'plan-stage.json'
    assert call(*args) == 3
    _answer(plan_path, 'unavailable-', 'yes')
    capsys.readouterr()

    assert call(*args) == 0
    assert '跳过 2 条目标卷不支持符号链接的链接' in capsys.readouterr().err


def test_copy_fallback_confirms_size_and_keeps_mtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Prober, 'capabilities', _no_links)
    root = tmp_path / 'tree'
    _write(root / 'keep' / 'a.bin', b'payload' * 100, MTIME_NEW)
    _write(root / 'other' / 'b.bin', b'payload' * 100, MTIME_OLD)

    args = ('remove', '--root', str(root), '--prefer', str(root / 'keep'))
    assert call(*args) == 3
    _accept(root / '.linktool' / 'plan-remove.json')
    assert call(*args) == 0

    plan_path = root / '.linktool' / 'plan-rebuild.json'
    assert call('rebuild', '--root', str(root)) == 3
    assert all(str(question['id']).startswith('copy-') for question in _questions(plan_path))
    _answer(plan_path, 'copy-', 'yes')
    assert call('rebuild', '--root', str(root)) == 0

    rebuilt = root / 'other' / 'b.bin'
    assert not rebuilt.is_symlink()
    assert rebuilt.read_bytes() == b'payload' * 100
    assert rebuilt.stat().st_mtime_ns == MTIME_OLD


def test_relative_links_respect_base_dir(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'deep' / 'a.bin', b'payload')
    _symlink(root / 'deep' / 'a.bin', root / 'deep' / 'link.lnk')

    for mode, expected in (('off', None), ('base', 'a.bin')):
        args = ('remove', '--root', str(root))
        if not (root / '.linktool' / 'plan-remove.json').exists():
            assert call(*args) == 3
        _accept(root / '.linktool' / 'plan-remove.json')
        assert call(*args) == 0
        assert call('rebuild', '--root', str(root), '--rel-mode', mode) == 0
        text = os.readlink(root / 'deep' / 'link.lnk')
        assert text == expected if expected is not None else text.endswith('a.bin')


def test_scan_outside_root_asks_first(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    outside = tmp_path / 'outside'
    _write(root / 'y.bin', b'payload')
    _write(outside / 'x.bin', b'payload')

    args = ('remove', '--root', str(root), '--scan', str(root), '--scan', str(outside))
    assert call(*args) == 3
    plan_path = root / '.linktool' / 'plan-remove.json'
    assert [question['kind'] for question in _questions(plan_path)] == ['confirm']

    _answer(plan_path, 'scan-', 'yes')
    assert call(*args) == 3
    assert _questions(plan_path)[0]['kind'] == 'choice'


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
    args = ('remove', '--root', str(root), '--prefer', str(root / 'keep'))
    assert call(*args) == 3
    _accept(root / '.linktool' / 'plan-remove.json')
    assert call(*args) == 0
    assert call('rebuild', '--root', str(root)) == 0
    assert (root / 'other' / 'b.bin').is_symlink()


def test_status_counts_what_each_record_still_holds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / 'tree'
    _write(root / 'keep' / 'a.bin', b'payload')
    _write(root / 'other' / 'b.bin', b'payload')
    _write(root / 'other' / 'c.bin', b'payload')
    staging = tmp_path / 'stage'
    args = ('stage', '--root', str(root), '--staging', str(staging), '--prefer', str(root / 'keep'))

    capsys.readouterr()
    assert call('status', '--root', str(root), '--staging', str(staging)) == 0
    assert '没有待重建的条目' in capsys.readouterr().err

    assert call(*args) == 3
    _accept(root / '.linktool' / 'plan-stage.json')
    assert call(*args) == 0
    capsys.readouterr()

    assert call('status', '--root', str(root), '--staging', str(staging)) == 0
    output = capsys.readouterr().err
    assert '2 条待重建，分布在 1 组' in output
    assert 'other/b.bin' in output
    assert '2 条待还原' in output
