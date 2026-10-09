"""
core/zapret/runner.py
---------------------
Запуск winws.exe — через фоновую службу FlowZap или напрямую.

  * Служба установлена → всегда через неё (FlowZap работает без прав
    администратора; см. core/service/client.py).
  * Службы нет, но FlowZap запущен от администратора (запасной режим из
    «Настроек») → напрямую, как раньше.
  * Иначе — NeedServiceError; launch(allow_install=True) сначала
    предлагает установить службу (одно окно UAC).

Оба варианта дают WinwsHandle с одинаковым поведением: ready (строка
«capture is started»), poll / wait / terminate, хвост вывода. Так
ServiceManager (запуск обхода) и PresetPingManager (проверка пресетов) не
знают, как именно запущен winws.
"""

import collections
import logging
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

from core.service import client as service_client
from core.system import winproc
from core.service.client import ServiceError
from core.zapret.winws import WINWS_EXE

logger = logging.getLogger(__name__)

READY_MARK = "capture is started"
_STATUS_POLL_SEC = 0.1      # пока ждём готовности winws (проверка пресетов ждёт её)
_STATUS_IDLE_SEC = 1.0      # потом: обход может работать часами


class NeedServiceError(ServiceError):
    """Без службы и без прав администратора обход не запустить."""


class WinwsHandle:
    """Запущенный winws (общая часть)."""

    def __init__(self, on_line: Optional[Callable[[str], None]]) -> None:
        self._on_line = on_line
        self.ready = threading.Event()
        self.tail: collections.deque[str] = collections.deque(maxlen=8)
        self.pid: Optional[int] = None
        self.returncode: Optional[int] = None
        self._done = threading.Event()

    def _line(self, line: str) -> None:
        self.tail.append(line)
        if READY_MARK in line.lower():
            self.ready.set()
        if self._on_line:
            self._on_line(line)

    def poll(self) -> Optional[int]:
        """None — работает; иначе код завершения (без ввода-вывода — можно из GUI)."""
        return self.returncode if self._done.is_set() else None

    def wait(self, timeout: Optional[float] = None) -> Optional[int]:
        self._done.wait(timeout)
        return self.poll()

    def terminate(self) -> None:
        raise NotImplementedError


class _DirectHandle(WinwsHandle):
    """winws, запущенный самим FlowZap (нужны права администратора)."""

    def __init__(self, cmd: list[str], cwd: Path, on_line) -> None:
        super().__init__(on_line)
        winproc.kill(WINWS_EXE)             # WinDivert — один перехват на компьютер
        _enable_tcp_timestamps()
        si = subprocess.STARTUPINFO()
        si.dwFlags = subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            cwd=str(cwd), creationflags=winproc.CREATE_NO_WINDOW, startupinfo=si,
        )
        self.pid = self._proc.pid
        threading.Thread(target=self._read, daemon=True, name="winws-reader").start()

    def _read(self) -> None:
        try:
            for raw in self._proc.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    self._line(line)
        except Exception:
            pass
        self.returncode = self._proc.wait()
        self._done.set()

    def terminate(self) -> None:
        try:
            self._proc.terminate()
            self._proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        except Exception:
            pass
        self._done.wait(3)


def _local_core_version(engine_dir: Path) -> str:
    """Версия zapret в папке FlowZap (zapret/version.txt) или ""."""
    try:
        f = Path(engine_dir).parent / "version.txt"
        return f.read_text(encoding="utf-8").strip() if f.exists() else ""
    except OSError:
        return ""


_synced_version: Optional[str] = None


def sync_engine(engine_dir: Path) -> None:
    """winws службы — той же версии, что zapret в FlowZap (пресеты рассчитаны
    на свою версию winws). Не совпадает или у службы winws ещё нет — служба
    сама скачивает нужную версию (без UAC). Не вышло, но старый winws у
    службы есть — работаем с ним; нет совсем — ошибка."""
    global _synced_version
    local = _local_core_version(engine_dir)
    if local and local == _synced_version:
        return
    current = service_client.session.status().get("engine_version") or ""
    if current and (current == local or not local):
        _synced_version = current
        return
    logger.info(f"Служба: winws {current or 'нет'} → zapret {local or 'последний'}, обновляю…")
    try:
        from core.dns.manager import retry_without_dns    # dns.manager импортирует runner
        _synced_version = retry_without_dns(lambda: service_client.session.update_engine(local),
                                            lambda e: isinstance(e, ServiceError))
        logger.info(f"Служба: winws обновлён до zapret {_synced_version}")
    except ServiceError as e:
        if not current:
            raise ServiceError(f"В фоновой службе нет winws, а скачать его не вышло: {e}") from e
        logger.warning(f"Служба: не удалось обновить winws ({e}) — работаю с zapret {current}")
        _synced_version = local


class _ServiceHandle(WinwsHandle):
    """winws, запущенный фоновой службой. Состояние и вывод — опросом
    службы в отдельном потоке (poll/wait сами канал не трогают)."""

    def __init__(self, cmd: list[str], on_line) -> None:
        super().__init__(on_line)
        sync_engine(Path(cmd[0]).parent)
        args, files = service_client.service_request_from_cmd(cmd)
        status = service_client.session.start_winws(args, files)
        self.pid = status.get("pid")
        self._seq = status.get("seq", 0) - 1     # строку «winws запущен» тоже показать
        self._stopping = False
        self._wake = threading.Event()
        threading.Thread(target=self._monitor, daemon=True, name="winws-service-monitor").start()

    def _monitor(self) -> None:
        while True:
            try:
                status = service_client.session.status(self._seq)
            except ServiceError as e:
                logger.error(f"Служба: {e}")
                self.returncode = -1
                break
            self._seq = status.get("seq", self._seq)
            for line in status.get("lines") or []:
                if not line.startswith("[служба]"):
                    self._line(line)
            if status.get("ready"):
                self.ready.set()
            if not status.get("running") or status.get("pid") != self.pid:
                code = status.get("exit_code")
                self.returncode = 0 if self._stopping or code is None else int(code)
                break
            if self._wake.wait(_STATUS_POLL_SEC if not self.ready.is_set() else _STATUS_IDLE_SEC):
                break                   # terminate() уже всё выяснил
        self._done.set()

    def terminate(self) -> None:
        """Остановить (если это всё ещё наш winws). Ответ службы на stop уже
        означает «остановлен» — не ждём очередного опроса монитора (раньше
        это стоило до 1 с на каждый пресет проверки)."""
        self._stopping = True
        try:
            status = service_client.session.status()
            if status.get("running") and status.get("pid") == self.pid:
                service_client.session.stop_winws()
        except ServiceError as e:
            logger.warning(f"Служба: остановка winws: {e}")
        if self.returncode is None:
            self.returncode = 0
        self._wake.set()
        self._done.set()


def uses_service() -> bool:
    return service_client.service_installed()


def ensure_backend(engine_dir: Path, allow_install: bool) -> None:
    """Убедиться, что winws есть чем запускать: служба установлена или
    FlowZap от администратора. Иначе при allow_install=True — установить
    службу (окно UAC; только по действию пользователя), иначе NeedServiceError.
    engine_dir — zapret/bin: оттуда служба при установке берёт winws."""
    if service_client.service_installed() or service_client.is_admin():
        return
    if not allow_install:
        raise NeedServiceError("Нужна фоновая служба FlowZap — установите её в «Настройках»")
    engine_dir = Path(engine_dir)
    version_file = engine_dir.parent / "version.txt"
    version = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else ""
    service_client.install(engine_dir, version)


def launch(cmd: list[str], cwd: Path, on_line: Optional[Callable[[str], None]] = None,
           allow_install: bool = False) -> WinwsHandle:
    """Запустить winws командой из core/winws.build_winws_cmd (см. ensure_backend)."""
    ensure_backend(Path(cmd[0]).parent, allow_install)
    if service_client.service_installed():
        return _ServiceHandle(cmd, on_line)
    return _DirectHandle(cmd, cwd, on_line)


def stop_all() -> None:
    """Остановить любой winws (выход, обновление Core). Синхронно."""
    if service_client.service_installed():
        try:
            if service_client.session.status().get("running"):
                service_client.session.stop_winws()
        except ServiceError as e:
            logger.warning(f"Служба: {e}")
    else:
        winproc.kill(WINWS_EXE)


_timestamps_done = False


def _enable_tcp_timestamps() -> None:
    """TCP timestamps нужны WinDivert. В режиме службы это делает служба."""
    global _timestamps_done
    if _timestamps_done:
        return
    _timestamps_done = True
    try:
        subprocess.run(["netsh", "interface", "tcp", "set", "global", "timestamps=enabled"],
                       capture_output=True, creationflags=winproc.CREATE_NO_WINDOW, timeout=5)
    except Exception:
        pass
