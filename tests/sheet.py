'''Reading an answer sheet the way its reader sees it.'''

import re

from dataclasses import dataclass
from pathlib import Path

_KINDS = {'确认': 'confirm', '选择': 'choice', '填写': 'fill'}
_HEAD = re.compile(r'# ([a-z]+-[0-9a-f]{8})  (确认|选择|填写)')
_CANDIDATE = re.compile(r' \d+  ')
_GROUP = re.compile(r'# 组 \d+  (.*)')
_SOURCE = re.compile(r'#   源：(.*)（(.*)）')
_ACTION = re.compile(r'#   (?:移除|暂存)：')


@dataclass(frozen=True)
class SheetQuestion:
    id: str
    kind: str
    prompt: str
    error: str | None


@dataclass(frozen=True)
class SheetDecision:
    identity: str
    source: str
    reason: str
    paths: tuple[str, ...]


def questions(plan_path: Path) -> list[SheetQuestion]:
    found: list[SheetQuestion] = []
    block: list[str] = []
    for line in _lines(plan_path):
        if line.startswith(("'", '"')):
            found.append(_question(block))
            block = []
        elif _HEAD.fullmatch(line) is not None:
            block = [line]
        elif block:
            block.append(line)
    return found


def decisions(plan_path: Path) -> list[SheetDecision]:
    found: list[SheetDecision] = []
    for paragraph in _paragraphs(_lines(plan_path)):
        group = _GROUP.fullmatch(paragraph[0])
        if group is None:
            continue
        source = reason = ''
        paths: list[str] = []
        for line in paragraph[1:]:
            pair = _SOURCE.fullmatch(line)
            if pair is not None:
                source, reason = pair.group(1), pair.group(2)
            elif _ACTION.match(line) is not None:
                paths.append(line.split('：', 1)[1])
        found.append(SheetDecision(group.group(1), source, reason, tuple(paths)))
    return found


def _question(block: list[str]) -> SheetQuestion:
    head = _HEAD.fullmatch(block[0])
    assert head is not None
    prompt: list[str] = []
    error: str | None = None
    past_options = False
    for line in block[1:]:
        text = line[2:]
        if text.startswith('可填：'):
            past_options = True
        elif not past_options:
            prompt.append(text)
        elif _CANDIDATE.match(text) or text == '已采纳':
            continue
        else:
            error = text
    return SheetQuestion(head.group(1), _KINDS[head.group(2)], '\n'.join(prompt), error)


def _lines(plan_path: Path) -> list[str]:
    return plan_path.with_suffix('.toml').read_text(encoding='utf-8').splitlines()


def _paragraphs(lines: list[str]) -> list[list[str]]:
    found: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line:
            current.append(line)
        elif current:
            found.append(current)
            current = []
    if current:
        found.append(current)
    return found
