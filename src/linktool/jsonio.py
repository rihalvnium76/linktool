import json

from pathlib import Path
from typing import cast

from linktool.errors import ToolError
from linktool.fs import write_atomic


class FormatError(ToolError):
    '''A stored file does not hold the shape this version of the tool writes.'''


def read(path: Path) -> object:
    return json.loads(path.read_text(encoding='utf-8'))


def write(path: Path, payload: object) -> None:
    write_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2) + '\n')


def mapping(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise FormatError(f'{what} 不是 JSON 对象')
    return cast(dict[str, object], value)


def sequence(value: object, what: str) -> list[object]:
    if not isinstance(value, list):
        raise FormatError(f'{what} 不是 JSON 数组')
    return cast(list[object], value)


def texts(value: object, what: str) -> list[str]:
    found: list[str] = []
    for item in sequence(value, what):
        if not isinstance(item, str):
            raise FormatError(f'{what} 里有不是字符串的项')
        found.append(item)
    return found


def text(source: dict[str, object], key: str) -> str:
    value = source.get(key)
    if not isinstance(value, str):
        raise FormatError(f'字段 {key} 缺失或不是字符串')
    return value


def maybe_text(source: dict[str, object], key: str) -> str | None:
    value = source.get(key)
    return value if isinstance(value, str) else None


def integer(source: dict[str, object], key: str) -> int:
    value = source.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise FormatError(f'字段 {key} 缺失或不是整数')
    return value


def maybe_integer(source: dict[str, object], key: str) -> int | None:
    value = source.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def flag(source: dict[str, object], key: str) -> bool | None:
    value = source.get(key)
    return value if isinstance(value, bool) else None
