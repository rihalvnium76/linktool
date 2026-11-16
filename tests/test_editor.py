'''Whether the answer sheet reaches the editor the environment names.'''

import os
import subprocess
import sys

from pathlib import Path

import pytest

from linktool import editor
from linktool.cli import main
from linktool.config import OpenMode


class Spawn:
    '''Stands in for the editor process, which a test cannot drive.'''

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> None:
        self.calls.append(argv)


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _tty(monkeypatch: pytest.MonkeyPatch, value: bool) -> None:
    monkeypatch.setattr(sys.stdout, 'isatty', lambda: value)


def _spawn(monkeypatch: pytest.MonkeyPatch) -> Spawn:
    spawn = Spawn()
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    return spawn


def test_the_sheet_opens_with_the_command_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawn = _spawn(monkeypatch)
    monkeypatch.setenv('VISUAL', 'vi -f')
    monkeypatch.delenv('EDITOR', raising=False)
    _tty(monkeypatch, True)
    sheet = tmp_path / 'plan-remove.toml'

    editor.open_sheet(sheet, OpenMode.ALWAYS, first_time=True)

    assert spawn.calls == [['vi', '-f', str(sheet)]]


def test_the_other_variable_answers_when_the_first_is_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawn = _spawn(monkeypatch)
    monkeypatch.delenv('VISUAL', raising=False)
    monkeypatch.setenv('EDITOR', 'notepad')
    _tty(monkeypatch, True)

    editor.open_sheet(tmp_path / 'plan.toml', OpenMode.ALWAYS, first_time=True)

    assert spawn.calls == [['notepad', str(tmp_path / 'plan.toml')]]


def test_never_leaves_the_sheet_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spawn = _spawn(monkeypatch)
    monkeypatch.setenv('VISUAL', 'vi')
    _tty(monkeypatch, True)

    editor.open_sheet(tmp_path / 'plan.toml', OpenMode.NEVER, first_time=True)

    assert spawn.calls == []


def test_once_opens_a_fresh_sheet_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spawn = _spawn(monkeypatch)
    monkeypatch.setenv('VISUAL', 'vi')
    _tty(monkeypatch, True)
    sheet = tmp_path / 'plan.toml'

    editor.open_sheet(sheet, OpenMode.ONCE, first_time=False)
    assert spawn.calls == []
    editor.open_sheet(sheet, OpenMode.ONCE, first_time=True)
    assert spawn.calls == [['vi', str(sheet)]]


def test_a_piped_run_leaves_the_sheet_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawn = _spawn(monkeypatch)
    monkeypatch.setenv('VISUAL', 'vi')
    _tty(monkeypatch, False)

    editor.open_sheet(tmp_path / 'plan.toml', OpenMode.ALWAYS, first_time=True)

    assert spawn.calls == []


def test_an_unset_editor_leaves_the_sheet_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawn = _spawn(monkeypatch)
    monkeypatch.delenv('VISUAL', raising=False)
    monkeypatch.delenv('EDITOR', raising=False)
    _tty(monkeypatch, True)

    editor.open_sheet(tmp_path / 'plan.toml', OpenMode.ALWAYS, first_time=True)

    assert spawn.calls == []


def test_a_failed_launch_only_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def missing(argv: list[str]) -> None:
        raise FileNotFoundError(2, '找不到文件')

    monkeypatch.setattr(subprocess, 'Popen', missing)
    monkeypatch.setenv('VISUAL', 'vi')
    _tty(monkeypatch, True)

    editor.open_sheet(tmp_path / 'plan.toml', OpenMode.ALWAYS, first_time=True)

    assert 'warn: 编辑器没能打开：' in capsys.readouterr().err


@pytest.mark.skipif(os.name != 'nt', reason='双引号保空格是 Windows 命令行的写法')
def test_a_quoted_windows_path_keeps_its_spaces() -> None:
    assert editor.split_command('"C:\\Program Files\\Vim\\gvim.exe" --nofork') == [
        'C:\\Program Files\\Vim\\gvim.exe',
        '--nofork',
    ]


def test_a_pending_run_opens_the_sheet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / 'tree'
    _write(root / 'a.bin', b'payload')
    _write(root / 'b.bin', b'payload')
    spawn = _spawn(monkeypatch)
    monkeypatch.setenv('VISUAL', 'vi')
    _tty(monkeypatch, True)

    assert main(['remove', '--scan', str(root), '--pick', 'first']) == 3

    assert spawn.calls == [['vi', str(root / '.linktool' / 'plan-remove.toml')]]


def test_once_does_not_reopen_a_sheet_already_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / 'tree'
    _write(root / 'a.bin', b'payload')
    _write(root / 'b.bin', b'payload')
    spawn = _spawn(monkeypatch)
    monkeypatch.setenv('VISUAL', 'vi')
    _tty(monkeypatch, True)
    args = ['remove', '--scan', str(root), '--pick', 'first', '--open', 'once']

    assert main(args) == 3
    assert len(spawn.calls) == 1
    assert main(args) == 3
    assert len(spawn.calls) == 1


def test_never_keeps_the_sheet_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / 'tree'
    _write(root / 'a.bin', b'payload')
    _write(root / 'b.bin', b'payload')
    spawn = _spawn(monkeypatch)
    monkeypatch.setenv('VISUAL', 'vi')
    _tty(monkeypatch, True)

    assert main(['remove', '--scan', str(root), '--pick', 'first', '--open', 'never']) == 3

    assert spawn.calls == []
