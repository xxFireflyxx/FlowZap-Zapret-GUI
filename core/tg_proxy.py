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
PROXY_PORT     = 1080
PROXY_HOST     = "127.0.0.1"


def _find_secret(proxy_dir: Path) -> Optional[str]:
    """Найти secret из конфига tg-ws-proxy."""
    import re, json

    # Основное место — %APPDATA%\TgWsProxy\config.json
    appdata = Path(os.environ.get("APPDATA", "")) / "TgWsProxy" / "config.json"
    candidates = [appdata] + [proxy_dir / n for n in ("config.json", "tgwsproxy.json")]

    for cfg in candidates:
        if cfg.exists():
            try:
                data = json.loads(cfg.read_text(encoding="utf-8"))
                secret = data.get("secret")
                if secret:
                    return secret
            except Exception:
                try:
                    text = cfg.read_text(encoding="utf-8")
                    m = re.search(r'"secret"\s*:\s*"([^"]+)"', text)
                    if m:
                        return m.group(1)
                except Exception:
                    pass
    return None


def _find_port(proxy_dir: Path) -> int:
    """Найти порт из конфига tg-ws-proxy."""
    import json

    appdata = Path(os.environ.get("APPDATA", "")) / "TgWsProxy" / "config.json"
    candidates = [appdata] + [proxy_dir / n for n in ("config.json",)]

    for cfg in candidates:
        if cfg.exists():
            try:
                data = json.loads(cfg.read_text(encoding="utf-8"))
                port = data.get("port")
                if port:
                    return int(port)
            except Exception:
                pass
    return PROXY_PORT


def build_tg_link(secret: Optional[str] = None, port: int = PROXY_PORT) -> str:
    """Собрать tg://proxy ссылку."""
    base = f"tg://proxy?server={PROXY_HOST}&port={port}"
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
        self._secret:  Optional[str] = None

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
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                self._enabled = True
                logger.info(f"TgWsProxy запущен (PID {self._proc.pid})")
                if self._on_state:
                    self._on_state(True)
                # Читаем вывод в фоне — ищем secret
                threading.Thread(
                    target=self._read_output,
                    daemon=True,
                    name="tgproxy-reader"
                ).start()
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

    def _read_output(self) -> None:
        """Читать stdout процесса — искать secret и порт."""
        import re
        if not self._proc or not self._proc.stdout:
            return
        try:
            for line in self._proc.stdout:
                try:
                    text = line.decode("utf-8", errors="replace").strip()
                    if text:
                        logger.debug(f"TgWsProxy: {text}")
                    # Ищем secret в выводе
                    for pattern in [
                        r'secret[=:\s]+([0-9a-fA-F]{32,})',
                        r'key[=:\s]+([0-9a-fA-F]{32,})',
                        r'"secret"\s*:\s*"([^"]+)"',
                        r'proxy.*secret.*?([0-9a-fA-F]{32,})',
                    ]:
                        m = re.search(pattern, text, re.IGNORECASE)
                        if m:
                            self._secret = m.group(1)
                            logger.info(f"TgWsProxy secret найден: {self._secret[:8]}...")
                            break
                except Exception:
                    pass
        except Exception:
            pass

    def toggle(self) -> bool:
        if self.is_running:
            self.stop()
            return False
        else:
            return self.start()

    def open_in_telegram(self) -> None:
        import webbrowser
        secret = self._secret or _find_secret(self._dir)
        port   = _find_port(self._dir)
        link   = build_tg_link(secret, port)
        logger.info(f"Открываем Telegram: {link}")
        webbrowser.open(link)

    def copy_link(self) -> str:
        secret = self._secret or _find_secret(self._dir)
        port   = _find_port(self._dir)
        return build_tg_link(secret, port)
