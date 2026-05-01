"""
core/manager.py
---------------
Управление жизненным циклом процесса zapret (и в будущем zapret2).
Запуск, остановка, рестарт — всё здесь.
Использует subprocess + threading для неблокирующего чтения stdout/stderr.
"""

import subprocess
import threading
import logging
import shlex
import os
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  Состояния процесса
# ─────────────────────────────────────────────

class ServiceState(Enum):
    STOPPED  = auto()
    STARTING = auto()
    RUNNING  = auto()
    STOPPING = auto()
    ERROR    = auto()


# ─────────────────────────────────────────────
#  Менеджер одного сервиса (zapret / zapret2)
# ─────────────────────────────────────────────

class ServiceManager:
    """
    Управляет одним внешним процессом (zapret.exe или аналог).
    
    Параметры
    ---------
    name : str
        Читаемое имя сервиса (для логов и UI).
    executable : Path
        Путь к исполняемому файлу.
    on_log : Callable[[str], None]
        Коллбэк, вызываемый для каждой строки stdout/stderr.
    on_state_change : Callable[[ServiceState], None]
        Коллбэк при смене состояния.
    """

    def __init__(
        self,
        name: str,
        executable: Path,
        on_log: Optional[Callable[[str], None]] = None,
        on_state_change: Optional[Callable[[ServiceState], None]] = None,
    ) -> None:
        self.name       = name
        self.executable = Path(executable)
        self.on_log     = on_log or (lambda msg: None)
        self.on_state_change = on_state_change or (lambda state: None)

        self._process:    Optional[subprocess.Popen] = None
        self._state:      ServiceState = ServiceState.STOPPED
        self._args:       list[str]   = []
        self._log_thread: Optional[threading.Thread] = None
        self._stop_event: threading.Event = threading.Event()

    # ── Публичные свойства ────────────────────

    @property
    def state(self) -> ServiceState:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._state == ServiceState.RUNNING

    @property
    def pid(self) -> Optional[int]:
        if self._process and self._process.poll() is None:
            return self._process.pid
        return None

    # ── Управление ───────────────────────────

    def start(self, args: list[str] | None = None) -> bool:
        """
        Запустить сервис с переданными аргументами CLI.
        
        Returns
        -------
        bool
            True если запуск инициирован, False при ошибке.
        """
        if self._state in (ServiceState.RUNNING, ServiceState.STARTING):
            self._emit_log(f"[WARN] {self.name} уже запущен (state={self._state.name})")
            return False

        if not self.executable.exists():
            self._emit_log(f"[ERROR] Исполняемый файл не найден: {self.executable}")
            self._set_state(ServiceState.ERROR)
            return False

        self._args = args or []
        self._stop_event.clear()
        self._set_state(ServiceState.STARTING)
        self._emit_log(f"[START] Запуск {self.name}...")

        try:
            cmd = [str(self.executable)] + self._args
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,   # объединяем stderr в stdout
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,                  # построчный буфер
                cwd=self.executable.parent,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except Exception as exc:
            self._emit_log(f"[ERROR] Не удалось запустить: {exc}")
            self._set_state(ServiceState.ERROR)
            return False

        self._set_state(ServiceState.RUNNING)
        self._emit_log(f"[INFO] {self.name} запущен (PID {self._process.pid})")

        # Читаем вывод в отдельном потоке
        self._log_thread = threading.Thread(
            target=self._read_output,
            daemon=True,
            name=f"{self.name}-log-reader",
        )
        self._log_thread.start()

        # Поток-наблюдатель — следит за завершением процесса
        threading.Thread(
            target=self._watch_process,
            daemon=True,
            name=f"{self.name}-watcher",
        ).start()

        return True

    def stop(self) -> bool:
        """Остановить сервис (SIGTERM → SIGKILL через 3 сек)."""
        if self._state not in (ServiceState.RUNNING, ServiceState.STARTING):
            return False

        self._set_state(ServiceState.STOPPING)
        self._emit_log(f"[STOP] Остановка {self.name}...")
        self._stop_event.set()

        if self._process:
            try:
                self._process.terminate()
                try:
                    self._process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._emit_log(f"[WARN] {self.name} принудительно завершён (SIGKILL)")
            except Exception as exc:
                self._emit_log(f"[ERROR] Ошибка при остановке: {exc}")

        self._set_state(ServiceState.STOPPED)
        self._emit_log(f"[INFO] {self.name} остановлен")
        return True

    def restart(self, args: list[str] | None = None) -> bool:
        """Рестарт: стоп → старт с теми же (или новыми) аргументами."""
        self._emit_log(f"[INFO] Рестарт {self.name}...")
        self.stop()
        return self.start(args or self._args)

    def update_args(self, args: list[str]) -> None:
        """Обновить аргументы запуска (применятся при следующем старте)."""
        self._args = args

    # ── Внутренние методы ─────────────────────

    def _set_state(self, new_state: ServiceState) -> None:
        """Сменить состояние и уведомить подписчика."""
        if self._state != new_state:
            self._state = new_state
            self.on_state_change(new_state)

    def _emit_log(self, message: str) -> None:
        """Отправить строку лога подписчику + в Python-логгер."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        full_msg = f"[{timestamp}] {message}"
        logger.debug(full_msg)
        self.on_log(full_msg)

    def _read_output(self) -> None:
        """Фоновый поток: читает stdout процесса построчно."""
        if not self._process or not self._process.stdout:
            return
        try:
            for line in self._process.stdout:
                if self._stop_event.is_set():
                    break
                line = line.rstrip("\n\r")
                if line:
                    self.on_log(line)
        except Exception as exc:
            self._emit_log(f"[ERROR] Ошибка чтения вывода: {exc}")

    def _watch_process(self) -> None:
        """Фоновый поток: ждёт завершения процесса и меняет состояние."""
        if not self._process:
            return
        return_code = self._process.wait()
        if not self._stop_event.is_set():
            # Процесс завершился сам — возможно, ошибка
            if return_code != 0:
                self._emit_log(f"[ERROR] {self.name} завершился с кодом {return_code}")
                self._set_state(ServiceState.ERROR)
            else:
                self._emit_log(f"[INFO] {self.name} завершился (код 0)")
                self._set_state(ServiceState.STOPPED)


# ─────────────────────────────────────────────
#  Фасад для нескольких сервисов
# ─────────────────────────────────────────────

class ZapretManager:
    """
    Высокоуровневый менеджер. Управляет zapret (и в будущем zapret2).
    Создаётся один раз в main.py и передаётся в UI.
    """

    def __init__(
        self,
        zapret_exe: Path,
        on_log: Optional[Callable[[str], None]] = None,
        on_state_change: Optional[Callable[[ServiceState], None]] = None,
    ) -> None:
        self.zapret = ServiceManager(
            name="zapret",
            executable=zapret_exe,
            on_log=on_log,
            on_state_change=on_state_change,
        )
        # Место для будущего zapret2:
        # self.zapret2 = ServiceManager("zapret2", zapret2_exe, ...)

    # Удобные делегирующие методы

    def start(self, args: list[str] | None = None) -> bool:
        return self.zapret.start(args)

    def stop(self) -> bool:
        return self.zapret.stop()

    def restart(self, args: list[str] | None = None) -> bool:
        return self.zapret.restart(args)

    @property
    def state(self) -> ServiceState:
        return self.zapret.state

    @property
    def is_running(self) -> bool:
        return self.zapret.is_running

    @property
    def pid(self) -> Optional[int]:
        return self.zapret.pid

    def shutdown_all(self) -> None:
        """Остановить все сервисы — вызывать при закрытии приложения."""
        self.zapret.stop()
        # self.zapret2.stop()
