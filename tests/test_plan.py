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


def test_the_json_holds_only_what_the_tool_reads(tmp_path: Path) -> None:
    ask = _interrogator()
    ask.confirm('plan-1234', '执行吗？')
    path = _save(tmp_path, ask)

    payload = json.loads(path.read_text(encoding='utf-8'))
    assert payload == {'version': plan.VERSION, 'command': 'remove', 'args': {'state': '/tree'}}
    lines = _sheet_lines(path)
    assert "'plan-1234' = ''" in lines
    assert not any(line.startswith('# decisions') for line in lines)


def test_the_sheet_lists_the_decisions_with_the_command_s_verb(tmp_path: Path) -> None:
    ask = _interrogator()
    ask.confirm('plan-1', '执行吗？')
    path = tmp_path / 'plan-stage.json'
    decision = plan.Decision(
        source='keep/a.bin',
        reason='偏好目录下唯一',
        identity='1.50 MiB 1234567890ab',
        paths=('other/b.bin', 'other/c.bin'),
    )
    plan.save(path, 'stage', {'state': '/tree'}, ask, (decision,))

    lines = _sheet_lines(path)
    assert '# decisions  这一轮的全部决定' in lines
    assert '# 组 1  1.50 MiB 1234567890ab' in lines
    assert '#   源：keep/a.bin（偏好目录下唯一）' in lines
    assert '#   暂存：other/b.bin' in lines
    assert '#   暂存：other/c.bin' in lines


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


def test_a_fill_without_candidates_accepts_a_path(tmp_path: Path) -> None:
    ask = _interrogator()
    ask.fill('missing-1', '请填一个本机路径作为替代源，或 skip 跳过该组。', ())
    lines = _sheet_lines(_save(tmp_path, ask))
    assert '# 可填：本机路径，或 skip' in lines
