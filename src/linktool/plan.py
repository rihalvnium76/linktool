import hashlib
import tomllib

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from linktool import jsonio
from linktool.errors import ToolError

VERSION = 2
INTRO = (
    '每个带引号的行是一道题的答案，填好后重跑同一条命令。空着的题会让命令停在这一轮。\n'
    'confirm 题填 yes 或 no；choice 题填候选序号、候选值或 skip；'
    'fill 题填候选序号、路径或 skip。\n'
    '值用单引号括起，反斜杠原样保留；值里有单引号时改用双引号并把反斜杠写成 \\\\。\n'
    '填了无效值时工具在这道题上方标出原因，等重填。'
)


class PlanError(ToolError):
    '''The stored plan belongs to another command or another set of arguments.'''


@dataclass(frozen=True, slots=True)
class Candidate:
    index: int
    value: str
    label: str


@dataclass(frozen=True, slots=True)
class Question:
    '''One question this run reached, with the answer it settled or the one it waits for.'''

    id: str
    kind: str
    prompt: str
    candidates: tuple[Candidate, ...]
    answer: str | None
    settled: bool
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Decision:
    '''One group's source and the members that give up their place for it.'''

    source: str
    reason: str
    identity: str
    paths: tuple[str, ...]


class Interrogator:
    '''Collects the decisions the tool cannot make, carrying answers across runs.'''

    def __init__(self, previous: Mapping[str, str]) -> None:
        self._previous = previous
        self._asked: list[Question] = []

    @property
    def reached(self) -> tuple[Question, ...]:
        '''Every question this run reached, answered or not, in the order it reached them.'''
        return tuple(self._asked)

    @property
    def pending(self) -> tuple[Question, ...]:
        return tuple(question for question in self._asked if not question.settled)

    def confirm(self, qid: str, prompt: str) -> bool | None:
        answer = self._ask(qid, 'confirm', prompt, (), _confirm)
        if answer is None:
            return None
        return answer == 'yes'

    def choose(self, qid: str, prompt: str, candidates: Sequence[Candidate]) -> str | None:
        return self._ask(qid, 'choice', prompt, candidates, _selection(candidates))

    def fill(self, qid: str, prompt: str, candidates: Sequence[Candidate]) -> str | None:
        return self._ask(qid, 'fill', prompt, candidates, _free_form(candidates))

    def _ask(
        self,
        qid: str,
        kind: str,
        prompt: str,
        candidates: Sequence[Candidate],
        validate: Callable[[str], str | None],
    ) -> str | None:
        raw = self._previous.get(qid)
        if raw is not None and raw.strip():
            valid = validate(raw)
            if valid is not None:
                self._asked.append(
                    Question(qid, kind, prompt, tuple(candidates), valid, settled=True)
                )
                return valid
            self._asked.append(
                Question(
                    qid,
                    kind,
                    prompt,
                    tuple(candidates),
                    raw,
                    settled=False,
                    error='已填的答案无效，按注释里的可填项重填',
                )
            )
            return None
        self._asked.append(Question(qid, kind, prompt, tuple(candidates), None, settled=False))
        return None


def short_id(kind: str, *parts: str) -> str:
    '''A stable question id whose prefix names what the question is about.'''
    seed = '\0'.join(parts).encode()
    return f'{kind}-{hashlib.blake2s(seed, digest_size=4).hexdigest()}'


def sheet(path: Path) -> Path:
    '''The answer sheet beside a plan's JSON file, which holds everything but the answers.'''
    return path.with_suffix('.toml')


def answers(path: Path, command: str, args: Mapping[str, str]) -> dict[str, str]:
    '''Answers written into this invocation's answer sheet, if it belongs to it.'''
    if not path.exists():
        return {}
    payload = jsonio.mapping(jsonio.read(path), 'plan.json')
    version = jsonio.maybe_integer(payload, 'version')
    if version != VERSION:
        raise PlanError(
            f'{path} 的计划版本是 {version}，本版本只读版本 {VERSION}。\n'
            '删掉这个文件与它旁边的 .toml 答卷，重跑同一条命令会重新生成。'
        )
    stored_command = jsonio.maybe_text(payload, 'command')
    stored_args = {
        key: str(value) for key, value in jsonio.mapping(payload.get('args'), 'args').items()
    }
    if stored_command != command or stored_args != dict(args):
        raise PlanError(
            f'{path} 属于「{stored_command}」命令或另一组参数。\n'
            '改回原来的参数，或把 args 段改成这次的参数再重跑，已填的答案会继续沿用。'
            '删掉这个文件与它的 .toml 答卷则重新提问。'
        )
    found = _read_sheet(sheet(path))
    return {key: value for key, value in found.items() if value.strip()}


def save(
    path: Path,
    command: str,
    args: Mapping[str, str],
    ask: Interrogator,
    decisions: Sequence[Decision],
) -> None:
    jsonio.write(path, {'version': VERSION, 'command': command, 'args': dict(args)})
    sheet(path).write_text(_render(ask.reached, decisions, command, path.name), encoding='utf-8')


def discard(path: Path) -> None:
    path.unlink(missing_ok=True)
    sheet(path).unlink(missing_ok=True)


def _render(
    questions: Sequence[Question], decisions: Sequence[Decision], command: str, plan_name: str
) -> str:
    lines = [f'# 本文件与 {plan_name} 配套，工具只从这份文件读答案。']
    lines.extend(f'# {line}' for line in INTRO.splitlines())
    lines.append('')
    if decisions:
        lines.extend(_decisions_block(decisions, command))
        lines.append('')
    for question in questions:
        lines.extend(_block(question))
        lines.append('')
    return '\n'.join(lines)


def _decisions_block(decisions: Sequence[Decision], command: str) -> list[str]:
    action = _ACTIONS.get(command, '移出原位')
    lines = ['# decisions  这一轮的全部决定']
    for number, decision in enumerate(decisions, start=1):
        lines.extend(
            (
                '',
                f'# 组 {number}  {decision.identity}',
                f'#   源：{decision.source}（{decision.reason}）',
            )
        )
        lines.extend(f'#   {action}：{path}' for path in decision.paths)
    return lines


_ACTIONS = {'remove': '移除', 'stage': '暂存'}


def _block(question: Question) -> list[str]:
    lines = [f'# {question.id}  {_KINDS[question.kind]}']
    lines.extend(f'# {line}' for line in question.prompt.splitlines())
    lines.append(f'# 可填：{_accepted(question)}')
    lines.extend(
        f'#   {candidate.index}  {candidate.value}  {candidate.label}'
        for candidate in question.candidates
    )
    if question.error is not None:
        lines.append(f'# {question.error}')
    elif question.settled:
        lines.append('# 已采纳')
    answer = question.answer or ''
    lines.append(f'{_toml_value(question.id)} = {_toml_value(answer)}')
    return lines


_KINDS = {'confirm': '确认', 'choice': '选择', 'fill': '填写'}


def _accepted(question: Question) -> str:
    if question.kind == 'confirm':
        return 'yes 或 no'
    if question.kind == 'fill':
        if not question.candidates:
            return '本机路径，或 skip'
        return '候选序号、本机路径，或 skip'
    values = '、'.join(candidate.value for candidate in question.candidates)
    return (
        f'{values}、序号 {question.candidates[0].index}～{question.candidates[-1].index}，或 skip'
    )


def _toml_value(text: str) -> str:
    '''Quote a key or value so a Windows path keeps its backslashes as typed.'''
    if "'" in text or '\n' in text:
        escaped = text.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
        return f'"{escaped}"'
    return f"'{text}'"


def _read_sheet(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = tomllib.loads(path.read_text(encoding='utf-8'))
    except tomllib.TOMLDecodeError as error:
        raise PlanError(
            f'{path} 不是有效的 TOML：{error}\n'
            "值请用单引号括起；用双引号时反斜杠要写成 \\\\，例如 'D:\\a\\b'。"
        ) from error
    return {key: value for key, value in payload.items() if isinstance(value, str)}


def _confirm(raw: str) -> str | None:
    answer = raw.strip().lower()
    return answer if answer in {'yes', 'no'} else None


def _selection(candidates: Sequence[Candidate]) -> Callable[[str], str | None]:
    allowed = {candidate.value for candidate in candidates} | {'skip'}

    def validate(raw: str) -> str | None:
        answer = raw.strip()
        if answer.isdigit():
            index = int(answer)
            for candidate in candidates:
                if candidate.index == index:
                    return candidate.value
            return None
        return answer if answer in allowed else None

    return validate


def _free_form(candidates: Sequence[Candidate]) -> Callable[[str], str | None]:
    pick = _selection(candidates)

    def validate(raw: str) -> str | None:
        answer = raw.strip()
        if not answer:
            return None
        if answer.isdigit():
            return pick(answer)
        return answer

    return validate
