import sys


def warn(message: str) -> None:
    sys.stderr.write(f'warn: {message}\n')


def note(message: str) -> None:
    sys.stderr.write(f'{message}\n')
