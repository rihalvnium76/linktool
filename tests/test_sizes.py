import pytest

from linktool.sizes import readable


@pytest.mark.parametrize(
    ('size', 'text'),
    [
        (0, '0 字节'),
        (1023, '1023 字节'),
        (1024, '1.00 KiB'),
        (12345, '12.1 KiB'),
        (1048576, '1.00 MiB'),
        (209715200, '200 MiB'),
        (5 * 1024**3, '5.00 GiB'),
        (1024**5, '1024 TiB'),
    ],
)
def test_a_size_reads_in_the_largest_unit_that_stays_short(size: int, text: str) -> None:
    assert readable(size) == text
