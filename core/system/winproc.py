"""
core/system/winproc.py
----------------------
Поиск и завершение процессов по имени exe (tasklist / taskkill) — общие
для zapret (winws.exe), проверки пресетов, обновления Core и TG Proxy.
Синхронные: вызывать из фонового потока.
"""

import logging
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000


def find_pid(image: str) -> Optional[int]:
    """PID первого процесса с таким именем exe или None."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"],
            capture_output=True, encoding="cp866", errors="replace",
            creationflags=CREATE_NO_WINDOW, timeout=5,
        )
    except Exception as e:
        logger.warning(f"tasklist {image}: {e}")
        return None
    for line in result.stdout.strip().splitlines():
        parts = line.strip().strip('"').split('","')
        if len(parts) >= 2 and parts[0].lower() == image.lower():
            try:
                return int(parts[1])
            except ValueError:
                pass
    return None


def is_running(image: str) -> bool:
    return find_pid(image) is not None


def kill(image: str) -> None:
    """Принудительно завершить все процессы с таким именем exe. Если их
    нет — taskkill просто ничего не делает."""
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", image],
            capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=5,
        )
    except Exception as e:
        logger.warning(f"taskkill {image}: {e}")
