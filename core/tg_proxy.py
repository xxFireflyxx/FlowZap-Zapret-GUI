"""
core/tg_proxy.py
----------------
Управление tg-ws-proxy — локальным MTProto прокси для Telegram.
Exe лежит в: <app_root>/tgproxy/TgWsProxy_windows.exe
"""

import logging
import subprocess
import threading
import time
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
    # tg-ws-proxy хранит конфиг в config.json рядом с exe
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
        """Проверить что exe существует."""
        return self._exe.exists()

    @property
    def is_running(self) -> bool:
        if self._proc is None:
            return False
        return self._proc.poll() is None

    def start(self) -> bool:
        """Запустить tg-ws-proxy. Вернуть True если успешно."""
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
                    creationflags=subprocess.CREATE_NO_WINDOW,
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
        """Остановить tg-ws-proxy."""
        with self._lock:
            if self._proc and self.is_running:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=5)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
                logger.info("TgWsProxy остановлен")
            self._proc    = None
            self._enabled = False
            if self._on_state:
                self._on_state(False)

    def toggle(self) -> bool:
        """Переключить состояние. Вернуть новое состояние."""
        if self.is_running:
            self.stop()
            return False
        else:
            return self.start()

    def open_in_telegram(self) -> None:
        """Открыть tg://proxy ссылку в Telegram."""
        import webbrowser
        secret = _find_secret(self._dir)
        link = build_tg_link(secret)
        logger.info(f"Открываем Telegram: {link}")
        webbrowser.open(link)

    def copy_link(self) -> str:
        """Вернуть ссылку для копирования."""
        secret = _find_secret(self._dir)
        return build_tg_link(secret)
