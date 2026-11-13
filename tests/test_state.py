import json

from pathlib import Path

import pytest

from linktool import state
from linktool.fs import Kind
from linktool.hashing import Identity


def _group() -> state.GroupRecord:
    return state.GroupRecord(
        key='f:7:abc',
        identity=Identity(size=7, digest='abc'),
        dir_target=None,
        source=state.Marker(rel='keep/a.bin'),
        members=[
            state.MemberRecord(
                rel='other/b.bin',
                abs=None,
                kind=Kind.FILE,
                mtime_ns=17,
                birth_ns=None,
                target_is_dir=None,
                status=state.PENDING,
            ),
            state.MemberRecord(
                rel=None,
                abs='/elsewhere/c.bin',
                kind=Kind.SYMLINK,
                mtime_ns=19,
                birth_ns=23,
                target_is_dir=True,
                status=state.PENDING,
            ),
        ],
    )


def test_a_record_survives_a_round_trip(tmp_path: Path) -> None:
    folder = state.workdir(tmp_path)
    store = state.StateStore(folder)
    store.state = tmp_path
    store.base = tmp_path / 'up'
    store.add([_group()], [tmp_path / 'stage'])

    again = state.StateStore(folder)
    assert again.state == tmp_path
    assert again.base == tmp_path / 'up'
    assert again.staging == [tmp_path / 'stage']
    assert again.groups == [_group()]


def test_a_record_of_another_version_is_refused(tmp_path: Path) -> None:
    folder = state.workdir(tmp_path)
    folder.mkdir(parents=True)
    (folder / 'state.json').write_text(json.dumps({'version': 1, 'groups': []}), encoding='utf-8')
    with pytest.raises(state.RecordError, match='记录版本是 1'):
        state.StateStore(folder)


def test_a_staging_manifest_names_its_owning_state_directory(tmp_path: Path) -> None:
    folder = state.workdir(tmp_path / 'stage')
    store = state.StagingStore(folder)
    store.owner = tmp_path / 'tree'
    store.add(
        [
            state.StagedItem(
                target=str(tmp_path / 'tree' / 'a.bin'),
                staged='files/a.bin',
                kind=Kind.FILE,
                mtime_ns=5,
                birth_ns=None,
                target_is_dir=None,
                status=state.STAGED,
            )
        ]
    )

    again = state.StagingStore(folder)
    assert again.owner == tmp_path / 'tree'
    assert [item.target for item in again.staged()] == [str(tmp_path / 'tree' / 'a.bin')]


def test_a_path_inside_the_owner_keeps_its_layout_in_the_area(tmp_path: Path) -> None:
    owner = tmp_path / 'tree'
    area = tmp_path / 'stage'
    assert state.staged_location(owner, area, owner / 'deep' / 'a.bin') == (
        area / 'files' / 'deep' / 'a.bin'
    )


def test_a_path_outside_the_owner_gets_its_own_directory(tmp_path: Path) -> None:
    owner = tmp_path / 'tree'
    area = tmp_path / 'stage'
    first = state.staged_location(owner, area, tmp_path / 'one' / 'a.bin')
    second = state.staged_location(owner, area, tmp_path / 'two' / 'a.bin')
    assert first.parent != second.parent
    assert first.name == second.name == 'a.bin'
    assert first.parent.parent == area / 'files' / 'external'
    assert first == state.staged_location(owner, area, tmp_path / 'one' / 'a.bin')
