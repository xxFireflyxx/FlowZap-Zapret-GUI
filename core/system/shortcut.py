"""
core/system/shortcut.py
-----------------------
Ярлык FlowZap на рабочем столе. Чистая логика без UI.

Перенесено из старой вкладки настроек с двумя отличиями: только PowerShell (без
win32com — результат тот же, зависимость не нужна) и папка рабочего стола берётся
через GetFolderPath('Desktop'), а не USERPROFILE\\Desktop — старый путь не работает,
если рабочий стол перенаправлен (например, в OneDrive).
"""

import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable


def _ps_quote(value) -> str:
    """Значение для одинарных кавычек PowerShell: ' удваивается."""
    return str(value).replace("'", "''")


def create_desktop_shortcut(app_dir: Path) -> None:
    """Создаёт FlowZap.lnk на рабочем столе. Бросает RuntimeError при ошибке.
    Синхронная — вызывать из фонового потока (см. create_desktop_shortcut_async)."""
    exe_path = Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2] / "main.py"
    icon_path = Path(app_dir) / "assets" / "icon.ico"
    script = (
        "$s=(New-Object -COM WScript.Shell).CreateShortcut("
        "(Join-Path ([Environment]::GetFolderPath('Desktop')) 'FlowZap.lnk'));"
        f"$s.TargetPath='{_ps_quote(exe_path)}';"
        f"$s.WorkingDirectory='{_ps_quote(exe_path.parent)}';"
    )
    if icon_path.exists():
        script += f"$s.IconLocation='{_ps_quote(icon_path)}';"
    script += "$s.Save()"
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, text=True, timeout=10,
        encoding="cp866", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())


def create_desktop_shortcut_async(app_dir: Path, on_done: Callable[[bool, str], None]) -> None:
    """Сама создаёт поток и зовёт on_done(ok, error) — как остальные core-модули."""

    def _worker() -> None:
        try:
            create_desktop_shortcut(app_dir)
        except Exception as e:
            import logging
            logging.getLogger("flowzap.shortcut").error(f"Ошибка создания ярлыка: {e}")
            on_done(False, str(e))
            return
        on_done(True, "")

    threading.Thread(target=_worker, daemon=True, name="shortcut-create").start()
