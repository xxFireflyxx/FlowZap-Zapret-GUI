"""
core/bat_parser.py
------------------
Читает .bat файлы zapret и извлекает аргументы для winws.exe.

Типичный .bat выглядит так:
    start "zapret: %~n0" /min "%BIN%winws.exe" --wf-tcp=80,443 ^
        --dpi-desync=fake ^
        ...

Парсер убирает @echo off, cd, start "...", /min, %BIN%, ^-продолжения строк
и возвращает чистый список аргументов для subprocess.
"""

import re
from pathlib import Path
from typing import Optional


# Регулярка: ищем строку с winws.exe — учитываем %BIN%, кавычки, /min, /max и т.п.
# Формат: start "..." [/min|/max|...] ["%BIN%winws.exe" | "...winws.exe" | winws.exe]
_WINWS_RE = re.compile(
    r'start\s+"[^"]*"\s+(?:/\w+\s+)*(?:"[^"]*winws(?:\.exe)?"|[^\s"]*winws(?:\.exe)?)\s+(.*)',
    re.IGNORECASE | re.DOTALL,
)

# Запасная регулярка: просто winws.exe где-то на строке (без start)
_WINWS_FALLBACK_RE = re.compile(
    r'winws(?:\.exe)?\s+(.*)',
    re.IGNORECASE | re.DOTALL,
)

# Убираем: %ПЕРЕМЕННАЯ%, ^ (продолжение строки BAT)
_VAR_RE = re.compile(r'%[^%]+%')
_CARET_RE = re.compile(r'\^')


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

    args_raw = match.group(1).strip()

    # Убираем переменные окружения вида %BIN%, %LISTS% и т.д.
    # GameFilter-переменные заменяем на плейсхолдер GAMEFILTER_TCP/UDP
    # чтобы manager мог подставить реальные значения
    args_raw = re.sub(r'%GameFilterTCP%', '__GAMEFILTER_TCP__', args_raw, flags=re.IGNORECASE)
    args_raw = re.sub(r'%GameFilterUDP%', '__GAMEFILTER_UDP__', args_raw, flags=re.IGNORECASE)
    # Убираем остальные переменные и лишние запятые
    args_raw = re.sub(r',%[^%]+%', '', args_raw)
    args_raw = re.sub(r'%[^%]+%,', '', args_raw)
    args_raw = _VAR_RE.sub('', args_raw)

    # Убираем ^
    args_raw = _CARET_RE.sub('', args_raw)

    # Убираем "pause", "exit", echo и подобные хвосты
    args_raw = re.sub(r'\b(pause|exit|echo\.?)\b.*', '', args_raw, flags=re.IGNORECASE)

    # Убираем одиночные и двойные кавычки
    args_raw = args_raw.replace('"', '').replace("'", '')

    # Разбиваем на токены
    args = args_raw.split()

    # Оставляем только аргументы вида --xxx и --xxx=yyy
    args = [a for a in args if a.startswith('--')]

    return args if args else None


def get_bat_description(bat_path: Path) -> str:
    """
    Вернуть короткое человекочитаемое описание пресета
    на основе ключевых аргументов winws.
    """
    args = parse_bat(bat_path) or []
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
            'desc': get_bat_description(bat) if args else '(аргументы не распарсены)',
        })

    return presets
