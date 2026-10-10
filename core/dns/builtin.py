"""
core/dns/builtin.py
-------------------
Встроенные DNS-серверы: прописаны в FlowZap, пользователь не может их удалить
или изменить. Список меняется только обновлениями — новой версией FlowZap
(BUILTIN_DNS ниже) или файлом core/dns/builtin-dns.toml в репозитории (скачивается
при запуске и принимается только с подписью автора — builtin-dns.toml.sig,
release/dns_list.py). Обновление может добавить пару, поменять её имя/адреса или
удалить её совсем; свои пары пользователя не трогаются.

В config.toml встроенная пара отмечена ключом builtin = "<id>" — по id её и
находим (имя может смениться). У списка есть номер (version): применяем
только список не старее уже применённого (dns.builtin_version) — иначе пара,
удалённая файлом из репозитория, возвращалась бы из BUILTIN_DNS при каждом
запуске.

Активная пара — всегда pairs[0] (см. core/dns/manager.py). Если обновление
удалило активную, активной становится следующая.
"""

import ipaddress
import logging
import threading
import tomllib
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("flowzap.dns.builtin")

_REPO_FILE = "core/dns/builtin-dns.toml"

# Список этой версии FlowZap. Меняя его, поднимайте BUILTIN_DNS_VERSION
# (и version в builtin-dns.toml рядом — не меньше этого номера).
# Порядок важен только для нового пользователя: первая пара станет активной.
# У остальных порядок их списка сохраняется — выбранный сервер не сменится.
BUILTIN_DNS_VERSION = 3
BUILTIN_DNS = [
    {"id": "by", "name": "Беларусский",
     "ipv4_main": "143.20.64.55", "ipv4_backup": "91.108.243.78",
     "ipv6_main": "", "ipv6_backup": ""},
    {"id": "reserve", "name": "Резерв",
     "ipv4_main": "159.94.200.33", "ipv4_backup": "193.233.112.67",
     "ipv6_main": "", "ipv6_backup": ""},
    {"id": "xbox", "name": "xbox-dns (временно недоступен)",
     "ipv4_main": "111.88.96.54", "ipv4_backup": "111.88.96.55",
     "ipv6_main": "2a00:ab00:1233:26::50", "ipv6_backup": "2a00:ab00:1233:26::51"},
]

_ADDR_KEYS = {"ipv4_main": 4, "ipv4_backup": 4, "ipv6_main": 6, "ipv6_backup": 6}
# До 1.0 встроенным был только xbox-dns, и отмечался он именем
_LEGACY_NAMES = {"xbox-dns": "xbox"}


def is_builtin_dns(pair: dict) -> bool:
    return isinstance(pair, dict) and bool(pair.get("builtin"))


def builtin_names(config: dict) -> set[str]:
    """Имена встроенных пар (в нижнем регистре) — заняты, своей паре их не дать."""
    names = {p["name"].lower() for p in BUILTIN_DNS}
    for pair in config.get("dns", {}).get("pairs", []):
        if is_builtin_dns(pair) and pair.get("name"):
            names.add(str(pair["name"]).lower())
    return names


def _config_pair(entry: dict) -> dict:
    pair = {"builtin": entry["id"], "name": entry["name"]}
    for key in _ADDR_KEYS:
        pair[key] = entry.get(key, "")
    # legacy-ключи, которые читаются как fallback (см. ParametersTab._save_dns)
    pair["main"], pair["backup"] = pair["ipv4_main"], pair["ipv4_backup"]
    return pair


def apply_builtin_dns(config: dict, entries: list[dict] | None = None,
                      version: int | None = None) -> bool:
    """Привести встроенные пары в config к списку entries (по умолчанию —
    BUILTIN_DNS этой версии). Список старее уже применённого не трогает
    (пропавшие пары вернёт следующая загрузка списка из репозитория).
    Возвращает True, если config изменился. Вызывать из GUI-потока или до
    создания окна — config читают виджеты."""
    if entries is None:
        entries, version = BUILTIN_DNS, BUILTIN_DNS_VERSION
    dns = config.setdefault("dns", {})
    pairs = dns.setdefault("pairs", [])
    before = [dict(p) if isinstance(p, dict) else p for p in pairs]
    applied = dns.get("builtin_version", 0)

    # Старые конфиги: xbox-dns без отметки
    for pair in pairs:
        if isinstance(pair, dict) and not pair.get("builtin") and pair.get("name") in _LEGACY_NAMES:
            pair["builtin"] = _LEGACY_NAMES[pair["name"]]

    if version < applied:
        # Список старее уже применённого (BUILTIN_DNS после обновления из
        # репозитория) — он устарел, встроенные пары не трогаем
        return [p for p in pairs if isinstance(p, dict)] != before
    dns["builtin_version"] = version
    ids_to_keep = {e["id"] for e in entries}

    wanted = {e["id"]: e for e in entries}
    by_ipv4 = {e["ipv4_main"]: e["id"] for e in entries if e.get("ipv4_main")}
    result, placed = [], set()
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        bid = pair.get("builtin")
        if not bid and pair.get("ipv4_main") in by_ipv4:
            # Своя пара с адресом встроенной — становится встроенной (на том же месте)
            bid = by_ipv4[pair["ipv4_main"]]
        if not bid:
            result.append(pair)
            continue
        if bid in placed or bid not in ids_to_keep:
            continue          # копия или пара, удалённая обновлением
        result.append(_config_pair(wanted[bid]))
        placed.add(bid)
    for entry in entries:
        if entry["id"] not in placed:
            result.append(_config_pair(entry))
            placed.add(entry["id"])

    changed = result != before
    if changed:
        old = {p.get("builtin"): p for p in before if is_builtin_dns(p)}
        new = {p["builtin"]: p for p in result if is_builtin_dns(p)}
        for bid in new.keys() - old.keys():
            logger.info(f"Встроенный DNS добавлен: {new[bid]['name']}")
        for bid in old.keys() - new.keys():
            logger.info(f"Встроенный DNS удалён: {old[bid].get('name')}")
        for bid in new.keys() & old.keys():
            if new[bid] != old[bid]:
                logger.info(f"Встроенный DNS обновлён: {new[bid]['name']}")
    pairs[:] = result
    return changed


def _parse(data: bytes) -> tuple[int, list[dict]] | None:
    """builtin-dns.toml: version = N и таблицы [[dns]] (id, name, адреса).
    Пары с битым id/именем или без единого корректного IP пропускаются; если
    не осталось ни одной — файл считаем битым (None), чтобы ошибка в
    репозитории не стёрла все встроенные пары."""
    raw = tomllib.loads(data.decode("utf-8"))
    version = raw.get("version")
    if not isinstance(version, int):
        logger.warning("builtin-dns.toml: нет номера version")
        return None
    entries, seen = [], set()
    for item in raw.get("dns", []):
        if not isinstance(item, dict):
            continue
        bid, name = str(item.get("id", "")).strip(), str(item.get("name", "")).strip()
        if not bid or not name or bid in seen:
            logger.warning(f"builtin-dns.toml: пара без id/имени или повтор id: {item!r}")
            continue
        entry = {"id": bid, "name": name}
        for key, ver in _ADDR_KEYS.items():
            value = str(item.get(key, "")).strip()
            try:
                entry[key] = value if value and ipaddress.ip_address(value).version == ver else ""
            except ValueError:
                logger.warning(f"builtin-dns.toml: некорректный {key} = {value!r} у «{name}»")
                entry[key] = ""
        if not entry["ipv4_main"] and not entry["ipv6_main"]:
            logger.warning(f"builtin-dns.toml: у «{name}» нет основного адреса — пропускаем")
            continue
        seen.add(bid)
        entries.append(entry)
    return (version, entries) if entries else None


def fetch_builtin_dns() -> tuple[int, list[dict]] | None:
    """Скачать список встроенных DNS: GitHub, при неудаче — GitLab. Только с
    подписью автора (builtin-dns.toml.sig, core/updates/signing.py): эти
    адреса FlowZap сам ставит в систему, и подменённый список (чужой доступ
    к репозиторию) тихо увёл бы DNS всех пользователей. Нет подписи или не
    сошлась — список не берём, остаётся встроенный в эту версию."""
    import urllib.request
    from core.updates import signing
    from core.updates.releases import FLOWZAP_REPO, ssl_context
    name = Path(_REPO_FILE).name
    bases = (f"https://raw.githubusercontent.com/{FLOWZAP_REPO}/main/{_REPO_FILE}",
             f"https://gitlab.com/xx_firefly_xx/flowzap/-/raw/main/{_REPO_FILE}")

    def get(url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "FlowZap/1.0"})
        with urllib.request.urlopen(req, timeout=10, context=ssl_context()) as r:
            return r.read(256 * 1024)

    for url in bases:
        try:
            data, sig = get(url), get(url + signing.SIGNATURE_SUFFIX)
        except Exception as exc:
            logger.warning(f"Не удалось получить список встроенных DNS ({url}): {exc}")
            continue
        try:
            signing.verify_file(data, name, sig)
        except signing.SignatureError as exc:
            logger.warning(f"Список встроенных DNS: подпись не прошла проверку ({exc}) — {url}")
            continue
        try:
            parsed = _parse(data)
        except Exception as exc:
            logger.warning(f"Список встроенных DNS не разобран ({url}): {exc}")
            continue
        if parsed:
            return parsed
    return None


def fetch_builtin_dns_async(on_done: Callable[[Optional[tuple[int, list[dict]]]], None]) -> None:
    """fetch_builtin_dns() в фоновом потоке; on_done(result) зовётся из него же."""
    threading.Thread(target=lambda: on_done(fetch_builtin_dns()),
                     daemon=True, name="builtin-dns-sync").start()
