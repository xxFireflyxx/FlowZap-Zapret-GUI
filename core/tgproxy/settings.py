"""
core/tgproxy/settings.py
------------------------
Настройки TG WS Proxy — раздел [tgproxy] в config.toml — и всё, что из них
следует: параметры командной строки сервера, ссылка tg://proxy, отпечаток
«Telegram уже подключён с этими портом и секретом».

Перенос со старой схемы: exe автора хранил настройки в
%APPDATA%\\TgWsProxy\\config.json — порт и секрет оттуда берутся один раз,
чтобы прокси, уже добавленный в Telegram, продолжил работать.
"""

import copy
import hashlib
import json
import logging
import os
import re
import socket
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULTS = {
    "port": 1443,
    "lan": False,               # слушать все адреса (0.0.0.0) — для телефона в той же сети
    "dc_ip": ["2:149.154.167.220", "4:149.154.167.220"],
    "cfproxy": True,            # запасной путь через Cloudflare
    "cfproxy_domains": [],      # свои домены Cloudflare; пусто — автоматически
    "worker_domains": [],       # свои Cloudflare Worker
    "h2": True,                 # медиа через Cloudflare одним HTTP/2-соединением
    "no_secure": False,         # Cloudflare без TLS (порт 80)
    "pool_size": 4,
    "buf_kb": 256,
    "verbose": False,
    "launch_telegram": True,    # включили прокси, а Telegram не запущен — запустить
}

# Что «Вернуть по умолчанию» не трогает: от них зависит ссылка в Telegram,
# а запуск Telegram — в колонке «Подключение», не в «Дополнительно»
CONNECTION_KEYS = ("port", "secret", "lan", "launch_telegram")

LOCAL_HOST = "127.0.0.1"

_RE_DOMAIN = re.compile(
    r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$")


def new_secret() -> str:
    return os.urandom(16).hex()


def valid_secret(value) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-fA-F]{32}", value))


def valid_port(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 65535


def parse_dc_ip(text: str) -> list[str]:
    """«2:149.154.167.220, 4:…» → список. ValueError с текстом для пользователя."""
    result = []
    for entry in re.split(r"[\s,;]+", text.strip()):
        if not entry:
            continue
        dc, _, ip = entry.partition(":")
        try:
            int(dc)
            socket.inet_pton(socket.AF_INET, ip)
        except (ValueError, OSError):
            raise ValueError(f"«{entry}» — нужно номер:IP, например 4:149.154.167.220") from None
        result.append(entry)
    return result


def parse_domains(text: str) -> list[str]:
    """Домены через запятую или пробел → список без повторов. ValueError — неверный домен."""
    result: list[str] = []
    for item in re.split(r"[\s,;]+", text.strip()):
        item = item.strip().lower()
        if "://" in item:
            item = item.split("://", 1)[1]
        item = item.strip("/").split("/")[0]
        if not item:
            continue
        if not _RE_DOMAIN.match(item):
            raise ValueError(f"«{item}» — не похоже на домен")
        if item not in result:
            result.append(item)
    return result


def _domain_list(value) -> list[str]:
    """Список доменов из старого конфига: строка или список строк."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    try:
        return parse_domains(",".join(v for v in value if isinstance(v, str)))
    except ValueError:
        return []


def _read_legacy() -> dict:
    """Настройки из конфига exe автора (%APPDATA%\\TgWsProxy\\config.json) в нашем формате."""
    path = Path(os.environ.get("APPDATA", "")) / "TgWsProxy" / "config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or not valid_secret(data.get("secret")):
        return {}

    result: dict = {"secret": data["secret"].lower()}
    try:
        port = int(data.get("port"))
        if valid_port(port):
            result["port"] = port
    except (TypeError, ValueError):
        pass
    result["lan"] = data.get("host") == "0.0.0.0"
    dc = data.get("dc_ip")
    if isinstance(dc, str):
        dc = dc.splitlines()
    if isinstance(dc, list):
        try:
            result["dc_ip"] = parse_dc_ip(",".join(x for x in dc if isinstance(x, str)))
        except ValueError:
            pass
    for key in ("cfproxy", "h2", "no_secure", "verbose"):
        if isinstance(data.get(key), bool):
            result[key] = data[key]
    for key in ("pool_size", "buf_kb"):
        if isinstance(data.get(key), int) and not isinstance(data.get(key), bool):
            result[key] = data[key]
    # В exe свои домены включаются галочкой; без неё список хранится, но не действует
    user = _domain_list(data.get("cfproxy_user_domain"))
    if data.get("cfproxy_user_domain_enabled", bool(user)):
        result["cfproxy_domains"] = user
    worker = _domain_list(data.get("cfproxy_worker_domain"))
    if data.get("cfproxy_worker_enabled", bool(worker)):
        result["worker_domains"] = worker
    return result


def ensure_config(config: dict) -> bool:
    """Привести config["tgproxy"] к полному виду. True — появился новый
    секрет (свой или перенесённый): конфиг нужно сохранить, иначе при
    следующем запуске секрет был бы другим и Telegram потерял бы прокси."""
    tg = config.get("tgproxy")
    if not isinstance(tg, dict):
        tg = {}
    created = False
    if not valid_secret(tg.get("secret")):
        legacy = _read_legacy()
        if legacy:
            logger.info("TG Proxy: настройки перенесены из %APPDATA%\\TgWsProxy\\config.json")
            tg.update(legacy)
        else:
            tg["secret"] = new_secret()
        created = True
    for key, value in DEFAULTS.items():
        if type(tg.get(key)) is not type(value):
            tg[key] = copy.deepcopy(value)
    if not valid_port(tg["port"]):
        tg["port"] = DEFAULTS["port"]
    tg.setdefault("linked", "")
    tg.setdefault("client", "")
    tg.setdefault("client_path", "")    # где лежит exe клиента — запомнен, когда он был запущен
    config["tgproxy"] = tg
    return created


def reset_advanced(tg: dict) -> None:
    """«Вернуть по умолчанию»: всё из «Дополнительно» (CONNECTION_KEYS не трогает)."""
    for key, value in DEFAULTS.items():
        if key not in CONNECTION_KEYS:
            tg[key] = copy.deepcopy(value)


def listen_host(tg: dict) -> str:
    return "0.0.0.0" if tg.get("lan") else LOCAL_HOST


def lan_address() -> Optional[str]:
    """Адрес этого компьютера в локальной сети (без отправки пакетов: UDP
    connect только выбирает маршрут)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None


def tg_link(tg: dict, host: str = LOCAL_HOST) -> str:
    # dd — секрет со случайным дополнением пакетов: такую ссылку даёт сам автор
    return f"tg://proxy?server={host}&port={tg['port']}&secret=dd{tg['secret']}"


def fingerprint(tg: dict) -> str:
    """Отпечаток порта и секрета: Telegram подключали именно с ними. Сменились —
    старый прокси в Telegram не работает, нужно подключить заново."""
    return hashlib.sha256(f"{tg.get('port')}:{tg.get('secret')}".encode()).hexdigest()[:16]


def is_linked(tg: dict) -> bool:
    return bool(tg.get("linked")) and tg.get("linked") == fingerprint(tg)


# Без них сервер запустится не с тем портом или секретом — Telegram не подключится
REQUIRED_OPTIONS = ("--port", "--secret")


def build_args(tg: dict, supported: Optional[set[str]] = None) -> tuple[list[str], list[str]]:
    """(параметры командной строки сервера, пропущенные). supported — какие
    параметры знает установленная версия (см. host.probe): если автор уберёт
    или переименует параметр, сервер всё равно запустится, а пропущенное
    попадёт в лог."""
    want: list[tuple[str, Optional[str]]] = [
        ("--port", str(tg["port"])),
        ("--host", listen_host(tg)),
        ("--secret", tg["secret"]),
    ]
    if tg["dc_ip"]:
        want += [("--dc-ip", entry) for entry in tg["dc_ip"]]
    else:
        want.append(("--dc-ip", None))      # без значения — ни одного правила DC → IP
    want += [("--buf-kb", str(tg["buf_kb"])), ("--pool-size", str(tg["pool_size"]))]
    want += [("--cfproxy-domain", d) for d in tg["cfproxy_domains"]]
    want += [("--cfproxy-worker-domain", d) for d in tg["worker_domains"]]
    if not tg["cfproxy"]:
        want.append(("--no-cfproxy", None))
    if not tg["h2"]:
        want.append(("--no-h2", None))
    if tg["no_secure"]:
        want.append(("--no-secure", None))
    if tg["verbose"]:
        want.append(("--verbose", None))

    args: list[str] = []
    skipped: list[str] = []
    for option, value in want:
        if supported is not None and option not in supported:
            if option not in skipped:
                skipped.append(option)
            continue
        args.append(option if value is None else f"{option}={value}")
    return args, skipped
