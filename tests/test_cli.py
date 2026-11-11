import os
import subprocess
import sys

from pathlib import Path

import pytest

from linktool import cli, config
from linktool.commands import dedupe

SOURCE = Path(__file__).resolve().parent.parent / 'src'


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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[config.Config] = []

    def capture(settings: config.Config, **_options: bool) -> int:
        seen.append(settings)
        return 0

    monkeypatch.setattr(dedupe, 'run', capture)

    assert cli.main(['remove', '--root', 'T', '--scan', 'a', 'b', '--scan', 'c']) == 0

    assert seen[0].scan == (Path.cwd() / 'a', Path.cwd() / 'b', Path.cwd() / 'c')
