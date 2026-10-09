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

from core.zapret.presets import GAME_FILTER_OFF, game_filter_ports, parse_bat
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


def add_game_lists(args: list[str], lists_dir: Path, game_ports: set[str]) -> list[str]:
    """Вставить игровые списки FlowZap в нужные профили пресета.

    Аргументы после последнего --new относятся только к последнему профилю,
    поэтому дописывать списки в конец нельзя: у Flowseal там игровой UDP, а
    профиль с --hostlist не срабатывает, когда имя хоста неизвестно (у
    игрового UDP его нет) — так игровой режим выключался целиком.
    - домены — к --hostlist=list-general-user.txt: там имя хоста известно
      (HTTP, TLS, QUIC);
    - IP — к --ipset игровых профилей (порты Game Filter): несколько --ipset
      в профиле объединяются. Профиль без --ipset и так берёт все IP —
      его не трогаем, иначе он бы сузился до игровых адресов.
    """
    domains, ipset = lists_dir / GAME_LIST_DOMAINS, lists_dir / GAME_LIST_IPSET
    dom_arg = f"--hostlist={domains}" if domains.exists() else None
    ip_arg = f"--ipset={ipset}" if ipset.exists() else None
    if not (dom_arg or ip_arg):
        logger.debug("Игровые списки ещё не загружены")
        return args

    profiles: list[list[str]] = [[]]
    for arg in args:
        if arg == "--new":
            profiles.append([])
        else:
            profiles[-1].append(arg)

    out: list[str] = []
    added_dom = added_ip = 0
    for i, profile in enumerate(profiles):
        ports = set()
        for arg in profile:
            if arg.startswith(("--filter-tcp=", "--filter-udp=")):
                ports.update(arg.split("=", 1)[1].split(","))
        is_game = bool(ports & game_ports)
        dom_done = ip_done = False
        if i:
            out.append("--new")
        for arg in profile:
            out.append(arg)
            key, _, val = arg.partition("=")
            if (dom_arg and not dom_done and key == "--hostlist"
                    and Path(val).name.lower() == "list-general-user.txt"):
                out.append(dom_arg)
                dom_done = True
                added_dom += 1
            elif ip_arg and not ip_done and is_game and key == "--ipset":
                out.append(ip_arg)
                ip_done = True
                added_ip += 1
    logger.info(f"Игровые списки: домены в {added_dom} проф., IP в {added_ip} проф.")
    return out


def build_winws_cmd(bat_path: Path, winws_exe: Path) -> Optional[tuple[list[str], Path]]:
    """Команда запуска winws.exe для пресета: (cmd, cwd) или None, если в
    .bat не нашлось аргументов winws. Пресет читается заново при каждом
    вызове — Game Filter мог поменяться. Пути из аргументов — к файлам в
    bin/ и lists/, в формате 8.3; при включённом Game Filter добавляются
    игровые списки FlowZap (см. add_game_lists)."""
    args = parse_bat(bat_path)
    if not args:
        return None
    zapret_dir = bat_path.parent
    bin_dir = zapret_dir / "bin"
    lists_dir = zapret_dir / "lists"
    ensure_user_lists(lists_dir)

    game_ports = set(game_filter_ports(zapret_dir)) - {GAME_FILTER_OFF}
    if game_ports:
        args = add_game_lists(args, lists_dir, game_ports)

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
