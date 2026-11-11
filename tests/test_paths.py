import os

from pathlib import Path

from linktool.paths import RelMode, from_rel, is_within, link_text, strip_verbatim, to_rel


def test_strip_verbatim_drive() -> None:
    assert strip_verbatim('\\\\?\\D:\\a\\b') == 'D:\\a\\b'


def test_strip_verbatim_unc() -> None:
    assert strip_verbatim('\\\\?\\UNC\\srv\\share\\x') == '\\\\srv\\share\\x'


def test_strip_verbatim_leaves_plain_text() -> None:
    assert strip_verbatim('D:\\a') == 'D:\\a'
    assert strip_verbatim('\\\\srv\\share\\x') == '\\\\srv\\share\\x'


def test_relative_round_trip(tmp_path: Path) -> None:
    nested = tmp_path / 'a' / 'b'
    nested.mkdir(parents=True)
    rel = to_rel(nested / 'x.bin', tmp_path)
    assert rel == 'a/b/x.bin'
    assert from_rel(tmp_path, rel) == nested / 'x.bin'


def test_is_within(tmp_path: Path) -> None:
    child = tmp_path / 'a' / 'b'
    child.mkdir(parents=True)
    assert is_within(child, tmp_path)
    assert is_within(tmp_path, tmp_path)
    assert not is_within(tmp_path.parent, tmp_path)


def test_link_text_keeps_relative_when_both_under_base(tmp_path: Path) -> None:
    source = tmp_path / 'a' / 'x.bin'
    link = tmp_path / 'b' / 'y.lnk'
    text = link_text(source, link, tmp_path, RelMode.BASE)
    assert text == os.path.join('..', 'a', 'x.bin')


def test_link_text_keeps_absolute_outside_base(tmp_path: Path) -> None:
    source = tmp_path / 'a' / 'x.bin'
    link = tmp_path / 'b' / 'y.lnk'
    assert link_text(source, link, tmp_path / 'b', RelMode.BASE) == str(source)


def test_link_text_off_is_absolute(tmp_path: Path) -> None:
    source = tmp_path / 'a' / 'x.bin'
    link = tmp_path / 'a' / 'y.lnk'
    assert link_text(source, link, tmp_path, RelMode.OFF) == str(source)


def test_link_text_all_goes_relative_even_outside_base(tmp_path: Path) -> None:
    source = tmp_path / 'a' / 'x.bin'
    link = tmp_path / 'b' / 'y.lnk'
    text = link_text(source, link, tmp_path / 'b', RelMode.ALL)
    assert text == os.path.join('..', 'a', 'x.bin')
