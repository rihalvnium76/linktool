import json
import os
import re
import shutil

from pathlib import Path

import pytest

from linktool.cli import main
from linktool.fs import Capabilities, Prober, supports_hardlink
from sheet import decisions as _decisions
from sheet import questions as _questions

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


def _sheet(plan_path: Path) -> Path:
    return plan_path.with_suffix('.toml')


def _fill(lines: list[str], prefix: str, value: str) -> None:
    for index, line in enumerate(lines):
        found = re.match(r"^'([^']+)' = ", line)
        if found is not None and found.group(1).startswith(prefix):
            lines[index] = f"'{found.group(1)}' = '{value}'"


def _answer(plan_path: Path, prefix: str, value: str) -> None:
    sheet = _sheet(plan_path)
    lines = sheet.read_text(encoding='utf-8').splitlines()
    _fill(lines, prefix, value)
    sheet.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def _accept(plan_path: Path) -> None:
    _answer(plan_path, 'plan-', 'yes')


def _no_links(self: Prober, folder: Path) -> Capabilities:
    return Capabilities(hardlink=False, symlink=False)


def test_remove_then_rebuild_restores_a_hardlink(tmp_path: Path) -> None:
    if not supports_hardlink():
        pytest.skip('这台设备的 Python 没有 os.link')
    root = tmp_path / 'tree'
    _write(root / 'keep' / 'a.bin', b'payload')
    _write(root / 'other' / 'b.bin', b'payload')

    args = ('remove', '--scan', str(root), '--prefer', str(root / 'keep'))
    assert call(*args) == 3
    plan_path = root / '.linktool' / 'plan-remove.json'
    assert all(question.id.startswith('plan-') for question in _questions(plan_path))
    _accept(plan_path)

    assert call(*args) == 0
    assert not (root / 'other' / 'b.bin').exists()
    assert (root / 'keep' / 'a.bin').exists()

    assert call('rebuild', '--state', str(root)) == 0
    rebuilt = root / 'other' / 'b.bin'
    assert rebuilt.exists()
    assert not rebuilt.is_symlink()
    assert rebuilt.stat().st_ino == (root / 'keep' / 'a.bin').stat().st_ino


def test_the_answer_sheet_lists_every_decision(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'keep' / 'a.bin', b'payload')
    _write(root / 'other' / 'b.bin', b'payload')
    assert call('remove', '--scan', str(root), '--prefer', str(root / 'keep')) == 3
    found = _decisions(root / '.linktool' / 'plan-remove.json')
    assert [(item.source, item.reason, item.paths) for item in found] == [
        (str(root / 'keep' / 'a.bin'), '偏好目录下唯一', (str(root / 'other' / 'b.bin'),))
    ]


def test_ambiguous_group_waits_for_an_answer(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'a.bin', b'payload')
    _write(root / 'b.bin', b'payload')

    assert call('remove', '--scan', str(root)) == 3
    plan_path = root / '.linktool' / 'plan-remove.json'
    assert all(question.id.startswith('source-') for question in _questions(plan_path))
    assert sorted(path.name for path in root.glob('*.bin')) == ['a.bin', 'b.bin']

    _answer(plan_path, 'source-', '1')
    assert call('remove', '--scan', str(root)) == 3
    _accept(plan_path)
    assert call('remove', '--scan', str(root)) == 0
    assert [path.name for path in root.glob('*.bin')] == ['a.bin']
    assert not plan_path.exists()


def test_pick_first_proposes_a_source_for_one_confirmation(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'a.bin', b'payload')
    _write(root / 'b.bin', b'payload')
    args = ('remove', '--scan', str(root), '--pick', 'first')

    assert call(*args) == 3
    plan_path = root / '.linktool' / 'plan-remove.json'
    assert all(question.id.startswith('plan-') for question in _questions(plan_path))
    assert _decisions(plan_path)[0].reason == '无规则可依，按路径排序取最前'
    assert _decisions(plan_path)[0].source == str(root / 'a.bin')

    _accept(plan_path)
    assert call(*args) == 0
    assert [path.name for path in root.glob('*.bin')] == ['a.bin']


def test_declining_the_plan_leaves_the_tree_alone(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'keep' / 'a.bin', b'payload')
    _write(root / 'other' / 'b.bin', b'payload')
    args = ('remove', '--scan', str(root), '--prefer', str(root / 'keep'))
    assert call(*args) == 3
    plan_path = root / '.linktool' / 'plan-remove.json'
    _answer(plan_path, 'plan-', 'no')
    assert call(*args) == 1
    assert (root / 'other' / 'b.bin').exists()


def test_plan_for_other_arguments_is_refused(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'a.bin', b'payload')
    _write(root / 'b.bin', b'payload')
    assert call('remove', '--scan', str(root)) == 3
    assert call('remove', '--scan', str(root), '--prefer', str(root)) == 1


def test_plan_for_another_pick_is_refused(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'a.bin', b'payload')
    _write(root / 'b.bin', b'payload')
    assert call('remove', '--scan', str(root)) == 3
    assert call('remove', '--scan', str(root), '--pick', 'first') == 1
    assert (root / 'b.bin').exists()


def test_invalid_answer_keeps_the_question(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'a.bin', b'payload')
    _write(root / 'b.bin', b'payload')
    assert call('remove', '--scan', str(root)) == 3
    plan_path = root / '.linktool' / 'plan-remove.json'
    _answer(plan_path, 'source-', '99')
    assert call('remove', '--scan', str(root)) == 3
    assert _questions(plan_path)[0].error is not None
    assert [path.name for path in root.glob('*.bin')] == ['a.bin', 'b.bin']


def test_stage_then_restore(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    staging = tmp_path / 'stage'
    _write(root / 'keep' / 'a.bin', b'payload')
    _write(root / 'other' / 'b.bin', b'payload')

    args = ('stage', '--scan', str(root), '--staging', str(staging), '--prefer', str(root / 'keep'))
    assert call(*args) == 3
    _accept(root / '.linktool' / 'plan-stage.json')
    assert call(*args) == 0
    assert not (root / 'other' / 'b.bin').exists()
    assert (staging / 'files' / 'other' / 'b.bin').exists()

    assert call('restore', '--state', str(root)) == 0
    assert (root / 'other' / 'b.bin').exists()
    assert not (staging / 'files' / 'other' / 'b.bin').exists()


def test_stage_keeps_the_inode_a_hardlink_shares(tmp_path: Path) -> None:
    if not supports_hardlink():
        pytest.skip('这台设备的 Python 没有 os.link')
    root = tmp_path / 'tree'
    staging = tmp_path / 'stage'
    source = root / 'keep' / 'a.bin'
    _write(source, b'payload')
    twin = root / 'other' / 'b.bin'
    twin.parent.mkdir(parents=True)
    os.link(source, twin)

    args = ('stage', '--scan', str(root), '--staging', str(staging), '--prefer', str(root / 'keep'))
    assert call(*args) == 3
    _accept(root / '.linktool' / 'plan-stage.json')
    assert call(*args) == 0

    held = staging / 'files' / 'other' / 'b.bin'
    assert held.stat().st_ino == source.stat().st_ino
    assert held.stat().st_nlink == 2

    assert call('restore', '--state', str(root)) == 0
    assert twin.stat().st_ino == source.stat().st_ino
    assert twin.stat().st_nlink == 2


def test_stage_holds_a_member_outside_the_base_under_its_own_digest(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    outside = tmp_path / 'outside'
    staging = tmp_path / 'stage'
    _write(root / 'keep' / 'a.bin', b'payload')
    _write(outside / 'b.bin', b'payload')

    args = (
        'stage',
        '--state',
        str(root),
        '--scan',
        str(root),
        str(outside),
        '--staging',
        str(staging),
        '--prefer',
        str(root / 'keep'),
    )
    assert call(*args) == 3
    plan_path = root / '.linktool' / 'plan-stage.json'
    _answer(plan_path, 'scan-', 'yes')
    assert call(*args) == 3
    _accept(plan_path)
    assert call(*args) == 0

    assert not os.path.lexists(outside / 'b.bin')
    assert sorted(path.name for path in outside.iterdir()) == []
    held = sorted((staging / 'files' / 'external').iterdir())
    assert len(held) == 1
    assert (held[0] / 'b.bin').read_bytes() == b'payload'


def test_staging_inside_a_scan_path_is_refused(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'a.bin', b'payload')
    assert call('stage', '--scan', str(root), '--staging', str(root / 'stage')) == 1


def test_a_staging_area_of_another_tree_is_refused(tmp_path: Path) -> None:
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    staging = tmp_path / 'stage'
    for tree in (first, second):
        _write(tree / 'keep' / 'a.bin', b'payload')
        _write(tree / 'other' / 'b.bin', b'payload')

    args = (
        'stage',
        '--scan',
        str(first),
        '--staging',
        str(staging),
        '--prefer',
        str(first / 'keep'),
    )
    assert call(*args) == 3
    _accept(first / '.linktool' / 'plan-stage.json')
    assert call(*args) == 0

    refused = ('stage', '--scan', str(second), '--staging', str(staging))
    assert call(*refused) == 1


def test_stage_records_only_the_area_it_filled(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    used = tmp_path / 'used'
    unused = tmp_path / 'unused'
    _write(root / 'keep' / 'a.bin', b'payload')
    _write(root / 'other' / 'b.bin', b'payload')

    args = (
        'stage',
        '--scan',
        str(root),
        '--staging',
        str(used),
        str(unused),
        '--prefer',
        str(root / 'keep'),
    )
    assert call(*args) == 3
    _accept(root / '.linktool' / 'plan-stage.json')
    assert call(*args) == 0

    recorded = json.loads((root / '.linktool' / 'state.json').read_text(encoding='utf-8'))
    assert recorded['staging'] == [str(used)]
    assert not (unused / '.linktool' / 'staging.json').exists()


def test_rebuild_keeps_a_link_a_link(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'a.bin', b'payload')
    _symlink(root / 'a.bin', root / 'link.lnk')

    args = ('remove', '--scan', str(root))
    assert call(*args) == 3
    _accept(root / '.linktool' / 'plan-remove.json')
    assert call(*args) == 0
    assert not (root / 'link.lnk').exists()

    assert call('rebuild', '--state', str(root)) == 0
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

    args = ('remove', '--scan', str(root))
    assert call(*args) == 3
    _accept(root / '.linktool' / 'plan-remove.json')
    assert call(*args) == 0
    assert not (root / 'link-a').exists()

    monkeypatch.setattr(Prober, 'capabilities', _no_links)
    plan_path = root / '.linktool' / 'plan-rebuild.json'
    assert call('rebuild', '--state', str(root)) == 3
    questions = _questions(plan_path)
    assert [question.id.split('-')[0] for question in questions] == ['unavailable']
    assert 'link-a' in questions[0].prompt

    _answer(plan_path, 'unavailable-', 'no')
    assert call('rebuild', '--state', str(root)) == 1
    assert not (root / 'link-a').exists()

    _answer(plan_path, 'unavailable-', 'yes')
    assert call('rebuild', '--state', str(root)) == 0
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
        '--scan',
        str(root),
        '--staging',
        str(staging),
        '--prefer',
        str(root / 'keep'),
    )
    plan_path = root / '.linktool' / 'plan-stage.json'
    assert call(*args) == 3
    assert [question.id.split('-')[0] for question in _questions(plan_path)] == ['unavailable']

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

    args = ('stage', '--scan', str(root), '--staging', str(staging))
    plan_path = root / '.linktool' / 'plan-stage.json'
    assert call(*args) == 3
    _accept(plan_path)
    assert call(*args) == 0
    assert not os.path.lexists(root / 'link.lnk')

    monkeypatch.setattr(Prober, 'capabilities', _no_links)
    restore_plan = root / '.linktool' / 'plan-restore.json'
    restore_args = ('restore', '--state', str(root))
    assert call(*restore_args) == 3
    assert [question.id.split('-')[0] for question in _questions(restore_plan)] == ['unavailable']

    _answer(restore_plan, 'unavailable-', 'yes')
    assert call(*restore_args) == 0

    assert not os.path.lexists(root / 'link.lnk')
    assert (staging / 'files' / 'link.lnk').is_symlink()


def test_restore_refuses_a_record_whose_staging_area_is_gone(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    staging = tmp_path / 'stage'
    _write(root / 'keep' / 'a.bin', b'payload')
    _write(root / 'other' / 'b.bin', b'payload')

    args = ('stage', '--scan', str(root), '--staging', str(staging), '--prefer', str(root / 'keep'))
    assert call(*args) == 3
    _accept(root / '.linktool' / 'plan-stage.json')
    assert call(*args) == 0

    os.rename(staging, tmp_path / 'moved')
    assert call('restore', '--state', str(root)) == 1


def test_stage_says_so_when_every_member_was_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / 'tree'
    _write(root / 'docs' / 'inner.bin', b'payload')
    _symlink('docs', root / 'link-a', is_dir=True)
    _symlink('docs', root / 'link-b', is_dir=True)
    staging = tmp_path / 'stage'

    monkeypatch.setattr(Prober, 'capabilities', _no_links)
    args = ('stage', '--scan', str(root), '--staging', str(staging))
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

    args = ('remove', '--scan', str(root), '--prefer', str(root / 'keep'))
    assert call(*args) == 3
    _accept(root / '.linktool' / 'plan-remove.json')
    assert call(*args) == 0

    plan_path = root / '.linktool' / 'plan-rebuild.json'
    assert call('rebuild', '--state', str(root)) == 3
    assert all(question.id.startswith('copy-') for question in _questions(plan_path))
    _answer(plan_path, 'copy-', 'yes')
    assert call('rebuild', '--state', str(root)) == 0

    rebuilt = root / 'other' / 'b.bin'
    assert not rebuilt.is_symlink()
    assert rebuilt.read_bytes() == b'payload' * 100
    assert rebuilt.stat().st_mtime_ns == MTIME_OLD


def test_relative_links_respect_rel_mode(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'deep' / 'a.bin', b'payload')
    _symlink(root / 'deep' / 'a.bin', root / 'deep' / 'link.lnk')

    for mode, expected in (('off', None), ('base', 'a.bin')):
        args = ('remove', '--scan', str(root))
        if not (root / '.linktool' / 'plan-remove.json').exists():
            assert call(*args) == 3
        _accept(root / '.linktool' / 'plan-remove.json')
        assert call(*args) == 0
        assert call('rebuild', '--state', str(root), '--rel-mode', mode) == 0
        text = os.readlink(root / 'deep' / 'link.lnk')
        assert text == expected if expected is not None else text.endswith('a.bin')


def test_scan_outside_the_base_asks_first(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    outside = tmp_path / 'outside'
    _write(root / 'y.bin', b'payload')
    _write(outside / 'x.bin', b'payload')

    args = ('remove', '--state', str(root), '--scan', str(root), str(outside))
    assert call(*args) == 3
    plan_path = root / '.linktool' / 'plan-remove.json'
    assert [question.kind for question in _questions(plan_path)] == ['confirm']

    _answer(plan_path, 'scan-', 'yes')
    assert call(*args) == 3
    assert [question.kind for question in _questions(plan_path)] == ['confirm', 'choice']


def test_a_member_outside_the_base_follows_the_tree_it_was_copied_to(tmp_path: Path) -> None:
    old = tmp_path / 'old'
    _write(old / 'tree' / 'keep' / 'a.bin', b'payload')
    _write(old / '1' / 'b.bin', b'payload')

    args = (
        'remove',
        '--state',
        str(old / 'tree'),
        '--scan',
        str(old / 'tree'),
        str(old / '1'),
        '--prefer',
        str(old / 'tree' / 'keep'),
    )
    assert call(*args) == 3
    plan_path = old / 'tree' / '.linktool' / 'plan-remove.json'
    _answer(plan_path, 'scan-', 'yes')
    assert call(*args) == 3
    sheet = (old / 'tree' / '.linktool' / 'plan-remove.toml').read_text(encoding='utf-8')
    assert str(old / '1' / 'b.bin') in sheet
    _accept(plan_path)
    assert call(*args) == 0
    assert not (old / '1' / 'b.bin').exists()

    recorded = json.loads((old / 'tree' / '.linktool' / 'state.json').read_text(encoding='utf-8'))
    member = recorded['groups'][0]['members'][0]
    assert member['rel'] == '../1/b.bin'
    assert member['abs'] is None

    shutil.copytree(old, tmp_path / 'new')
    shutil.rmtree(old)
    assert call('rebuild', '--state', str(tmp_path / 'new' / 'tree')) == 0
    rebuilt = tmp_path / 'new' / '1' / 'b.bin'
    assert rebuilt.read_bytes() == b'payload'
    if supports_hardlink():
        assert rebuilt.stat().st_ino == (tmp_path / 'new' / 'tree' / 'keep' / 'a.bin').stat().st_ino


def test_a_higher_base_records_paths_from_above_the_state(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'keep' / 'a.bin', b'payload')
    _write(root / 'other' / 'b.bin', b'payload')

    args = (
        'remove',
        '--state',
        str(root),
        '--scan',
        str(root),
        '--base',
        str(tmp_path),
        '--prefer',
        str(root / 'keep'),
    )
    assert call(*args) == 3
    plan_path = root / '.linktool' / 'plan-remove.json'
    assert _decisions(plan_path)[0].paths == (str(root / 'other' / 'b.bin'),)
    _accept(plan_path)
    assert call(*args) == 0
    assert not (root / 'other' / 'b.bin').exists()


def test_a_different_base_is_answered_before_the_rebuild(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'keep' / 'a.bin', b'payload')
    _write(root / 'other' / 'b.bin', b'payload')

    args = (
        'remove',
        '--state',
        str(root),
        '--scan',
        str(root),
        '--base',
        str(tmp_path),
        '--prefer',
        str(root / 'keep'),
    )
    assert call(*args) == 3
    _accept(root / '.linktool' / 'plan-remove.json')
    assert call(*args) == 0
    assert not (root / 'other' / 'b.bin').exists()

    plan_path = root / '.linktool' / 'plan-rebuild.json'
    assert call('rebuild', '--state', str(root)) == 3
    assert [question.id.split('-')[0] for question in _questions(plan_path)] == ['base']

    _answer(plan_path, 'base-', 'recorded')
    assert call('rebuild', '--state', str(root)) == 0
    assert (root / 'other' / 'b.bin').exists()


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
    args = ('remove', '--scan', str(root), '--prefer', str(root / 'keep'))
    assert call(*args) == 3
    _accept(root / '.linktool' / 'plan-remove.json')
    assert call(*args) == 0
    assert call('rebuild', '--state', str(root)) == 0
    assert (root / 'other' / 'b.bin').is_symlink()


def test_status_counts_what_each_record_still_holds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / 'tree'
    _write(root / 'keep' / 'a.bin', b'payload')
    _write(root / 'other' / 'b.bin', b'payload')
    _write(root / 'other' / 'c.bin', b'payload')
    staging = tmp_path / 'stage'
    args = ('stage', '--scan', str(root), '--staging', str(staging), '--prefer', str(root / 'keep'))

    capsys.readouterr()
    assert call('status', '--state', str(root)) == 0
    assert '没有待重建的条目' in capsys.readouterr().err

    assert call(*args) == 3
    _accept(root / '.linktool' / 'plan-stage.json')
    assert call(*args) == 0
    capsys.readouterr()

    assert call('status', '--state', str(root)) == 0
    output = capsys.readouterr().err
    assert '2 条待重建，分布在 1 组' in output
    assert str(root / 'other' / 'b.bin') in output
    assert '2 条待还原' in output


def test_status_says_an_area_it_cannot_find_is_gone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / 'tree'
    _write(root / 'keep' / 'a.bin', b'payload')
    _write(root / 'other' / 'b.bin', b'payload')
    staging = tmp_path / 'stage'
    args = ('stage', '--scan', str(root), '--staging', str(staging), '--prefer', str(root / 'keep'))

    assert call(*args) == 3
    _accept(root / '.linktool' / 'plan-stage.json')
    assert call(*args) == 0

    staging.rename(tmp_path / 'moved-away')
    capsys.readouterr()
    assert call('status', '--state', str(root)) == 0
    output = capsys.readouterr().err
    assert f'{staging}: 暂存区缺失' in output
    assert '不属于' not in output


def test_a_group_the_rules_leave_open_names_its_members(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'a.bin', b'payload')
    _write(root / 'b.bin', b'payload')

    assert call('remove', '--scan', str(root)) == 3
    sheet = _sheet(root / '.linktool' / 'plan-remove.json').read_text(encoding='utf-8')
    assert '有 2 条路径，规则定不出唯一的源' in sheet
    assert 'realpath' not in sheet


def test_the_gate_points_at_the_decisions_in_the_sheet(tmp_path: Path) -> None:
    root = tmp_path / 'tree'
    _write(root / 'a.bin', b'payload')
    _write(root / 'b.bin', b'payload')

    assert call('remove', '--scan', str(root), '--pick', 'first') == 3
    sheet = _sheet(root / '.linktool' / 'plan-remove.json').read_text(encoding='utf-8')
    assert '# 本文件的 decisions 段列出这一轮的全部决定：1 组，1 条路径会被移除或暂存' in sheet
    assert '# decisions  这一轮的全部决定' in sheet
    assert '#   源：' + str(root / 'a.bin') + '（无规则可依，按路径排序取最前）' in sheet
    assert '#   移除：' + str(root / 'b.bin') in sheet
