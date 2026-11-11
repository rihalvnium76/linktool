from pathlib import Path

from linktool import EXIT_OK, log
from linktool import config as config_module
from linktool.config import ConfigError


def run(path: Path | None) -> int:
    target = path if path is not None else Path(config_module.DEFAULT_FILE)
    if target.exists():
        raise ConfigError(f'{target} 已存在，先确认内容或换个 --path')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(config_module.TEMPLATE, encoding='utf-8')
    log.note(f'已写入配置模板 {target}')
    return EXIT_OK
