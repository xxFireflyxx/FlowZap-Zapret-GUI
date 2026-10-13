"""
core/zapret/presets.py
----------------------
Читает .bat файлы zapret и извлекает аргументы для winws.exe.

Типичный .bat выглядит так:
    start "zapret: %~n0" /min "%BIN%winws.exe" --wf-tcp=80,443 ^
        --dpi-desync=fake ^
        ...

Парсер убирает @echo off, cd, start "...", /min, %BIN%, ^-продолжения строк
и возвращает чистый список аргументов для subprocess.
"""

import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Регулярка: ищем строку с winws.exe — учитываем %BIN%, кавычки, /min, /max и т.п.
# Формат: start "..." [/min|/max|...] ["%BIN%winws.exe" | "...winws.exe" | winws.exe]
_WINWS_RE = re.compile(
    r'start\s+"[^"]*"\s+(?:/\w+\s+)*(?:"[^"]*winws(?:\.exe)?"|[^\s"]*winws(?:\.exe)?)\s+(.*)',
    re.IGNORECASE,
)

# Запасная регулярка: просто winws.exe где-то на строке (без start)
_WINWS_FALLBACK_RE = re.compile(
    r'winws(?:\.exe)?\s+(.*)',
    re.IGNORECASE,
)

# ^ (продолжение строки и экранирование в BAT)
_CARET_RE = re.compile(r'\^')


GAME_FILTER_OFF = "12"  # «пустой» порт, которым service.bat выключает Game Filter


GAME_FILTER_MODES = ("all", "tcp", "udp")
_GAME_RANGE_DEFAULT = "1024-65535"
_PORT_ITEM_RE = re.compile(r"([1-9]\d{0,4})(?:-([1-9]\d{0,4}))?")


def _game_filter_file(bat_dir: Path) -> Path:
    return bat_dir / "utils" / "game_filter.enabled"


def _valid_port_ranges(value: str) -> bool:
    for item in value.split(","):
        m = _PORT_ITEM_RE.fullmatch(item)
        if not m or int(m.group(1)) > 65535 or int(m.group(2) or 0) > 65535:
            return False
    return True


def read_game_filter(bat_dir: Path) -> tuple[str, str, str]:
    """(режим, TCP-порты, UDP-порты) из utils/game_filter.enabled так же, как
    читает его service.bat Flowseal: старый формат — одно слово all/tcp/udp,
    с 1.10.3 — строки mode=…, tcp=…, udp=… (свои диапазоны портов). Режим —
    all/tcp/udp или disabled (нет файла, mode=disabled, непонятное)."""
    mode, tcp, udp = "disabled", _GAME_RANGE_DEFAULT, _GAME_RANGE_DEFAULT
    try:
        text = _game_filter_file(bat_dir).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return mode, tcp, udp
    for line in text.splitlines():
        key, _, value = line.strip().partition("=")
        key, value = key.strip().lower(), value.strip().replace(" ", "")
        if key == "mode":
            mode = value.lower()
        elif key == "all" or (key in ("tcp", "udp") and not value):
            mode = key
        elif key == "tcp" and _valid_port_ranges(value):
            tcp = value
        elif key == "udp" and _valid_port_ranges(value):
            udp = value
    return (mode if mode in GAME_FILTER_MODES else "disabled"), tcp, udp


def write_game_filter(bat_dir: Path, mode: str) -> None:
    """Записать режим (all/tcp/udp/disabled) в формате Flowseal 1.10.3,
    сохранив диапазоны портов из файла."""
    _, tcp, udp = read_game_filter(bat_dir)
    path = _game_filter_file(bat_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"mode={mode}\ntcp={tcp}\nudp={udp}\n", encoding="utf-8")


def game_filter_ports(bat_dir: Path) -> tuple[str, str]:
    """(GameFilterTCP, GameFilterUDP) для подстановки в пресет — как в
    service.bat: выключенный протокол получает «пустой» порт 12."""
    mode, tcp, udp = read_game_filter(bat_dir)
    return (tcp if mode in ("all", "tcp") else GAME_FILTER_OFF,
            udp if mode in ("all", "udp") else GAME_FILTER_OFF)


def _cut_command_tail(line: str) -> str:
    """Строка .bat до первого & или | вне кавычек: дальше идёт уже другая
    команда («& pause», «& exit»). Не по словам: «Exit Games» в пути или
    echo.msk.ru в списке доменов — часть аргументов."""
    quoted = escaped = False
    for i, c in enumerate(line):
        if escaped:
            escaped = False
        elif c == '^':
            escaped = True
        elif c == '"':
            quoted = not quoted
        elif c in '&|' and not quoted:
            return line[:i]
    return line


def parse_bat(bat_path: Path) -> Optional[list[str]]:
    """
    Распарсить .bat файл и вернуть список аргументов для winws.exe.
    Возвращает None если winws.exe не найден в файле.
    """
    try:
        text = bat_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    # Объединяем строки с продолжением (^) в одну
    text = re.sub(r'\^\s*\r?\n\s*', ' ', text)

    # Ищем строку с winws — сначала основная регулярка
    match = _WINWS_RE.search(text)
    if not match:
        # Запасной вариант
        match = _WINWS_FALLBACK_RE.search(text)
    if not match:
        return None

    args_raw = _cut_command_tail(match.group(1)).strip()

    # Папки рядом с .bat файлом
    bat_dir   = bat_path.parent
    bin_dir   = bat_dir / "bin"
    lists_dir = bat_dir / "lists"

    # Подставляем реальные пути вместо %BIN% и %LISTS%
    # Используем lambda чтобы избежать интерпретации \ как regex escape
    bin_str   = str(bin_dir) + "\\"
    lists_str = str(lists_dir) + "\\"
    args_raw = re.sub(r'%BIN%',   lambda m: bin_str,   args_raw, flags=re.IGNORECASE)
    args_raw = re.sub(r'%LISTS%', lambda m: lists_str, args_raw, flags=re.IGNORECASE)

    # Читаем игровой фильтр и подставляем реальные значения
    game_tcp, game_udp = game_filter_ports(bat_dir)
    game_any = game_tcp if game_tcp != GAME_FILTER_OFF else game_udp    # как %GameFilter% в service.bat
    args_raw = re.sub(r'%GameFilterTCP%', lambda m: game_tcp, args_raw, flags=re.IGNORECASE)
    args_raw = re.sub(r'%GameFilterUDP%', lambda m: game_udp, args_raw, flags=re.IGNORECASE)
    args_raw = re.sub(r'%GameFilter%',    lambda m: game_any, args_raw, flags=re.IGNORECASE)

    # Убираем оставшиеся переменные окружения вида %VAR%
    # Порядок важен: сначала ,VAR потом VAR, потом одиночные
    args_raw = re.sub(r',%[^%]+%', '', args_raw)
    args_raw = re.sub(r'%[^%]+%,', '', args_raw)
    args_raw = re.sub(r'%[^%]+%', '', args_raw)

    # Убираем ^
    args_raw = _CARET_RE.sub('', args_raw)

    # Парсим аргументы вручную — shlex некорректно работает
    # с кириллическими путями в кавычках на Windows
    cleaned = []
    i = 0
    s = args_raw.strip()
    while i < len(s):
        # Пропускаем пробелы
        while i < len(s) and s[i] in (' ', '\t'):
            i += 1
        if i >= len(s):
            break

        # Читаем токен
        token = []
        in_quotes = False
        quote_char = None

        while i < len(s):
            c = s[i]
            if not in_quotes and c in ('"', "'"):
                in_quotes = True
                quote_char = c
                i += 1
            elif in_quotes and c == quote_char:
                in_quotes = False
                quote_char = None
                i += 1
            elif not in_quotes and c in (' ', '\t'):
                break
            else:
                token.append(c)
                i += 1

        tok = ''.join(token).strip()
        if not tok:
            continue

        if tok.startswith('--'):
            cleaned.append(tok)
        elif cleaned:
            # Первый не-аргумент после набора аргументов — стоп
            break

    logger.debug(f"parse_bat {bat_path.name}: {len(cleaned)} args")
    return cleaned if cleaned else None


def get_bat_description(args: list[str], bat_path: Path) -> str:
    """
    Вернуть короткое человекочитаемое описание пресета
    на основе уже распарсенных аргументов winws (см. parse_bat).
    """
    tags = []
    for arg in args:
        if '--dpi-desync=' in arg:
            val = arg.split('=', 1)[1]
            tags.append(f"desync:{val[:20]}")
        if '--wf-udp' in arg and 'QUIC' not in tags:
            tags.append("QUIC")
    return ', '.join(tags) if tags else bat_path.stem


def _natural_sort_key(name: str) -> list:
    """
    Ключ натуральной сортировки: general (ALT) < general (ALT2) < general (ALT10).
    Убираем скобки, чередуем текст и числа.
    """
    import re
    clean = re.sub(r'[()]', '', name).strip()
    parts = re.split(r'(\d+)', clean.lower())
    key = []
    for p in parts:
        if p.isdigit():
            key.append(int(p))
        else:
            key.append(p.strip())
    return key


def list_presets(presets_dir: Path) -> list[dict]:
    """
    Найти все .bat файлы в директории и вернуть список пресетов.

    Returns
    -------
    list of dict:
        [{'name': 'general', 'path': Path(...), 'args': [...], 'desc': '...'}, ...]
    """
    if not presets_dir.exists():
        return []

    presets = []
    for bat in sorted(presets_dir.glob("*.bat"), key=lambda b: _natural_sort_key(b.stem)):
        # Пропускаем служебные скрипты
        if bat.stem.lower() in ('service', 'install', 'uninstall', 'setup'):
            continue
        args = parse_bat(bat)
        # Включаем пресет даже если аргументы не распарсились — показываем имя файла
        presets.append({
            'name': bat.stem,
            'path': bat,
            'args': args or [],
            'desc': get_bat_description(args, bat) if args else '(аргументы не распарсены)',
        })

    return presets
