import hashlib

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from linktool import jsonio
from linktool.errors import ToolError

INTRO = (
    '本文件由 linktool 生成，列出它无法自行决定的问题。\n'
    '在每一题的 answer 字段填入答案，然后重新运行同一条命令；'
    '命令行参数必须与生成本文件时一致。\n'
    'confirm 题填 yes 或 no；choice 题填候选序号、候选值或 skip；'
    'fill 题填候选序号、路径或 skip。\n'
    '工具不会猜你的意思：填了无效答案会在这里标出原因，等你重填。'
)


class PlanError(ToolError):
    '''The stored plan belongs to another command or another set of arguments.'''


@dataclass(frozen=True, slots=True)
class Candidate:
    index: int
    value: str
    label: str


@dataclass(slots=True)
class Question:
    id: str
    kind: str
    prompt: str
    candidates: tuple[Candidate, ...]
    answer: str | None
    error: str | None

    def to_json(self) -> dict[str, object]:
        return {
            'id': self.id,
            'kind': self.kind,
            'prompt': self.prompt,
            'candidates': [
                {'index': item.index, 'value': item.value, 'label': item.label}
                for item in self.candidates
            ],
            'answer': self.answer,
            'error': self.error,
        }


class Interrogator:
    '''Collects the decisions the tool cannot make, carrying answers across runs.'''

    def __init__(self, previous: Mapping[str, str]) -> None:
        self._previous = previous
        self._questions: list[Question] = []

    @property
    def questions(self) -> tuple[Question, ...]:
        return tuple(self._questions)

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
        error = None
        if raw is not None and raw.strip():
            valid = validate(raw)
            if valid is not None:
                return valid
            error = '答案无效，请按 prompt 的说明重填'
        self._questions.append(
            Question(
                id=qid,
                kind=kind,
                prompt=prompt,
                candidates=tuple(candidates),
                answer=raw or None,
                error=error,
            )
        )
        return None


def short_id(*parts: str) -> str:
    seed = '\0'.join(parts).encode()
    return hashlib.blake2s(seed, digest_size=4).hexdigest()


def answers(path: Path, command: str, args: Mapping[str, str]) -> dict[str, str]:
    '''Answers already written into the plan file, if it belongs to this invocation.'''
    if not path.exists():
        return {}
    payload = jsonio.mapping(jsonio.read(path), 'plan.json')
    stored_command = jsonio.maybe_text(payload, 'command')
    stored_args = {
        key: str(value) for key, value in jsonio.mapping(payload.get('args'), 'args').items()
    }
    if stored_command != command or stored_args != dict(args):
        raise PlanError(
            f'{path} 属于「{stored_command}」命令或另一组参数。\n'
            '改回原来的参数，或删除该文件后重新运行。'
        )
    found: dict[str, str] = {}
    for item in jsonio.sequence(payload.get('questions'), 'questions'):
        question = jsonio.mapping(item, 'question')
        answer = jsonio.maybe_text(question, 'answer')
        qid = jsonio.maybe_text(question, 'id')
        if qid is not None and answer is not None and answer.strip():
            found[qid] = answer
    return found


def save(path: Path, command: str, args: Mapping[str, str], questions: Sequence[Question]) -> None:
    jsonio.write(
        path,
        {
            'version': 1,
            'command': command,
            'args': dict(args),
            'intro': INTRO,
            'questions': [question.to_json() for question in questions],
        },
    )


def discard(path: Path) -> None:
    path.unlink(missing_ok=True)


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
