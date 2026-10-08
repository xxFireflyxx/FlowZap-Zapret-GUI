"""
core/zapret/game_lists.py
-------------------------
Игровые списки доменов и IP (medvedeff-true/ru-gaming-blocklist) для Game
Filter — скачиваются в zapret/lists не чаще раза в 6 часов.
"""
import logging
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from core.updates.releases import ssl_context
from core.zapret.winws import GAME_LIST_DOMAINS, GAME_LIST_IPSET

logger = logging.getLogger(__name__)

GAMING_LISTS_REPO_RAW = "https://raw.githubusercontent.com/medvedeff-true/ru-gaming-blocklist/main"
_GAMING_REMOTE_DOMAINS     = "medvedeff-game-list-all.txt"
_GAMING_REMOTE_IPSET       = "medvedeff-game-ipset.txt"
GAMING_UPDATE_INTERVAL = 6 * 3600  # 6 часов в секундах
_GAMING_STAMP_FILE     = "gaming_lists_updated.txt"


def update_gaming_lists(
    lists_dir: Path,
    force: bool = False,
    on_done: Optional[Callable[[bool, str], None]] = None,
) -> None:
    """
    Скачать игровые списки доменов и IP в фоновом потоке.
    Пропускает загрузку если с последнего обновления прошло менее 6 часов.
    force=True — игнорировать кэш.
    """
    def _worker() -> None:
        stamp_file = lists_dir / _GAMING_STAMP_FILE

        # Проверяем кэш
        if not force and stamp_file.exists():
            try:
                last = float(stamp_file.read_text(encoding="utf-8").strip())
                if time.time() - last < GAMING_UPDATE_INTERVAL:
                    logger.debug("Игровые списки актуальны, пропускаем загрузку")
                    if on_done:
                        on_done(True, "актуальны")
                    return
            except Exception:
                pass

        try:
            lists_dir.mkdir(parents=True, exist_ok=True)
            for remote, local in (
                (_GAMING_REMOTE_DOMAINS, GAME_LIST_DOMAINS),
                (_GAMING_REMOTE_IPSET,   GAME_LIST_IPSET),
            ):
                url = f"{GAMING_LISTS_REPO_RAW}/{remote}"
                logger.info(f"Обновление игрового списка: {remote} -> {local}")
                req = urllib.request.Request(url, headers={"User-Agent": "FlowZap/1.0"})
                with urllib.request.urlopen(req, timeout=30, context=ssl_context()) as r:
                    data = r.read()
                (lists_dir / local).write_bytes(data)
                logger.info(f"Сохранён: {local} ({len(data)} байт)")

            stamp_file.write_text(str(time.time()), encoding="utf-8")
            logger.info("Игровые списки обновлены")
            if on_done:
                on_done(True, "обновлены")

        except Exception as exc:
            logger.warning(f"Ошибка обновления игровых списков: {exc}")
            if on_done:
                on_done(False, str(exc))

    threading.Thread(target=_worker, daemon=True, name="gaming-lists-updater").start()
