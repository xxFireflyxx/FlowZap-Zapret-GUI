"""
main.py
-------
Точка входа FlowZap.
"""

import sys
import logging
import traceback
import tomllib
from pathlib import Path

# При запуске из exe (PyInstaller onedir) используем папку с exe, иначе папку со скриптом
ROOT = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent

# ─────────────────────────────────────────────
#  Аварийный лог — пишем ДО настройки логгера
#  чтобы поймать ошибки импорта и старта
# ─────────────────────────────────────────────

CRASH_LOG = ROOT / "logs" / "crash.log"


def _write_crash(text: str) -> None:
    try:
        CRASH_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(CRASH_LOG, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass


def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "flowzap.log"
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def load_config(config_path: Path) -> dict:
    defaults = {
        "zapret": {
            "exe_path": str(ROOT / "zapret" / "bin" / "winws.exe"),
            "presets_dir": str(ROOT / "zapret"),
            "last_preset": "general",
            "args": [],
            "autostart": False,
        },
        "ui":      {"theme": "dark", "remember_tab": True},
        "updater": {"repo": "Flowseal/zapret-discord-youtube", "check_on_start": True},
        "dns":     {"servers": ["111.88.98.50", "111.88.96.51"]},
    }
    if not config_path.exists():
        return defaults
    try:
        with open(config_path, "rb") as f:
            user_cfg = tomllib.load(f)
        for section, values in user_cfg.items():
            if section in defaults and isinstance(values, dict):
                defaults[section].update(values)
            else:
                defaults[section] = values
    except Exception as exc:
        logging.getLogger(__name__).error(f"Ошибка чтения config.toml: {exc}")
    return defaults


def main() -> None:
    setup_logging(ROOT / "logs")
    log = logging.getLogger("flowzap")
    log.info("─── FlowZap запускается ───")

    config = load_config(ROOT / "config.toml")
    log.debug(f"Конфиг: {config}")

    from ui.theme import theme
    theme.apply_ctk_theme()

    from core.manager import ZapretManager
    _exe_raw = Path(config["zapret"]["exe_path"])
    zapret_exe = _exe_raw if _exe_raw.is_absolute() else ROOT / _exe_raw
    manager = ZapretManager(zapret_exe=zapret_exe)

    from ui.main_window import MainWindow
    config["_app_dir"] = str(ROOT)   # служебный ключ — путь к корню приложения
    app = MainWindow(manager=manager, config=config, config_path=ROOT / "config.toml")
    if config["zapret"].get("autostart", False):
        startup_args = config["zapret"].get("args", [])
        log.info("Автозапуск zapret...")
        app.after(500, lambda: manager.start(startup_args))

    log.info("UI готов, запуск mainloop")
    app.mainloop()
    log.info("─── FlowZap завершён ───")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        err = traceback.format_exc()
        _write_crash(err)
        # Показать окно с ошибкой даже без GUI
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                "FlowZap — критическая ошибка",
                f"Приложение упало при запуске.\n\n"
                f"Лог сохранён в:\n{CRASH_LOG}\n\n"
                f"{err[-800:]}",
            )
            root.destroy()
        except Exception:
            pass
        sys.exit(1)
