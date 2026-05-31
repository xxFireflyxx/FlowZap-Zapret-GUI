"""
core/tg_proxy.py
----------------
Управление tg-ws-proxy — локальным MTProto прокси для Telegram.
Exe лежит в: <app_root>/tgproxy/TgWsProxy_windows.exe
"""

import logging
import subprocess
import threading
import os
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger(__name__)

PROXY_EXE_NAME = "TgWsProxy_windows.exe"
PROXY_DIR_NAME = "tgproxy"
PROXY_PORT     = 1443
PROXY_HOST     = "127.0.0.1"


def _find_secret(proxy_dir: Path) -> Optional[str]:
    """Найти secret из конфига tg-ws-proxy."""
    for cfg_name in ("config.json", "tgwsproxy.json", "config.toml"):
        cfg = proxy_dir / cfg_name
        if cfg.exists():
            try:
                text = cfg.read_text(encoding="utf-8")
                import re
                m = re.search(r'"secret"\s*:\s*"([^"]+)"', text)
                if m:
                    return m.group(1)
            except Exception:
                pass
    return None


def build_tg_link(secret: Optional[str] = None) -> str:
    """Собрать tg://proxy ссылку."""
    base = f"tg://proxy?server={PROXY_HOST}&port={PROXY_PORT}"
    if secret:
        base += f"&secret={secret}"
    return base


class TgProxyManager:
    """Менеджер процесса tg-ws-proxy."""

    def __init__(self, app_root: Path, on_state_change: Callable[[bool], None] = None) -> None:
        self._exe      = app_root / PROXY_DIR_NAME / PROXY_EXE_NAME
        self._dir      = app_root / PROXY_DIR_NAME
        self._proc: Optional[subprocess.Popen] = None
        self._enabled  = False
        self._on_state = on_state_change
        self._lock     = threading.Lock()

    @property
    def is_available(self) -> bool:
        return self._exe.exists()

    @property
    def is_running(self) -> bool:
        if self._proc is None:
            return False
        return self._proc.poll() is None

    def start(self) -> bool:
        with self._lock:
            if self.is_running:
                return True
            if not self.is_available:
                logger.warning(f"TgWsProxy не найден: {self._exe}")
                return False
            try:
                self._proc = subprocess.Popen(
                    [str(self._exe)],
                    cwd=str(self._dir),
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                self._enabled = True
                logger.info(f"TgWsProxy запущен (PID {self._proc.pid})")
                if self._on_state:
                    self._on_state(True)
                return True
            except Exception as e:
                logger.error(f"Ошибка запуска TgWsProxy: {e}")
                return False

    def stop(self) -> None:
        with self._lock:
            if self._proc and self.is_running:
                pid = self._proc.pid
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:
                        self._proc.kill()
                        self._proc.wait(timeout=2)
                    except Exception:
                        pass
                except Exception:
                    pass
                # Гарантированно убиваем через taskkill (на случай если процесс завис)
                if os.name == "nt":
                    try:
                        subprocess.run(
                            ["taskkill", "/F", "/IM", PROXY_EXE_NAME],
                            capture_output=True,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                            timeout=3,
                        )
                    except Exception:
                        pass
                logger.info(f"TgWsProxy остановлен (PID {pid})")
            self._proc    = None
            self._enabled = False
            if self._on_state:
                self._on_state(False)

    def toggle(self) -> bool:
        if self.is_running:
            self.stop()
            return False
        else:
            return self.start()

    def open_in_telegram(self) -> None:
        import webbrowser
        secret = _find_secret(self._dir)
        link = build_tg_link(secret)
        logger.info(f"Открываем Telegram: {link}")
        webbrowser.open(link)

    def copy_link(self) -> str:
        secret = _find_secret(self._dir)
        return build_tg_link(secret)
