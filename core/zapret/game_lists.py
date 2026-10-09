"""
core/zapret/game_lists.py
-------------------------
Игровые списки доменов и IP (medvedeff-true/ru-gaming-blocklist, Unlicense)
для Game Filter — скачиваются в zapret/lists не чаще раза в 6 часов. Как они
попадают в команду winws — winws.add_game_lists().
"""
import ipaddress
import logging
import os
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from core.updates.releases import ssl_context
from core.zapret.winws import GAME_LIST_DOMAINS, GAME_LIST_IPSET

logger = logging.getLogger(__name__)

# Основной источник и зеркало (jsDelivr отдаёт файлы того же репозитория,
# с задержкой кэша, — когда raw.githubusercontent.com недоступен)
GAMING_LISTS_SOURCES = (
    "https://raw.githubusercontent.com/medvedeff-true/ru-gaming-blocklist/main",
    "https://cdn.jsdelivr.net/gh/medvedeff-true/ru-gaming-blocklist@main",
)
_GAMING_REMOTE_DOMAINS     = "medvedeff-game-list-all.txt"
_GAMING_REMOTE_IPSET       = "medvedeff-game-ipset.txt"
GAMING_UPDATE_INTERVAL = 6 * 3600  # 6 часов в секундах
_GAMING_STAMP_FILE     = "gaming_lists_updated.txt"
_MIN_ENTRIES = 50  # меньше — значит пришло что-то не то

# В списке доменов автор держит и отдельные IP/подсети — пропускаем их тоже
_HOST_RE = re.compile(r"^[A-Za-z0-9*._:/-]+$")


def _entries(text: str) -> list[str]:
    return [s for s in (line.strip() for line in text.splitlines())
            if s and not s.startswith(("#", ";", "//"))]


def _check_list(data: bytes, is_ipset: bool) -> str:
    """Пустая строка, если список годится для winws, иначе — что не так.
    Битый файл (обрыв загрузки, страница-заглушка провайдера) мог не дать
    winws запуститься."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "не текст UTF-8"
    entries = _entries(text)
    if len(entries) < _MIN_ENTRIES:
        return f"слишком мало записей ({len(entries)})"
    for entry in entries:
        if is_ipset:
            try:
                ipaddress.ip_network(entry, strict=False)
            except ValueError:
                return f"не IP/подсеть: {entry[:60]!r}"
        elif not _HOST_RE.match(entry):
            return f"не домен: {entry[:60]!r}"
    return ""


def _download_list(remote: str, is_ipset: bool) -> bytes:
    errors = []
    for base in GAMING_LISTS_SOURCES:
        url = f"{base}/{remote}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "FlowZap/1.0"})
            with urllib.request.urlopen(req, timeout=30, context=ssl_context()) as r:
                data = r.read()
        except Exception as exc:
            errors.append(f"{url}: {exc}")
            continue
        problem = _check_list(data, is_ipset)
        if not problem:
            return data
        errors.append(f"{url}: {problem}")
    raise RuntimeError("; ".join(errors))


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
            # Сначала скачиваем и проверяем оба, потом подменяем: при ошибке
            # остаются прежние файлы, а не один новый и один старый
            fresh = []
            for remote, local, is_ipset in (
                (_GAMING_REMOTE_DOMAINS, GAME_LIST_DOMAINS, False),
                (_GAMING_REMOTE_IPSET,   GAME_LIST_IPSET,   True),
            ):
                logger.info(f"Обновление игрового списка: {remote} -> {local}")
                fresh.append((local, _download_list(remote, is_ipset)))
            for local, data in fresh:
                tmp = lists_dir / (local + ".tmp")
                tmp.write_bytes(data)
                os.replace(tmp, lists_dir / local)
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
