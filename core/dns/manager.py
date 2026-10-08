"""
core/dns/manager.py
-------------------
Логика применения DNS + определение активной пары из конфига.
Чистая логика без UI — используется и dashboard.py (кнопка DNS), и
parameters.py (сразу после выбора новой активной пары).

Контракт активной пары: она всегда config["dns"]["pairs"][0]. Выбор другой
пары — это перестановка списка, а не отдельное поле вроде "active_pair".

DNS ставится через PowerShell (модуль DnsClient), одним процессом на всю
операцию. Раньше были netsh-вызовы с разбором текста «Подключён / Connected»:
на других языках Windows интерфейсы не находились, DNS ставился и на
виртуальные адаптеры (VPN, Hyper-V, VirtualBox), а ошибки netsh не
проверялись — UI показывал «включено», даже если ничего не применилось.

Кто меняет DNS (как и winws, см. core/zapret/runner.py):
  * установлена фоновая служба FlowZap → она (FlowZap без прав
    администратора); если FlowZap закроется или упадёт, служба сама сбросит DNS;
  * службы нет, FlowZap от администратора → напрямую этим же скриптом;
  * иначе — по действию пользователя предлагается установить службу.

Ни один из синхронных вызовов не создаёт свой фоновый поток — для UI есть
apply_dns_async(), как и у остальных core-модулей.
"""

import ipaddress
import logging
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

from core.zapret import runner
from core.service import client as service_client
from core.service.client import ServiceError

logger = logging.getLogger(__name__)

_PS_TIMEOUT = 30


def get_active_pair(config: dict) -> dict | None:
    """Активная DNS-пара — всегда первая в config['dns']['pairs'].
    Возвращает нормализованный dict с гарантированными ключами
    ipv4_main/ipv4_backup/ipv6_main/ipv6_backup/name, или None, если пар
    нет или у первой пары нет ни одного IP-адреса."""
    pairs = config.get("dns", {}).get("pairs", [])
    if not pairs or not isinstance(pairs[0], dict):
        return None
    pair = pairs[0]
    ipv4_main = pair.get("ipv4_main", pair.get("main", ""))
    ipv6_main = pair.get("ipv6_main", "")
    if not ipv4_main and not ipv6_main:
        return None
    return {
        "name":        pair.get("name", ipv4_main or ipv6_main),
        "ipv4_main":   ipv4_main,
        "ipv4_backup": pair.get("ipv4_backup", pair.get("backup", "")),
        "ipv6_main":   ipv6_main,
        "ipv6_backup": pair.get("ipv6_backup", ""),
    }


def _pair_addresses(pair: dict) -> list[str]:
    """IP-адреса пары по порядку: основной/запасной IPv4, затем IPv6.
    Некорректные (например, имя хоста вместо IP) пропускаются — Windows
    принимает в качестве DNS только IP-адреса."""
    result = []
    for key, version in (("ipv4_main", 4), ("ipv4_backup", 4), ("ipv6_main", 6), ("ipv6_backup", 6)):
        value = (pair.get(key) or "").strip()
        if not value:
            continue
        try:
            if ipaddress.ip_address(value).version == version:
                result.append(value)
                continue
        except ValueError:
            pass
        logger.warning(f"DNS: {key} = {value!r} — не IPv{version}-адрес, пропускаю")
    return result


# Адаптеры: при включении — те, через которые идёт интернет (есть маршрут по
# умолчанию: Wi-Fi, Ethernet, vEthernet внешнего коммутатора Hyper-V, VPN с
# шлюзом); если таких нет — все включённые. При выключении — все включённые,
# как и раньше: сбросить надо и там, где адрес мог остаться с прошлого раза.
# Status и маршруты не переводятся — работает на любом языке Windows.
_PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
try {
    $up = @(Get-NetAdapter | Where-Object { $_.Status -eq 'Up' })
    if ($up.Count -eq 0) { Write-Output 'NOADAPTER'; exit 0 }
    $targets = $up
    if (__ENABLE__) {
        $gw = @(Get-NetRoute -DestinationPrefix '0.0.0.0/0', '::/0' -ErrorAction SilentlyContinue |
                Select-Object -ExpandProperty ifIndex -Unique)
        $withGw = @($up | Where-Object { $gw -contains $_.ifIndex })
        if ($withGw.Count -gt 0) { $targets = $withGw }
    }
    foreach ($a in $targets) {
        try {
            if (__ENABLE__) {
                Set-DnsClientServerAddress -InterfaceIndex $a.ifIndex -ServerAddresses @(__ADDRESSES__)
            } else {
                Set-DnsClientServerAddress -InterfaceIndex $a.ifIndex -ResetServerAddresses
            }
            Write-Output ('OK|' + $a.Name)
        } catch {
            Write-Output ('ERR|' + $a.Name + '|' + $_.Exception.Message)
        }
    }
    Clear-DnsClientCache -ErrorAction SilentlyContinue
} catch {
    Write-Output ('FATAL|' + $_.Exception.Message)
    exit 1
}
"""


def apply_dns(enable: bool, pair: dict | None, allow_install: bool = False,
              engine_dir: Optional[Path] = None) -> str:
    """Включить DNS пары / сбросить на DHCP (см. _PS_SCRIPT, какие адаптеры).
    После смены чистит DNS-кэш Windows — иначе старые ответы живут ещё
    минуты. Возвращает пустую строку при успехе или текст ошибки для
    пользователя.

    allow_install — по нажатию пользователя: нет службы и прав → предложить
    установить службу (окно UAC); engine_dir — zapret/bin для неё (может
    ещё не быть — служба встанет без winws).

    Выполняется синхронно — для UI есть apply_dns_async()."""
    addresses = _pair_addresses(pair or {}) if enable else []
    if enable and not addresses:
        return "У выбранного сервера нет корректных IP-адресов"
    try:
        runner.ensure_backend(engine_dir or Path(), allow_install)
    except ServiceError as e:
        return str(e)
    if service_client.service_installed():
        return _apply_via_service(enable, pair, addresses)
    return _apply_direct(enable, pair, addresses)


def _apply_via_service(enable: bool, pair: dict | None, addresses: list[str]) -> str:
    try:
        if enable:
            reply = service_client.session.request({"op": "dns-set", "servers": addresses})
        else:
            reply = service_client.session.request({"op": "dns-reset"})
    except ServiceError as e:
        logger.error(f"DNS через службу: {e}")
        return str(e)
    if reply.get("no_adapter"):
        logger.warning("DNS: нет включённых сетевых адаптеров")
        return "Нет активного сетевого подключения"
    done = reply.get("adapters") or []
    failed = [f.get("name", "?") for f in reply.get("failed") or []]
    for f in reply.get("failed") or []:
        logger.error(f"DNS: {f.get('name')}: {f.get('error')}")
    return _summary(enable, pair, addresses, done, failed, "через службу")


def _apply_direct(enable: bool, pair: dict | None, addresses: list[str]) -> str:
    script = (_PS_SCRIPT
              .replace("__ENABLE__", "$true" if enable else "$false")
              .replace("__ADDRESSES__", ", ".join(f"'{a}'" for a in addresses)))
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", script],
            capture_output=True, timeout=_PS_TIMEOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        logger.error(f"DNS: PowerShell не ответил за {_PS_TIMEOUT} с")
        return "Windows не ответила вовремя — попробуйте ещё раз"
    except Exception as e:
        logger.error(f"Ошибка DNS: {e}")
        return str(e)

    out = result.stdout.decode("utf-8", errors="replace").splitlines()
    done, failed = [], []
    for line in (l.strip() for l in out):
        if line == "NOADAPTER":
            logger.warning("DNS: нет включённых сетевых адаптеров")
            return "Нет активного сетевого подключения"
        if line.startswith("OK|"):
            done.append(line[3:])
        elif line.startswith("ERR|"):
            name, _, msg = line[4:].partition("|")
            failed.append(name)
            logger.error(f"DNS: {name}: {msg}")
        elif line.startswith("FATAL|"):
            logger.error(f"DNS: {line[6:]}")
            return "Не удалось изменить DNS — подробности в логе"

    if result.returncode != 0 and not done:
        err = result.stderr.decode("cp866", errors="replace").strip()
        logger.error(f"DNS: PowerShell вернул {result.returncode}: {err}")
        return "Не удалось изменить DNS — подробности в логе"

    return _summary(enable, pair, addresses, done, failed, "напрямую")


def _summary(enable: bool, pair: dict | None, addresses: list[str],
             done: list[str], failed: list[str], via: str) -> str:
    what = f"DNS установлен {via}: {(pair or {}).get('name', '?')} ({', '.join(addresses)})" if enable \
        else f"DNS сброшен на DHCP {via}"
    logger.info(f"{what} | Адаптеры: {', '.join(done) or '—'}"
                + (f" | Ошибка: {', '.join(failed)}" if failed else ""))
    if failed and not done:
        return f"Не удалось изменить DNS на адаптере: {', '.join(failed)}"
    return ""


def apply_dns_async(
    enable: bool,
    pair: Optional[dict],
    on_done: Callable[[bool, str], None],
    allow_install: bool = False,
    engine_dir: Optional[Path] = None,
) -> None:
    """Асинхронная обёртка над apply_dns() — сама создаёт поток и зовёт
    on_done(ok, error) по завершении. Вызывающий UI-код (dashboard) только
    оборачивает on_done в Signal.emit."""

    def _worker() -> None:
        error = apply_dns(enable, pair, allow_install, engine_dir)
        on_done(not error, error)

    threading.Thread(target=_worker, daemon=True, name="dns-apply").start()
