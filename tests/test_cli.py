import os
import subprocess
import sys

from pathlib import Path

import pytest

from linktool import cli, config
from linktool.commands import dedupe

SOURCE = Path(__file__).resolve().parent.parent / 'src'


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[config.Config]:
    seen: list[config.Config] = []

    def capture(settings: config.Config, **_options: bool) -> int:
        seen.append(settings)
        return 0

    monkeypatch.setattr(dedupe, 'run', capture)
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
    assert result.stdout.startswith('linktool ')


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


def test_several_scan_paths_need_a_state(tmp_path: Path) -> None:
    first = tmp_path / 'a'
    second = tmp_path / 'b'
    first.mkdir()
    second.mkdir()

    assert cli.main(['remove', '--scan', str(first), str(second)]) == 1


def test_every_command_takes_only_the_settings_it_reads(tmp_path: Path) -> None:
    assert cli.main(['status', '--state', str(tmp_path)]) == 0
    with pytest.raises(SystemExit):
        cli.main(['status', '--state', str(tmp_path), '--scan', str(tmp_path)])
    with pytest.raises(SystemExit):
        cli.main(['status', '--state', str(tmp_path), '--staging', str(tmp_path)])


def test_a_scan_path_is_required_where_records_are_written(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        cli.main(['remove', '--state', str(tmp_path)])
    with pytest.raises(SystemExit):
        cli.main(['stage', '--scan', str(tmp_path)])


def test_a_staging_area_may_not_sit_inside_a_scan_path(tmp_path: Path) -> None:
    assert cli.main(['stage', '--scan', str(tmp_path), '--staging', str(tmp_path / 's')]) == 1


def test_relative_paths_follow_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / 'tree').mkdir()
    monkeypatch.chdir(tmp_path)
    seen = _capture(monkeypatch)

    assert cli.main(['remove', '--scan', 'tree']) == 0

    assert seen[0].state == tmp_path / 'tree'
