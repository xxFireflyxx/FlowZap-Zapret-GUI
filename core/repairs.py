"""
core/repairs.py
---------------
Исправления от автора: подписанный PowerShell-скрипт, который FlowZap
предлагает выполнить, когда в выпущенной версии что-то сломалось, а
обновление само по себе не поможет (например, сломано само обновление).

Файл repairs.toml в корне репозитория + repairs.toml.sig (подпись автора,
release/repair.py). Скачивается при запуске вместе с объявлениями
(core/announcements.py fetch_signed): без верной подписи файл игнорируется
целиком. Подпись привязана к имени файла — подписанные объявления за
исправления не выдать.

Ничего не выполняется само: FlowZap показывает плашку на главной, по кнопке —
окно с описанием и текстом скрипта, и только после «Применить» запускает его.
Скрипт работает с правами самого FlowZap (обычно — пользователя; через
фоновую службу — никогда).

    [[repair]]
    id = "2026-10-12-1"            # постоянный; применённое не предлагается снова
    title = "Починка обновления"
    text = "В 1.1.2 кнопка «Обновить» не ставит новую версию. Исправление скачает её само."
    min_version = "1.1.2"          # для каких версий FlowZap (включительно)
    max_version = "1.1.2"
    until = "2026-12-31"           # не предлагать после этой даты
    beta_only = true               # только с beta = true в [updater] (проверить на себе)
    exit_flowzap = true            # скрипт меняет сам FlowZap: запустить отдельно и
                                   # закрыть FlowZap (перезапустить его — дело скрипта)
    script = '''
    Write-Output "FlowZap: $env:FLOWZAP_DIR"
    '''

Скрипту передаются переменные окружения: FLOWZAP_DIR (папка FlowZap),
FLOWZAP_EXE и FLOWZAP_ARGS (чем перезапустить FlowZap; ARGS пуст у собранного,
из исходников — путь к main.py), FLOWZAP_PID (процесс FlowZap — с
exit_flowzap дождаться его выхода), FLOWZAP_VERSION. Вывод и код выхода
пишутся в logs/repair.log. Без exit_flowzap: код 0 — применено (больше не
предлагается), иначе — «не получилось», предложится при следующем запуске.
С exit_flowzap результат неизвестен — считается применённым сразу.
"""

import datetime
import logging
import os
import re
import subprocess
import sys
import threading
import tomllib
from pathlib import Path
from typing import Callable, Optional

from core.announcements import _version, fetch_signed

logger = logging.getLogger(__name__)

FILE_NAME = "repairs.toml"
LOG_NAME = "repair.log"
TIMEOUT_SEC = 300           # скрипт без exit_flowzap дольше — снимаем
_MAX_ITEMS = 3
# id попадает в имя файла скрипта — только безопасные символы
_RE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# Вывод PowerShell 5.1 в трубу — в кодировке консоли (cp866); с этой строкой — UTF-8
_PRELUDE = "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false\r\n"


def parse(data: bytes) -> list[dict]:
    """Исправления из TOML. Битые записи пропускаются (с предупреждением)."""
    raw = tomllib.loads(data.decode("utf-8"))
    result, seen = [], set()
    for item in raw.get("repair", []):
        if not isinstance(item, dict):
            continue
        rid = str(item.get("id", "")).strip()
        text = str(item.get("text", "")).strip()
        script = item.get("script")
        if not _RE_ID.match(rid) or rid in seen:
            logger.warning(f"{FILE_NAME}: некорректный или повторный id: {rid!r}")
            continue
        if not text or not isinstance(script, str) or not script.strip():
            logger.warning(f"{FILE_NAME}: «{rid}» — нет текста или скрипта")
            continue
        until = item.get("until")
        if isinstance(until, str):
            try:
                until = datetime.date.fromisoformat(until)
            except ValueError:
                logger.warning(f"{FILE_NAME}: «{rid}» — непонятная дата until={until!r}")
                until = None
        if isinstance(until, datetime.datetime):
            until = until.date()
        seen.add(rid)
        result.append({
            "id": rid,
            "title": str(item.get("title", "")).strip() or "Исправление от автора FlowZap",
            "text": text,
            "script": script,
            "min_version": str(item.get("min_version", "")).strip(),
            "max_version": str(item.get("max_version", "")).strip(),
            "until": until if isinstance(until, datetime.date) else None,
            "beta_only": item.get("beta_only") is True,
            "exit_flowzap": item.get("exit_flowzap") is True,
        })
    return result


def relevant(items: list[dict], gui_version: str, done, beta: bool = False,
             today: Optional[datetime.date] = None) -> list[dict]:
    """Что предложить этой версии: не применённое, не просроченное, версия в
    диапазоне, beta_only — только на бета-канале."""
    today = today or datetime.date.today()
    current = _version(gui_version)
    shown = []
    for item in items:
        if item["id"] in done:
            continue
        if item["beta_only"] and not beta:
            continue
        if item["until"] and today > item["until"]:
            continue
        if item["min_version"] and current < _version(item["min_version"]):
            continue
        if item["max_version"] and current > _version(item["max_version"]):
            continue
        shown.append(item)
    return shown[:_MAX_ITEMS]


def fetch() -> Optional[list[dict]]:
    """Скачать и проверить исправления. None — не удалось; [] — их нет."""
    return fetch_signed(FILE_NAME, parse, "Исправления")


# ── Выполнение ───────────────────────────────────────────────────────────

def _app_command(root: Path) -> tuple[str, str]:
    """Чем перезапустить FlowZap: (программа, аргументы). Собранный —
    FlowZap.exe без аргументов, из исходников — pythonw и main.py (как в
    записи автозапуска)."""
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable)), ""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    return str(exe), f'"{root / "main.py"}"'


def _env(root: Path, version: str) -> dict:
    exe, args = _app_command(root)
    env = dict(os.environ)
    env.update(FLOWZAP_DIR=str(root), FLOWZAP_EXE=exe, FLOWZAP_ARGS=args,
               FLOWZAP_PID=str(os.getpid()), FLOWZAP_VERSION=version)
    return env


def _write_script(item: dict, root: Path) -> Path:
    """Скрипт — в logs/ (UTF-8 с BOM: иначе PowerShell 5.1 читает кириллицу
    в кодировке ANSI)."""
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / f"repair-{item['id']}.ps1"
    body = item["script"].replace("\r\n", "\n").replace("\n", "\r\n")
    path.write_text(_PRELUDE + body, encoding="utf-8-sig", newline="")
    return path


def _command(script: Path) -> list[str]:
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(script)]


def _log(root: Path, text: str) -> None:
    try:
        with open(root / "logs" / LOG_NAME, "a", encoding="utf-8") as f:
            f.write(text)
    except OSError as e:
        logger.warning(f"Не удалось записать {LOG_NAME}: {e}")


def _header(item: dict, version: str, mode: str) -> str:
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"\n=== {stamp} — исправление «{item['id']}» ({mode}), FlowZap {version} ===\n"


def run(item: dict, root: Path, version: str, timeout: float = TIMEOUT_SEC) -> tuple[bool, str]:
    """Выполнить исправление (без exit_flowzap) и дождаться. (успех, текст
    для пользователя). Вывод — в logs/repair.log."""
    script = _write_script(item, root)
    _log(root, _header(item, version, "выполняю"))
    logger.info(f"Исправление «{item['id']}»: запускаю")
    try:
        proc = subprocess.run(_command(script), capture_output=True, timeout=timeout,
                              env=_env(root, version), cwd=str(root),
                              creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"") + (e.stderr or b"")
        _log(root, out.decode("utf-8", errors="replace") + f"\n[не уложилось в {int(timeout)} с — остановлено]\n")
        logger.warning(f"Исправление «{item['id']}»: не уложилось в {int(timeout)} с")
        return False, "исправление не уложилось во время и остановлено"
    except OSError as e:
        _log(root, f"[не запустилось: {e}]\n")
        logger.warning(f"Исправление «{item['id']}»: не запустилось — {e}")
        return False, f"не удалось запустить PowerShell: {e}"
    finally:
        script.unlink(missing_ok=True)
    out = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace")
    _log(root, out + (("\n[ошибки]\n" + err) if err.strip() else "") + f"\n[код выхода {proc.returncode}]\n")
    logger.info(f"Исправление «{item['id']}»: код выхода {proc.returncode}")
    if proc.returncode == 0:
        return True, ""
    return False, f"скрипт завершился с ошибкой (код {proc.returncode})"


def launch_detached(item: dict, root: Path, version: str) -> None:
    """Запустить исправление с exit_flowzap: отдельно от FlowZap (переживёт
    его выход), вывод — в logs/repair.log. Ошибка запуска — исключение."""
    script = _write_script(item, root)
    _log(root, _header(item, version, "запущено отдельно, FlowZap закрывается"))
    log = open(root / "logs" / LOG_NAME, "ab")
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    kwargs = dict(stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=_env(root, version),
                  cwd=str(root.parent))      # не из папки FlowZap — скрипт может её менять
    try:
        try:
            # CREATE_BREAKAWAY_FROM_JOB — не умереть вместе с FlowZap (как скрипт обновления)
            subprocess.Popen(_command(script), creationflags=flags | 0x01000000, **kwargs)
        except OSError:
            subprocess.Popen(_command(script), creationflags=flags, **kwargs)
    finally:
        log.close()
    logger.info(f"Исправление «{item['id']}»: запущено отдельно, FlowZap закрывается")


def run_async(item: dict, root: Path, version: str,
              on_done: Callable[[bool, str], None]) -> None:
    """run() в фоновом потоке; on_done(успех, текст) — из него же."""
    def _worker() -> None:
        try:
            ok, msg = run(item, root, version)
        except Exception as e:
            logger.exception("Исправление: сбой")
            ok, msg = False, str(e)
        on_done(ok, msg)
    threading.Thread(target=_worker, daemon=True, name="repair").start()
