"""
main.py
-------
Точка входа FlowZap.
"""

import sys
import io
if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()
import logging
import traceback
import tomllib
import socket
import threading
from pathlib import Path

# ─────────────────────────────────────────────
#  Single Instance Check (Безопасный метод через сокет)
# ─────────────────────────────────────────────
INSTANCE_PORT = 58392
_single_instance_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

def wake_existing_and_exit():
    """Отправляет сигнал развертывания первому экземпляру и закрывается."""
    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect(("127.0.0.1", INSTANCE_PORT))
        client.sendall(b"WAKEUP")
        client.close()
    except Exception:
        pass
    sys.exit(0)

try:
    # Пытаемся занять локальный порт
    _single_instance_sock.bind(("127.0.0.1", INSTANCE_PORT))
    _single_instance_sock.listen(1)
except socket.error:
    # Порт занят -> программа уже работает, будим её и выходим
    wake_existing_and_exit()


# Определяем ROOT — папка где лежит exe или скрипт
if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).parent
else:
    ROOT = Path(__file__).parent

# ─────────────────────────────────────────────
#  faulthandler — ловит нативные крахи
# ─────────────────────────────────────────────
import faulthandler
(ROOT / "logs").mkdir(parents=True, exist_ok=True)
_fh_log = open(ROOT / "logs" / "faulthandler.log", "w", buffering=1, encoding="utf-8")
faulthandler.enable(file=_fh_log)

# ─────────────────────────────────────────────
#  Аварийный лог
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
    level = logging.INFO if getattr(sys, "frozen", False) else logging.DEBUG
    logging.basicConfig(
        level=level,
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
        "ui":      {"theme": "earthy", "remember_tab": True},
        "updater": {"repo": "Flowseal/zapret-discord-youtube", "check_on_start": True},
        "dns": {
            "pairs": [{
                "name":        "xbox-dns",
                "ipv4_main":   "111.88.96.50",
                "ipv4_backup": "111.88.96.51",
                "ipv6_main":   "2a00:ab00:1233:26::50",
                "ipv6_backup": "2a00:ab00:1233:26::51",
                "main":        "111.88.96.50",
                "backup":      "111.88.96.51",
            }],
            "servers": ["111.88.96.50", "111.88.96.51"],
        },
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
        dns = defaults.get("dns", {})
        if not dns.get("pairs") and dns.get("servers"):
            servers = dns["servers"]
            dns["pairs"] = [{
                "name":        "xbox-dns",
                "ipv4_main":   servers[0] if len(servers) > 0 else "",
                "ipv4_backup": servers[1] if len(servers) > 1 else "",
                "ipv6_main":   "2a00:ab00:1233:26::50",
                "ipv6_backup": "2a00:ab00:1233:26::51",
                "main":        servers[0] if len(servers) > 0 else "",
                "backup":      servers[1] if len(servers) > 1 else "",
            }]
    except Exception as exc:
        logging.getLogger(__name__).error(f"Ошибка чтения config.toml: {exc}")
    return defaults

def main() -> None:
    setup_logging(ROOT / "logs")
    log = logging.getLogger("flowzap")
    log.info("─── FlowZap запускается ───")

    # Если прошлое обновление прошло успешно (раз мы вообще смогли
    # стартовать), .old-хвосты больше не нужны — откатывать уже нечего.
    from core.updater import cleanup_old_update_leftovers
    cleanup_old_update_leftovers(ROOT)

    config = load_config(ROOT / "config.toml")
    log.debug(f"Конфиг: {config}")

    from ui.theme import theme
    theme.set_theme(config.get("ui", {}).get("theme", "earthy"))
    theme.apply_ctk_theme()

    from core.updater import set_github_token
    gh_token = config.get("github", {}).get("token", "")
    if gh_token:
        set_github_token(gh_token)
        log.info("GitHub токен установлен")

    from core.manager import ZapretManager
    _exe_raw = Path(config["zapret"]["exe_path"])
    zapret_exe = _exe_raw if _exe_raw.is_absolute() else ROOT / _exe_raw
    manager = ZapretManager(zapret_exe=zapret_exe)

    def _warmup_windivert():
        import subprocess, time
        winws = ROOT / "zapret" / "bin" / "winws.exe"
        if winws.exists():
            try:
                proc = subprocess.Popen(
                    [str(winws), "--wf-tcp=80"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    creationflags=0x08000000,
                )
                time.sleep(3)
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except Exception:
                    proc.kill()
                log.info("WinDivert прогрет при старте")
            except Exception as e:
                log.debug(f"Прогрев WinDivert: {e}")

    threading.Thread(target=_warmup_windivert, daemon=True, name="windivert-warmup").start()

    from ui.main_window import MainWindow
    config["_app_dir"] = str(ROOT)
    app = MainWindow(manager=manager, config=config, config_path=ROOT / "config.toml")

    # Фоновый поток: слушает сокет и разворачивает окно при сигнале "WAKEUP"
    def _listen_for_wakeup():
        while True:
            try:
                conn, addr = _single_instance_sock.accept()
                data = conn.recv(1024)
                if b"WAKEUP" in data:
                    def _restore():
                        if hasattr(app, "deiconify"):
                            app.deiconify()
                        if hasattr(app, "focus_force"):
                            app.focus_force()
                    app.after(0, _restore)
                conn.close()
            except Exception:
                break

    threading.Thread(target=_listen_for_wakeup, daemon=True, name="InstanceListener").start()

    def _migrate_win_autostart():
        from ui.settings_tab import ensure_win_autostart_migrated
        if ensure_win_autostart_migrated(config):
            app.after(0, app.save_config)

    def _schedule_migration():
        threading.Thread(target=_migrate_win_autostart, daemon=True, name="autostart-migrate").start()

    app.after(6000, _schedule_migration)

    def _sync_xbox_dns():
        from core.updater import sync_xbox_dns
        if sync_xbox_dns(config):
            app.after(0, app.save_config)

    def _schedule_xbox_dns_sync():
        threading.Thread(target=_sync_xbox_dns, daemon=True, name="xbox-dns-sync").start()

    app.after(6000, _schedule_xbox_dns_sync)

    if config["zapret"].get("autostart", False):
        log.info("Автозапуск zapret...")

        def _autostart():
            dashboard = app._tabs.get("dashboard")
            bat_path = None
            if dashboard and hasattr(dashboard, "_get_current_bat"):
                bat_path = dashboard._get_current_bat()
            if bat_path is None:
                from core.bat_parser import list_presets
                last = config["zapret"].get("last_preset", "")
                presets = list_presets(ROOT / "zapret")
                preset = next((p for p in presets if p["name"] == last), None)
                if preset:
                    bat_path = preset.get("path")
            manager.start(bat_path=bat_path)

        app.after(1500, _autostart)

    def _restore_state():
        dashboard = app._tabs.get("dashboard")
        if dashboard and hasattr(dashboard, "restore_state"):
            dashboard.restore_state()

    app.after(2000, _restore_state)

    from core.updater import update_gaming_lists, GAMING_LIST_DOMAINS, GAMING_LIST_IPSET
    _lists_dir = ROOT / "zapret" / "lists"
    _first_run = not (_lists_dir / GAMING_LIST_DOMAINS).exists()
    if _first_run:
        log.info("Первый запуск — загружаем игровые списки синхронно...")
        update_gaming_lists(_lists_dir, force=True)
        import time as _time
        _time.sleep(3)
    else:
        update_gaming_lists(_lists_dir)

    log.info("UI готов, запуск mainloop")

    app.mainloop()
    log.info("─── FlowZap завершён ───")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        err = traceback.format_exc()
        _write_crash(err)
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