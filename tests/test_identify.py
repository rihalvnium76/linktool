import os

from pathlib import Path

import pytest

from linktool import hashing, identify, scan


def _entries(root: Path) -> list[scan.Entry]:
    return scan.collect([root], root, []).entries


def _symlink(source: Path | str, target: Path, *, is_dir: bool = False) -> None:
    try:
        os.symlink(source, target, target_is_directory=is_dir)
    except OSError as error:
        pytest.skip(f'本机不能创建符号链接：{error}')


def _write(path: Path, content: bytes, mtime_ns: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, ns=(mtime_ns, mtime_ns))


def test_same_content_forms_one_group(tmp_path: Path) -> None:
    _write(tmp_path / 'a.bin', b'payload', 1)
    _write(tmp_path / 'b.bin', b'payload', 2)
    groups = identify.build(_entries(tmp_path))
    assert len(groups) == 1
    assert sorted(member.entry.label for member in groups[0].members) == ['a.bin', 'b.bin']


def test_different_content_stays_apart(tmp_path: Path) -> None:
    _write(tmp_path / 'a.bin', b'payload', 1)
    _write(tmp_path / 'b.bin', b'other!!', 2)
    assert identify.build(_entries(tmp_path)) == []


def test_symlink_joins_the_group_of_its_target(tmp_path: Path) -> None:
    _write(tmp_path / 'a.bin', b'payload', 1)
    _symlink(tmp_path / 'a.bin', tmp_path / 'link.bin')
    groups = identify.build(_entries(tmp_path))
    assert len(groups) == 1
    assert sorted(member.entry.label for member in groups[0].members) == ['a.bin', 'link.bin']


def test_hardlinks_form_one_group(tmp_path: Path) -> None:
    _write(tmp_path / 'a.bin', b'payload', 1)
    os.link(tmp_path / 'a.bin', tmp_path / 'b.bin')
    groups = identify.build(_entries(tmp_path))
    assert len(groups) == 1
    assert len(groups[0].members) == 2


def test_broken_symlink_forms_no_group(tmp_path: Path) -> None:
    _symlink(tmp_path / 'missing.bin', tmp_path / 'dangling.bin')
    _symlink('missing.bin', tmp_path / 'dangling2.bin')
    assert identify.build(_entries(tmp_path)) == []


def test_directory_links_group_by_target(tmp_path: Path) -> None:
    (tmp_path / 'shared').mkdir()
    _symlink(tmp_path / 'shared', tmp_path / 'one', is_dir=True)
    _symlink(tmp_path / 'shared', tmp_path / 'two', is_dir=True)
    groups = identify.build(_entries(tmp_path))
    assert len(groups) == 1
    assert groups[0].is_dir
    assert groups[0].dir_target == tmp_path / 'shared'


def test_auto_source_uses_single_realpath_among_links(tmp_path: Path) -> None:
    _write(tmp_path / 'a.bin', b'payload', 1)
    _symlink(tmp_path / 'a.bin', tmp_path / 'x.bin')
    _symlink(tmp_path / 'a.bin', tmp_path / 'y.bin')
    group = identify.build(_entries(tmp_path))[0]
    assert identify.auto_source(group, ()) == tmp_path / 'a.bin'


def test_auto_source_uses_preferred_directory(tmp_path: Path) -> None:
    _write(tmp_path / 'keep' / 'a.bin', b'payload', 1)
    _write(tmp_path / 'other' / 'b.bin', b'payload', 2)
    group = identify.build(_entries(tmp_path))[0]
    assert identify.auto_source(group, (tmp_path / 'keep',)) == tmp_path / 'keep' / 'a.bin'


def test_auto_source_reports_conflict(tmp_path: Path) -> None:
    _write(tmp_path / 'keep' / 'a.bin', b'payload', 1)
    _write(tmp_path / 'other' / 'b.bin', b'payload', 2)
    _symlink(tmp_path / 'other' / 'b.bin', tmp_path / 'other' / 'c.bin')
    group = identify.build(_entries(tmp_path))[0]
    assert identify.auto_source(group, (tmp_path / 'keep',)) is None


def test_auto_source_without_rule_is_undecided(tmp_path: Path) -> None:
    _write(tmp_path / 'a.bin', b'payload', 1)
    _write(tmp_path / 'b.bin', b'payload', 2)
    group = identify.build(_entries(tmp_path))[0]
    assert identify.auto_source(group, ()) is None


def test_quick_digest_separates_same_size(tmp_path: Path) -> None:
    _write(tmp_path / 'a.bin', b'a' * 8192, 1)
    _write(tmp_path / 'b.bin', b'b' * 4096 + b'c' * 4096, 2)
    assert hashing.quick(8192, tmp_path / 'a.bin') != hashing.quick(8192, tmp_path / 'b.bin')


def test_auto_source_counts_hardlinks_in_preferred_directory_as_one(tmp_path: Path) -> None:
    _write(tmp_path / 'keep' / 'a.bin', b'payload', 1)
    os.link(tmp_path / 'keep' / 'a.bin', tmp_path / 'keep' / 'b.bin')
    _write(tmp_path / 'other' / 'c.bin', b'payload', 2)
    group = identify.build(_entries(tmp_path))[0]
    assert identify.auto_source(group, (tmp_path / 'keep',)) == tmp_path / 'keep' / 'a.bin'
