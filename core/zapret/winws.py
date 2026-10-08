"""
core/zapret/winws.py
--------------------
Всё о запуске winws.exe, общее для настоящего запуска zapret (manager.py),
проверки пресетов (preset_checker.py) и установки Core (core/updates/zapret.py):
команда из .bat-пресета, пустые пользовательские списки, прогрев WinDivert.

Проверка пресетов запускает winws той же командой, что и «Запустить», —
раньше у неё не было игровых списков, а порты Game Filter оставались теми,
что были при загрузке списка пресетов.
"""

import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

from core.zapret.presets import parse_bat
from core.system.winproc import CREATE_NO_WINDOW

logger = logging.getLogger(__name__)

WINWS_EXE = "winws.exe"

# Файлы, которые пресеты Flowseal указывают в аргументах. winws.exe падает,
# если файл указан, но его нет, — создаём пустые.
USER_LIST_FILES = (
    "list-general-user.txt",
    "list-exclude-user.txt",
    "list-exclude.txt",
    "ipset-exclude-user.txt",
    "ipset-exclude.txt",
)
GAME_LIST_DOMAINS = "flowzap-game-list-all.txt"
GAME_LIST_IPSET   = "flowzap-game-ipset.txt"


def short_path(path) -> str:
    """Путь в формате 8.3 — без кириллицы и пробелов (если 8.3-имена на
    диске включены; иначе возвращается как есть)."""
    try:
        import ctypes
        p = str(path)
        buf = ctypes.create_unicode_buffer(512)
        ctypes.windll.kernel32.GetShortPathNameW(p, buf, 512)
        return buf.value if buf.value else p
    except Exception:
        return str(path)


def ensure_user_lists(lists_dir: Path) -> None:
    lists_dir.mkdir(parents=True, exist_ok=True)
    for name in USER_LIST_FILES:
        path = lists_dir / name
        if not path.exists():
            try:
                path.touch()
                logger.info(f"Создан пустой файл: {name}")
            except Exception as e:
                logger.warning(f"Не удалось создать {name}: {e}")


def build_winws_cmd(bat_path: Path, winws_exe: Path) -> Optional[tuple[list[str], Path]]:
    """Команда запуска winws.exe для пресета: (cmd, cwd) или None, если в
    .bat не нашлось аргументов winws. Пресет читается заново при каждом
    вызове — Game Filter мог поменяться. Пути из аргументов — к файлам в
    bin/ и lists/, в формате 8.3; при включённом Game Filter добавляются
    игровые списки FlowZap."""
    args = parse_bat(bat_path)
    if not args:
        return None
    zapret_dir = bat_path.parent
    bin_dir = zapret_dir / "bin"
    lists_dir = zapret_dir / "lists"
    ensure_user_lists(lists_dir)

    resolved = []
    for arg in args:
        if "=" not in arg:
            resolved.append(arg)
            continue
        key, val = arg.split("=", 1)
        val = val.strip('"').strip("'")
        for search_dir in (bin_dir, lists_dir):
            candidate = search_dir / val
            if candidate.exists():
                val = short_path(candidate)
                break
            candidate = search_dir / Path(val).name
            if candidate.exists():
                val = short_path(candidate)
                break
        resolved.append(f"{key}={val}")

    if (zapret_dir / "utils" / "game_filter.enabled").exists():
        for name, key in ((GAME_LIST_DOMAINS, "--hostlist"), (GAME_LIST_IPSET, "--ipset")):
            path = lists_dir / name
            if path.exists():
                resolved.append(f"{key}={short_path(path)}")
            else:
                logger.debug(f"Game список не найден (будет загружен): {name}")

    return [short_path(winws_exe)] + resolved, bin_dir


def warmup_windivert(winws_exe: Path) -> None:
    """Короткий запуск winws.exe (~3 с): после перезагрузки или установки
    Core драйвер WinDivert грузится в ядро при первом запуске, и без прогрева
    первый тест пресетов мог дать ложный FAIL. Синхронный."""
    if not winws_exe.exists():
        return
    logger.info("Прогрев WinDivert...")
    try:
        proc = subprocess.Popen(
            [str(winws_exe), "--wf-tcp=80"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
        time.sleep(3)
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
        logger.info("WinDivert прогрет")
    except Exception as e:
        logger.debug(f"Прогрев WinDivert: {e}")
