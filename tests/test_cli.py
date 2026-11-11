import os
import subprocess
import sys

from pathlib import Path

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
