'''Byte counts written the way a person reads them.'''

_UNITS = ('KiB', 'MiB', 'GiB', 'TiB')


def readable(size: int) -> str:
    '''A byte count in the largest binary unit that keeps the number short.'''
    if size < 1024:
        return f'{size} 字节'
    value = size / 1024
    unit = 0
    while value >= 1024 and unit < len(_UNITS) - 1:
        value /= 1024
        unit += 1
    digits = 2 if value < 10 else 1 if value < 100 else 0
    return f'{value:.{digits}f} {_UNITS[unit]}'
