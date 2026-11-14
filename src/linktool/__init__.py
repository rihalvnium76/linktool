'''Deduplicate a tree by removing duplicate files and links, then rebuild them.'''

import hashlib

from pathlib import Path

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_PENDING = 3

DIGEST_LENGTH = 12


def source_digest() -> str:
    '''A digest over this package's own files, which tells two copies of it apart.

    The version number comes from installed metadata and stays the same across every copy
    of one release, while this digest follows the content, so a copy taken without git
    still answers which source it runs.
    '''
    digest = hashlib.blake2s()
    root = Path(__file__).parent
    for path in sorted(root.rglob('*.py')):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:DIGEST_LENGTH]
