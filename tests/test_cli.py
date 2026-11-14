import os
import re
import subprocess
import sys

from importlib import metadata
from pathlib import Path

import pytest

from linktool import cli, config, source_digest
from linktool.commands import dedupe, fix

SOURCE = Path(__file__).resolve().parent.parent / 'src'


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[config.Config]:
    seen: list[config.Config] = []

    def capture(settings: config.Config, **_options: bool) -> int:
        seen.append(settings)
        return 0

    monkeypatch.setattr(dedupe, 'run', capture)
    return seen


def _capture_fix(monkeypatch: pytest.MonkeyPatch) -> list[config.Config]:
    seen: list[config.Config] = []

    def capture(settings: config.Config) -> int:
        seen.append(settings)
        return 0

    monkeypatch.setattr(fix, 'run', capture)
    return seen


def test_module_entry_point_prints_the_version() -> None:
    environment = {**os.environ, 'PYTHONPATH': str(SOURCE)}
    result = subprocess.run(
        [sys.executable, '-m', 'linktool', '--version'],
        capture_output=True,
        text=True,
        env=environment,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert re.fullmatch(r'linktool [0-9.]+ \([0-9a-f]{12}\)\n', result.stdout)


def test_the_version_short_name_prints_what_the_long_one_prints(
    capsys: pytest.CaptureFixture[str],
) -> None:
    printed: list[str] = []
    for flag in ('-V', '--version'):
        with pytest.raises(SystemExit) as raised:
            cli.main([flag])
        assert raised.value.code == 0
        printed.append(capsys.readouterr().out)

    assert printed[0] == printed[1]
    assert printed[0].startswith('linktool ')


def test_the_source_digest_is_stable_and_short() -> None:
    assert source_digest() == source_digest()
    assert re.fullmatch(r'[0-9a-f]{12}', source_digest())


def test_an_uninstalled_copy_names_its_source_without_a_version(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def missing(name: str) -> str:
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(cli.metadata, 'version', missing)

    with pytest.raises(SystemExit) as raised:
        cli.main(['-V'])

    assert raised.value.code == 0
    assert re.fullmatch(r'linktool \([0-9a-f]{12}\)\n', capsys.readouterr().out)


def test_a_list_setting_takes_several_values_in_one_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ('a', 'b', 'c'):
        (tmp_path / name).mkdir()
    monkeypatch.chdir(tmp_path)
    seen = _capture(monkeypatch)

    assert cli.main(['remove', '--state', str(tmp_path), '--scan', 'a', 'b', '--scan', 'c']) == 0

    assert seen[0].scan == (tmp_path / 'a', tmp_path / 'b', tmp_path / 'c')


def test_the_one_scan_path_supplies_the_state_and_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _capture(monkeypatch)

    assert cli.main(['remove', '--scan', str(tmp_path)]) == 0

    assert seen[0].state == tmp_path
    assert seen[0].base == tmp_path


def test_every_short_name_stands_for_the_same_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = tmp_path / 'tree'
    other = tmp_path / 'other'
    staging = tmp_path / 'stag'
    record = tmp_path / 'rec'
    for path in (tree, other, staging, record):
        path.mkdir()
    seen = _capture(monkeypatch)

    argv = [
        'stage',
        '-s', str(tree), str(other),
        '-S', str(record),
        '-b', str(tmp_path),
        '-x', str(staging),
        '-p', str(tree),
    ]  # fmt: skip
    assert cli.main(argv) == 0

    settings = seen[0]
    assert settings.scan == (tree, other)
    assert settings.state == record
    assert settings.base == tmp_path
    assert settings.staging == (staging,)
    assert settings.prefer == (tree,)


def test_the_rel_mode_short_name_stands_for_the_same_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _capture_fix(monkeypatch)

    assert cli.main(['fix', '-s', str(tmp_path), '-S', str(tmp_path), '-r', 'all']) == 0

    assert seen[0].scan == (tmp_path,)
    assert seen[0].state == tmp_path
    assert seen[0].rel_mode == 'all'


def test_several_scan_paths_need_a_state(tmp_path: Path) -> None:
    first = tmp_path / 'a'
    second = tmp_path / 'b'
    first.mkdir()
    second.mkdir()

    assert cli.main(['remove', '--scan', str(first), str(second)]) == 1


def test_every_command_takes_only_the_settings_it_reads(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        cli.main(['status', '--state', str(tmp_path)])
    with pytest.raises(SystemExit):
        cli.main(['status', str(tmp_path), '--scan', str(tmp_path)])
    with pytest.raises(SystemExit):
        cli.main(['restore', '--state', str(tmp_path), '--base', str(tmp_path)])


def test_a_scan_path_is_required_where_records_are_written(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        cli.main(['remove', '--state', str(tmp_path)])
    with pytest.raises(SystemExit):
        cli.main(['stage', '--scan', str(tmp_path)])


def test_a_staging_area_may_not_sit_inside_a_scan_path(tmp_path: Path) -> None:
    assert cli.main(['stage', '--scan', str(tmp_path), '--staging', str(tmp_path / 's')]) == 1


def test_a_staging_area_that_exists_has_to_be_a_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tree = tmp_path / 'tree'
    tree.mkdir()
    taken = tmp_path / 'stage'
    taken.write_bytes(b'')

    assert cli.main(['stage', '--scan', str(tree), '--staging', str(taken)]) == 1

    assert f'暂存区不是目录：{taken}' in capsys.readouterr().err


def test_relative_paths_follow_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / 'tree').mkdir()
    monkeypatch.chdir(tmp_path)
    seen = _capture(monkeypatch)

    assert cli.main(['remove', '--scan', 'tree']) == 0

    assert seen[0].state == tmp_path / 'tree'
