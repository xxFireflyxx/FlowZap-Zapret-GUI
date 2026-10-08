"""
core/tgproxy/manager.py
-----------------------
Управление tg-ws-proxy — локальным MTProto прокси для Telegram.
Exe лежит в: <app_root>/tgproxy/TgWsProxy_windows.exe
"""

import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from core.system import winproc

logger = logging.getLogger(__name__)

PROXY_EXE_NAME = "TgWsProxy_windows.exe"
PROXY_DIR_NAME = "tgproxy"
PROXY_PORT     = 1080
PROXY_HOST     = "127.0.0.1"

# Сколько ждать после запуска, прежде чем считать, что прокси поднялся:
# при занятом порте или битом конфиге процесс завершается почти сразу.
_START_CHECK_SEC = 0.7

_SECRET_PATTERNS = [re.compile(p, re.IGNORECASE) for p in (
    r'secret[=:\s]+([0-9a-fA-F]{32,})',
    r'key[=:\s]+([0-9a-fA-F]{32,})',
    r'"secret"\s*:\s*"([^"]+)"',
    r'proxy.*secret.*?([0-9a-fA-F]{32,})',
)]


def _read_proxy_config(proxy_dir: Path) -> tuple[Optional[str], int]:
    """(secret, port) из конфига tg-ws-proxy: сначала %APPDATA%\\TgWsProxy\\config.json,
    затем файлы рядом с exe. Чего нет — None / порт по умолчанию."""
    candidates = [
        Path(os.environ.get("APPDATA", "")) / "TgWsProxy" / "config.json",
        proxy_dir / "config.json",
        proxy_dir / "tgwsproxy.json",
    ]
    secret, port = None, None
    for cfg in candidates:
        if not cfg.exists():
            continue
        try:
            text = cfg.read_text(encoding="utf-8")
        except Exception:
            continue
        try:
            data = json.loads(text)
            secret = secret or data.get("secret")
            port = port or data.get("port")
        except Exception:
            m = re.search(r'"secret"\s*:\s*"([^"]+)"', text)
            if m and not secret:
                secret = m.group(1)
        if secret and port:
            break
    try:
        port = int(port) if port else PROXY_PORT
    except (TypeError, ValueError):
        port = PROXY_PORT
    return secret, port


def build_tg_link(secret: Optional[str] = None, port: int = PROXY_PORT) -> str:
    """Собрать tg://proxy ссылку."""
    base = f"tg://proxy?server={PROXY_HOST}&port={port}"
    if secret:
        base += f"&secret={secret}"
    return base


class TgProxyManager:
    """Менеджер процесса tg-ws-proxy."""

    def __init__(self, app_root: Path, on_state_change: Callable[[bool], None] = None) -> None:
        self._dir      = app_root / PROXY_DIR_NAME
        self._exe      = self._dir / PROXY_EXE_NAME
        self._proc: Optional[subprocess.Popen] = None
        self._on_state = on_state_change
        self._lock     = threading.Lock()
        self._secret:  Optional[str] = None

    @property
    def is_available(self) -> bool:
        return self._exe.exists()

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> bool:
        """Запустить прокси и убедиться, что он не упал сразу. Синхронный
        (ждёт ~0,7 с) — вызывать из фонового потока."""
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
                    stdin=subprocess.DEVNULL,
                    creationflags=winproc.CREATE_NO_WINDOW,
                )
            except Exception as e:
                logger.error(f"Ошибка запуска TgWsProxy: {e}")
                return False
            # Вывод читаем в фоне — ищем secret и не даём переполниться pipe
            threading.Thread(target=self._read_output, args=(self._proc,),
                             daemon=True, name="tgproxy-reader").start()
            time.sleep(_START_CHECK_SEC)
            if self._proc.poll() is not None:
                logger.error(f"TgWsProxy завершился сразу после запуска (код {self._proc.returncode}) — "
                             "возможно, порт занят другой программой")
                self._proc = None
                return False
            logger.info(f"TgWsProxy запущен (PID {self._proc.pid})")
            if self._on_state:
                self._on_state(True)
            return True

    def stop(self) -> None:
        with self._lock:
            if self.is_running:
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
                # Гарантированно (на случай, если процесс завис)
                winproc.kill(PROXY_EXE_NAME)
                logger.info(f"TgWsProxy остановлен (PID {pid})")
            self._proc = None
            if self._on_state:
                self._on_state(False)

    def _read_output(self, proc: subprocess.Popen) -> None:
        """Читать вывод процесса — искать secret."""
        try:
            for line in proc.stdout:
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                logger.debug(f"TgWsProxy: {text}")
                for pattern in _SECRET_PATTERNS:
                    m = pattern.search(text)
                    if m:
                        self._secret = m.group(1)
                        logger.info(f"TgWsProxy secret найден: {self._secret[:8]}...")
                        break
        except Exception:
            pass

    def check_running_async(self, on_done: Callable[[bool, str], None]) -> None:
        """Определить, запущен ли TgWsProxy в системе (в т.ч. вне FlowZap).
        Сам создаёт поток, зовёт on_done(running, "")."""
        threading.Thread(
            target=lambda: on_done(winproc.is_running(PROXY_EXE_NAME), ""),
            daemon=True, name="tgproxy-check",
        ).start()

    def set_running_async(self, enable: bool, on_done: Callable[[bool, str], None]) -> None:
        """Включить/выключить TgWsProxy. on_done(running, error): running —
        ФАКТИЧЕСКОЕ состояние после операции (не целевое), error — текст для
        пользователя или ""."""

        def _worker() -> None:
            if enable:
                if not self.is_available:
                    on_done(False, f"TgWsProxy не найден: {self._exe}")
                elif not self.start():
                    on_done(False, "Не удалось запустить TgWsProxy — подробности в логе.")
                else:
                    on_done(True, "")
                return
            error = self.stop_all()
            on_done(bool(error), error)

        threading.Thread(target=_worker, daemon=True, name="tgproxy-toggle").start()

    def stop_all(self) -> str:
        """Синхронно остановить TgWsProxy — свой процесс через stop(), внешний
        (запущенный не из FlowZap) — по имени. "" при успехе или текст ошибки,
        если процесс всё ещё жив. Для выхода из приложения и set_running_async."""
        if self.is_running:
            self.stop()
        else:
            winproc.kill(PROXY_EXE_NAME)
        if winproc.is_running(PROXY_EXE_NAME):
            return "Не удалось остановить TgWsProxy (возможно, нужны права администратора)."
        return ""

    def open_in_telegram(self) -> None:
        import webbrowser
        secret, port = _read_proxy_config(self._dir)
        link = build_tg_link(self._secret or secret, port)
        logger.info("Открываем Telegram: tg://proxy (127.0.0.1)")
        webbrowser.open(link)
