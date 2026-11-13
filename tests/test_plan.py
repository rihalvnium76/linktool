import json

from pathlib import Path

import pytest

from linktool import plan


def _interrogator(previous: dict[str, str] | None = None) -> plan.Interrogator:
    return plan.Interrogator(previous or {})


def _save(tmp_path: Path, ask: plan.Interrogator) -> Path:
    path = tmp_path / 'plan-remove.json'
    plan.save(path, 'remove', {'state': '/tree'}, ask, ())
    return path


def _sheet_lines(plan_path: Path) -> list[str]:
    return plan.sheet(plan_path).read_text(encoding='utf-8').splitlines()


def _set(plan_path: Path, qid: str, value: str) -> None:
    sheet = plan.sheet(plan_path)
    lines = sheet.read_text(encoding='utf-8').splitlines()
    for index, line in enumerate(lines):
        if line.startswith(f"'{qid}' = "):
            lines[index] = f"'{qid}' = {value}"
    sheet.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def test_a_plan_keeps_the_answers_in_the_sheet(tmp_path: Path) -> None:
    ask = _interrogator()
    ask.confirm('plan-1234', '执行吗？')
    path = _save(tmp_path, ask)

    payload = json.loads(path.read_text(encoding='utf-8'))
    assert payload['version'] == plan.VERSION
    assert payload['command'] == 'remove'
    assert payload['questions'] == [
        {
            'id': 'plan-1234',
            'kind': 'confirm',
            'prompt': '执行吗？',
            'candidates': [],
            'error': None,
        }
    ]
    assert "'plan-1234' = ''" in _sheet_lines(path)


def test_an_answer_in_the_sheet_is_reused(tmp_path: Path) -> None:
    ask = _interrogator()
    assert ask.confirm('plan-1234', '执行吗？') is None
    path = _save(tmp_path, ask)
    _set(path, 'plan-1234', "'yes'")

    assert plan.answers(path, 'remove', {'state': '/tree'}) == {'plan-1234': 'yes'}
    again = _interrogator(plan.answers(path, 'remove', {'state': '/tree'}))
    assert again.confirm('plan-1234', '执行吗？') is True
    assert again.pending == ()
    assert [question.settled for question in again.reached] == [True]


def test_a_multi_line_prompt_keeps_its_lines(tmp_path: Path) -> None:
    ask = _interrogator()
    ask.confirm('scan-1', '这些路径在基准之外：\n  /a\n  /b')
    lines = _sheet_lines(_save(tmp_path, ask))
    assert '#   /a' in lines
    assert all(not line or line.startswith('#') or '=' in line for line in lines)


def test_a_windows_path_survives_the_sheet(tmp_path: Path) -> None:
    ask = _interrogator()
    ask.fill('fix-1', '填一个本机路径', ())
    path = _save(tmp_path, ask)
    _set(path, 'fix-1', r"'D:\tree\a.bin'")

    seen = plan.answers(path, 'remove', {'state': '/tree'})
    again = _interrogator(seen)
    assert again.fill('fix-1', '填一个本机路径', ()) == r'D:\tree\a.bin'


def test_an_invalid_answer_is_kept_and_marked(tmp_path: Path) -> None:
    ask = _interrogator()
    ask.confirm('plan-1', '执行吗？')
    path = _save(tmp_path, ask)
    _set(path, 'plan-1', "'maybe'")

    again = _interrogator(plan.answers(path, 'remove', {'state': '/tree'}))
    assert again.confirm('plan-1', '执行吗？') is None
    _save(tmp_path, again)

    lines = _sheet_lines(path)
    assert "'plan-1' = 'maybe'" in lines
    assert any('已填的答案无效' in line for line in lines)


def test_a_plan_for_other_arguments_is_refused(tmp_path: Path) -> None:
    ask = _interrogator()
    ask.confirm('plan-1', '执行吗？')
    path = _save(tmp_path, ask)
    with pytest.raises(plan.PlanError, match='属于'):
        plan.answers(path, 'remove', {'state': '/other'})


def test_a_broken_sheet_names_itself(tmp_path: Path) -> None:
    ask = _interrogator()
    ask.confirm('plan-1', '执行吗？')
    path = _save(tmp_path, ask)
    plan.sheet(path).write_text("'plan-1' = 'yes\n", encoding='utf-8')
    with pytest.raises(plan.PlanError, match='不是有效的 TOML'):
        plan.answers(path, 'remove', {'state': '/tree'})


def test_discarding_a_plan_removes_both_files(tmp_path: Path) -> None:
    ask = _interrogator()
    ask.confirm('plan-1', '执行吗？')
    path = _save(tmp_path, ask)
    plan.discard(path)
    assert not path.exists()
    assert not plan.sheet(path).exists()
