"""
core/system/autostart.py
------------------------
Автозапуск FlowZap вместе с Windows — запись в реестре пользователя
(HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run). Прав администратора
не нужно: обход и DNS работают через фоновую службу FlowZap.

До 1.0 автозапуск был задачей Планировщика «с наивысшими правами» (FlowZap
требовал администратора). Такую задачу без прав не удалить, поэтому её
убирает установщик службы (он и так запускается с UAC), а FlowZap после
установки переносит автозапуск в реестр (core/service/client.install). Пока
задача жива, второй записи не делаем — иначе при входе запустились бы два
FlowZap.

*_async-обёртки сами создают поток и зовут on_done из него — UI оборачивает
колбэк в Signal, как у остальных core-модулей.
"""

import logging
import subprocess
import sys
import threading
import winreg
from pathlib import Path
from typing import Callable

log = logging.getLogger("flowzap.autostart")

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "FlowZap"
LEGACY_TASK = "FlowZap"


def _command() -> str:
    """Команда запуска FlowZap для записи в реестре."""
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable)}"'
    # Из исходников — через pythonw, чтобы не было консоли
    pythonw = Path(sys.executable).parent / "pythonw.exe"
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    return f'"{exe}" "{Path(__file__).resolve().parents[2] / "main.py"}"'


def _run_value_exists() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
            return True
    except OSError:
        return False


def legacy_task_exists() -> bool:
    """Осталась ли задача Планировщика от FlowZap до 1.0."""
    try:
        result = subprocess.run(["schtasks", "/query", "/tn", LEGACY_TASK],
                                capture_output=True, timeout=5,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        return result.returncode == 0
    except Exception:
        return False


def _remove_legacy_task() -> bool:
    """Удалить старую задачу. Без прав администратора обычно не выйдет — тогда
    её уберёт установщик фоновой службы."""
    try:
        result = subprocess.run(["schtasks", "/delete", "/tn", LEGACY_TASK, "/f"],
                                capture_output=True, timeout=10,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        return result.returncode == 0
    except Exception:
        return False


def autostart_enabled() -> bool:
    """Запускается ли FlowZap вместе с Windows (новым способом или старым)."""
    return _run_value_exists() or legacy_task_exists()


def set_autostart(enable: bool) -> None:
    """Включить/выключить автозапуск. Бросает RuntimeError с текстом для пользователя."""
    if enable:
        if legacy_task_exists():
            return          # старая задача и так запустит FlowZap — второй записи не нужно
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, _command())
        log.info("Автозапуск включён (реестр пользователя)")
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, RUN_VALUE)
    except FileNotFoundError:
        pass
    if legacy_task_exists() and not _remove_legacy_task():
        raise RuntimeError("Старую задачу автозапуска можно убрать только установкой фоновой "
                           "службы или запуском FlowZap от администратора")
    log.info("Автозапуск выключен")


def _registered_exe() -> Path | None:
    """exe из записи автозапуска (первый аргумент, в кавычках или без)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE)
    except OSError:
        return None
    value = str(value).strip()
    if value.startswith('"'):
        end = value.find('"', 1)
        return Path(value[1:end]) if end > 0 else None
    return Path(value.split(" ", 1)[0]) if value else None


def repair_autostart_path() -> bool:
    """Папку FlowZap перенесли или старую копию удалили — запись автозапуска
    указывает на exe, которого нет, и Windows молча ничего не запускает, а
    галочка в настройках стоит. Переписываем на этот FlowZap. Только для
    собранного FlowZap и только когда прежнего exe нет: работающая копия в
    другой папке (тестовая) автозапуск не перехватывает. True — исправлено."""
    if not getattr(sys, "frozen", False):
        return False
    old = _registered_exe()
    if old is None or old.exists():
        return False
    set_autostart(True)
    log.info(f"Автозапуск указывал на несуществующий {old} — теперь на {Path(sys.executable)}")
    return True


def migrate_legacy_autostart() -> bool:
    """Задача Планировщика от FlowZap до 1.0 → запись в реестре. Переносим,
    только если задачу удалось удалить (FlowZap сейчас от администратора);
    иначе её удалит установщик службы, а перенос сделает core/service/client.install.
    True — автозапуск перенесён."""
    if not legacy_task_exists() or not _remove_legacy_task():
        return False
    set_autostart(True)
    log.info("Автозапуск перенесён из Планировщика в реестр пользователя")
    return True


def autostart_enabled_async(on_done: Callable[[bool], None]) -> None:
    """autostart_enabled() в фоне (schtasks — до 5 с). on_done(enabled)."""
    threading.Thread(target=lambda: on_done(autostart_enabled()), daemon=True, name="autostart-check").start()


def set_autostart_async(enable: bool, on_done: Callable[[bool, str], None]) -> None:
    """set_autostart() в фоне. on_done(ok, error): error — текст ошибки или ""."""

    def _worker() -> None:
        try:
            set_autostart(enable)
        except Exception as e:
            log.error(f"Не удалось изменить автозапуск (enable={enable}): {e}")
            on_done(False, str(e))
            return
        on_done(True, "")

    threading.Thread(target=_worker, daemon=True, name="autostart-set").start()
