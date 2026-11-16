import os
import stat

from dataclasses import dataclass, field
from pathlib import Path

from linktool import EXIT_OK, errors, hashing, log, plan, scan, sizes, state
from linktool.commands import shared
from linktool.config import Config
from linktool.fs import Kind, create_symlink, remove_path, set_times
from linktool.hashing import Identity
from linktool.paths import link_text, plain


@dataclass(slots=True)
class Finder:
    '''Existing files and directories, indexed by size and by name.

    The size index keeps the full digest off every file that cannot match: only a size
    that some broken link asks for gets read.
    '''

    by_size: dict[int, list[Path]] = field(default_factory=dict[int, list[Path]])
    by_name: dict[str, list[Path]] = field(default_factory=dict[str, list[Path]])
    dirs_by_name: dict[str, list[Path]] = field(default_factory=dict[str, list[Path]])
    digests: dict[str, Identity] = field(default_factory=dict[str, Identity])

    @classmethod
    def build(cls, result: scan.ScanResult) -> Finder:
        finder = cls()
        for entry in result.entries:
            target = entry.path if entry.kind is Kind.FILE else Path(os.path.realpath(entry.path))
            try:
                info = os.stat(target)
            except OSError:
                continue
            if stat.S_ISDIR(info.st_mode):
                finder._add_dir(target)
            elif stat.S_ISREG(info.st_mode):
                finder._add_file(target, info.st_size)
        for path in result.dirs:
            finder._add_dir(path)
        return finder

    def _add_file(self, path: Path, size: int) -> None:
        bucket = self.by_size.setdefault(size, [])
        if not any(plain(item) == plain(path) for item in bucket):
            bucket.append(path)
        named = self.by_name.setdefault(path.name, [])
        if not any(plain(item) == plain(path) for item in named):
            named.append(path)

    def _add_dir(self, path: Path) -> None:
        named = self.dirs_by_name.setdefault(path.name, [])
        if not any(plain(item) == plain(path) for item in named):
            named.append(path)

    def match_by_identity(self, identity: Identity | None) -> list[Path]:
        if identity is None:
            return []
        return self._by_identity(identity)

    def match_by_name(self, old_target: str | None) -> list[Path]:
        name = Path(old_target).name if old_target else ''
        if not name:
            return []
        return [*self.by_name.get(name, []), *self.dirs_by_name.get(name, [])]

    def _by_identity(self, identity: Identity) -> list[Path]:
        found: list[Path] = []
        for path in self.by_size.get(identity.size, []):
            key = plain(path)
            digest = self.digests.get(key)
            if digest is None:
                try:
                    digest = hashing.full(identity.size, path)
                except OSError as error:
                    log.warn(f'跳过无法读取的文件 {path}: {error.strerror}')
                    continue
                self.digests[key] = digest
            if digest == identity:
                found.append(path)
        return found


def run(config: Config) -> int:
    folder = state.workdir(config.state)
    plan_path = folder / 'plan-fix.json'
    args = shared.snapshot(config)
    store = state.StateStore(folder)
    ask = plan.Interrogator(plan.answers(plan_path, 'fix', args))

    base = shared.effective_base(store, config, ask)
    gate = shared.scan_gate(ask, config.scan, base if base is not None else config.base)
    if base is None or gate is None:
        return shared.await_answers(plan_path, 'fix', args, ask)
    if not gate:
        raise errors.ToolError('基准目录之外的扫描路径未获确认，本次未执行')

    result = scan.collect(config.scan, [folder])
    broken = sorted(
        (
            entry
            for entry in result.entries
            if entry.kind is not Kind.FILE and not os.path.exists(entry.path)
        ),
        key=lambda entry: plain(entry.path),
    )
    if not broken:
        log.note('没有发现失效的符号链接')
        return EXIT_OK

    finder = Finder.build(result)
    actions: list[tuple[scan.Entry, Path]] = []
    for entry in broken:
        label = shared.record_label(base, entry.path)
        found = store.find(label)
        identity = found[0].identity if found is not None else None
        exact = finder.match_by_identity(identity)
        if len(exact) == 1:
            actions.append((entry, exact[0]))
            continue
        candidates = exact or finder.match_by_name(entry.link_text)
        answer = ask.fill(
            plan.short_id('fix', label),
            _prompt(entry, identity, exact, candidates),
            tuple(
                plan.Candidate(index=index, value=str(path), label=_label(identity, path, exact))
                for index, path in enumerate(candidates, start=1)
            ),
        )
        if answer is None or answer == 'skip':
            continue
        source = Path(answer)
        if not source.exists():
            log.warn(f'候选源不存在：{source}')
            continue
        actions.append((entry, source))

    if ask.pending:
        return shared.await_answers(plan_path, 'fix', args, ask)

    for entry, source in actions:
        text = link_text(source, entry.path, base, config.rel_mode)
        label = shared.record_label(base, entry.path)
        store.begin_relink(label, text, entry.mtime_ns, entry.birth_ns)
        remove_path(entry.path)
        create_symlink(text, entry.path, target_is_dir=source.is_dir())
        set_times(entry.path, entry.mtime_ns, follow=False, birth_ns=entry.birth_ns)
    store.flush()
    plan.discard(plan_path)
    log.note(f'已修复 {len(actions)} 条链接，未处理 {len(broken) - len(actions)} 条')
    return EXIT_OK


def _prompt(
    entry: scan.Entry, identity: Identity | None, exact: list[Path], candidates: list[Path]
) -> str:
    recorded = (
        f'记录里这份文件是 {sizes.readable(identity.size)}。'
        if identity is not None
        else '记录里没有这一组的信息。'
    )
    return '\n'.join(
        (f'失效链接 {entry.path} 原本指向 {entry.link_text}', recorded, _found(exact, candidates))
    )


def _found(exact: list[Path], candidates: list[Path]) -> str:
    '''How the candidates were matched, which is all the tool knows about them.'''
    tail = '填候选序号、本机路径，或 skip 跳过。'
    if not candidates:
        return f'没有找到同名的条目。{tail}'
    if exact:
        return f'找到 {len(exact)} 个大小与摘要都与记录一致的文件。{tail}'
    return f'找到 {len(candidates)} 个{_matched(candidates)}。{tail}'


def _matched(candidates: list[Path]) -> str:
    dirs = sum(1 for path in candidates if path.is_dir())
    if not dirs:
        return '同名文件'
    if dirs == len(candidates):
        return '同名目录'
    return '同名文件或目录'


def _label(identity: Identity | None, path: Path, exact: list[Path]) -> str:
    '''What tells this candidate apart from the record and from the other candidates.'''
    info = path.stat()
    if stat.S_ISDIR(info.st_mode):
        return '目录'
    if identity is None or exact:
        return ''
    if info.st_size == identity.size:
        return '大小相同，内容不同'
    return f'大小 {sizes.readable(info.st_size)}，与记录不同'
