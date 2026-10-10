"""
core/system/logfiles.py
-----------------------
Логи с ограничением размера: когда файл дорастает до предела, он уходит в
logs/archive/ (flowzap.1.log — предыдущий, flowzap.2.log — ещё раньше), а в
logs/ остаются только текущие файлы — людям, которые ищут flowzap.log, не
нужно разбираться в куче копий. Без Qt: нужен и скрытому процессу TG Proxy.
"""

import logging.handlers
import os
import re
from pathlib import Path

ARCHIVE_DIR = "archive"


class ArchivedLogHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler, копии которого лежат в logs/archive/<имя>.<N><расширение>."""

    def __init__(self, path: Path, max_bytes: int, backups: int) -> None:
        super().__init__(path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
        path = Path(path)
        archive = path.parent / ARCHIVE_DIR

        def namer(default: str) -> str:
            # «…\flowzap.log.2» → «…\archive\flowzap.2.log»
            number = default.rsplit(".", 1)[1]
            return str(archive / f"{path.stem}.{number}{path.suffix}")

        def rotator(source: str, dest: str) -> None:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if os.path.exists(source):
                os.replace(source, dest)

        self.namer = namer
        self.rotator = rotator


def tidy_old_backups(log_dir: Path) -> None:
    """Копии от прежних версий (flowzap.log.1, tgproxy.log.1 рядом с логами) —
    в archive/ под новыми именами."""
    try:
        for old in Path(log_dir).iterdir():
            m = re.fullmatch(r"(.+)(\.log)\.(\d+)", old.name)
            if not m or not old.is_file():
                continue
            archive = old.parent / ARCHIVE_DIR
            archive.mkdir(exist_ok=True)
            new = archive / f"{m[1]}.{m[3]}{m[2]}"
            if new.exists():
                old.unlink()
            else:
                old.replace(new)
    except OSError:
        pass        # не судьба — останутся рядом, не повод не запускаться
