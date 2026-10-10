"""
core/announcements.py
---------------------
Объявления от автора: плашка на главной. Нужны, когда пользователям надо
что-то сказать, а обновление до них не дойдёт или само сломано (пример —
0.5.2: обновиться можно было только вручную, а сказать об этом было негде).

Файл announcements.toml в корне репозитория + announcements.toml.sig
(подпись автора, release/signing.py sign-file). FlowZap скачивает их при
запуске: GitHub, при неудаче GitLab. Без верной подписи файл игнорируется
целиком — иначе чужой доступ к репозиторию позволял бы показывать любые
«объявления» со ссылками от имени FlowZap.

    [[announcement]]
    id = "2026-10-manual-update"     # постоянный; закрытое пользователем не вернётся
    title = "Обновите FlowZap вручную"
    text = "В версии 1.0.0 сломано обновление кнопкой. Скачайте 1.0.1 со страницы проекта."
    tone = "warning"                 # info (по умолчанию) или warning
    link = "https://github.com/xxFireflyxx/FlowZap-Zapret-GUI/releases"
    link_text = "Открыть страницу"   # по умолчанию «Подробнее»
    min_version = "1.0.0"            # показывать с этой версии (включительно)
    max_version = "1.0.0"            # и до этой (включительно)
    until = "2026-12-31"             # не показывать после этой даты
"""

import datetime
import logging
import re
import threading
import tomllib
import urllib.request
from typing import Callable, Optional

from core.updates import signing

logger = logging.getLogger(__name__)

FILE_NAME = "announcements.toml"
_TONES = ("info", "warning")
_MAX_ITEMS = 5          # больше плашек на главной — уже не объявления


def _version(text: str) -> tuple[int, ...]:
    m = re.match(r"\s*[vV]?(\d+(?:\.\d+)*)", text or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def parse(data: bytes) -> list[dict]:
    """Объявления из TOML. Битые записи пропускаются (с предупреждением в
    логе), чтобы ошибка в одной не прятала остальные."""
    raw = tomllib.loads(data.decode("utf-8"))
    result, seen = [], set()
    for item in raw.get("announcement", []):
        if not isinstance(item, dict):
            continue
        aid = str(item.get("id", "")).strip()
        text = str(item.get("text", "")).strip()
        if not aid or not text or aid in seen:
            logger.warning(f"{FILE_NAME}: объявление без id/текста или повтор id: {item!r}")
            continue
        link = str(item.get("link", "")).strip()
        if link and not link.startswith("https://"):
            logger.warning(f"{FILE_NAME}: «{aid}» — ссылка не https, убираю: {link!r}")
            link = ""
        until = item.get("until")
        if isinstance(until, str):
            try:
                until = datetime.date.fromisoformat(until)
            except ValueError:
                logger.warning(f"{FILE_NAME}: «{aid}» — непонятная дата until={until!r}")
                until = None
        if isinstance(until, datetime.datetime):
            until = until.date()
        seen.add(aid)
        result.append({
            "id": aid,
            "title": str(item.get("title", "")).strip(),
            "text": text,
            "tone": item.get("tone") if item.get("tone") in _TONES else "info",
            "link": link,
            "link_text": str(item.get("link_text", "")).strip() or "Подробнее",
            "min_version": str(item.get("min_version", "")).strip(),
            "max_version": str(item.get("max_version", "")).strip(),
            "until": until if isinstance(until, datetime.date) else None,
        })
    return result


def relevant(items: list[dict], gui_version: str, dismissed,
             today: Optional[datetime.date] = None) -> list[dict]:
    """Что показать этой версии: не закрытое, не просроченное, версия в диапазоне."""
    today = today or datetime.date.today()
    current = _version(gui_version)
    shown = []
    for item in items:
        if item["id"] in dismissed:
            continue
        if item["until"] and today > item["until"]:
            continue
        if item["min_version"] and current < _version(item["min_version"]):
            continue
        if item["max_version"] and current > _version(item["max_version"]):
            continue
        shown.append(item)
    return shown[:_MAX_ITEMS]


def _urls(path: str) -> tuple[str, ...]:
    from core.updates.releases import FLOWZAP_REPO
    return (f"https://raw.githubusercontent.com/{FLOWZAP_REPO}/main/{path}",
            f"https://gitlab.com/xx_firefly_xx/flowzap/-/raw/main/{path}")


def _get(url: str) -> bytes:
    from core.updates.releases import ssl_context
    req = urllib.request.Request(url, headers={"User-Agent": "FlowZap/1.0"})
    with urllib.request.urlopen(req, timeout=10, context=ssl_context()) as r:
        return r.read(256 * 1024)


def fetch() -> Optional[list[dict]]:
    """Скачать и проверить объявления. None — не удалось (нет файла, сеть,
    подпись не сошлась); [] — файл есть, объявлений нет."""
    for data_url, sig_url in zip(_urls(FILE_NAME), _urls(FILE_NAME + signing.SIGNATURE_SUFFIX)):
        try:
            data, sig = _get(data_url), _get(sig_url)
        except Exception as exc:
            logger.debug(f"Объявления: {data_url} — {exc}")
            continue
        try:
            signing.verify_file(data, FILE_NAME, sig)
        except signing.SignatureError as exc:
            # Подменённый файл — повод насторожиться, а не просто сбой сети
            logger.warning(f"Объявления: подпись не прошла проверку ({exc}) — {data_url}")
            continue
        try:
            items = parse(data)
        except Exception as exc:
            logger.warning(f"Объявления: файл не разобран — {exc}")
            continue
        logger.info(f"Объявления: {len(items)} шт. ({data_url.split('/')[2]})")
        return items
    return None


def fetch_async(on_done: Callable[[Optional[list[dict]]], None]) -> None:
    """fetch() в фоновом потоке; on_done зовётся из него же."""
    threading.Thread(target=lambda: on_done(fetch()), daemon=True, name="announcements").start()
