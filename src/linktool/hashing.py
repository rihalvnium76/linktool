from dataclasses import dataclass
from pathlib import Path

import xxhash

CHUNK = 1 << 20
SAMPLE = 4096
SAMPLE_MIN = 1 << 20


@dataclass(frozen=True, slots=True)
class Identity:
    size: int
    digest: str


def full(size: int, path: Path) -> Identity:
    hasher = xxhash.xxh3_128()
    with path.open('rb') as handle:
        while chunk := handle.read(CHUNK):
            hasher.update(chunk)
    return Identity(size=size, digest=hasher.hexdigest())


def quick(size: int, path: Path) -> str:
    '''Digest of the first and last sample, mixed with the size.

    Only files of at least SAMPLE_MIN get one: below that, reading both samples costs as
    much as reading the whole file.
    '''
    hasher = xxhash.xxh3_128()
    hasher.update(str(size).encode())
    with path.open('rb') as handle:
        hasher.update(handle.read(SAMPLE))
        if size > 2 * SAMPLE:
            handle.seek(size - SAMPLE)
            hasher.update(handle.read(SAMPLE))
    return hasher.hexdigest()
