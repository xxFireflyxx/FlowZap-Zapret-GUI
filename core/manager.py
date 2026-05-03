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
        self._bat_path:   Optional[Path] = None
        self._winws_pid:  Optional[int] = None
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

    def start(self, args: list[str] | None = None, bat_path: "Path | None" = None) -> bool:
        """
        Запустить сервис.
        Если bat_path передан — запускает bat через cmd /c (bat сам загружает переменные и запускает winws.exe).
        Иначе — запускает executable напрямую с args.
        """
        if self._state in (ServiceState.RUNNING, ServiceState.STARTING):
            self._emit_log(f"[WARN] {self.name} уже запущен (state={self._state.name})")
            return False

        self._bat_path = Path(bat_path) if bat_path else None
        self._args = args or []
        self._winws_pid = None
        self._stop_event.clear()
        self._set_state(ServiceState.STARTING)
        self._emit_log(f"[START] Запуск {self.name}...")

        try:
            # Убиваем старый winws.exe если уже запущен
            if os.name == "nt":
                try:
                    result = subprocess.run(
                        ["tasklist", "/FI", "IMAGENAME eq winws.exe", "/FO", "CSV", "/NH"],
                        capture_output=True, text=True, encoding="cp866", errors="replace",
                        creationflags=0x08000000,
                    )
                    if "winws.exe" in result.stdout:
                        subprocess.run(["taskkill", "/F", "/IM", "winws.exe"],
                                       capture_output=True, creationflags=0x08000000)
                        self._emit_log("[INFO] Старый winws.exe завершён")
                except Exception:
                    pass

            if self._bat_path and self._bat_path.exists():
                self._emit_log(f"[INFO] Режим: прямой запуск winws.exe из пресета ({self._bat_path.name})")

                from core.bat_parser import parse_bat
                bat_args = parse_bat(self._bat_path) or self._args

                bin_dir   = self._bat_path.parent / "bin"
                lists_dir = self._bat_path.parent / "lists"

                # Создаём пустые пользовательские файлы если не существуют —
                # winws.exe падает с ошибкой если файл указан но отсутствует
                _user_files = [
                    "list-general-user.txt",
                    "list-exclude-user.txt",
                    "list-exclude.txt",
                    "ipset-exclude-user.txt",
                    "ipset-exclude.txt",
                ]
                lists_dir.mkdir(parents=True, exist_ok=True)
                for _uf in _user_files:
                    _fp = lists_dir / _uf
                    if not _fp.exists():
                        try:
                            _fp.touch()
                            self._emit_log(f"[INFO] Создан пустой файл: {_uf}")
                        except Exception as _e:
                            self._emit_log(f"[WARN] Не удалось создать {_uf}: {_e}")

                # Определяем значение GameFilter
                game_flag = self._bat_path.parent / "utils" / "game_filter.enabled"
                if game_flag.exists():
                    game_ports = "1024-65535"
                    self._emit_log("[INFO] Game Filter: включён (1024-65535)")
                else:
                    game_ports = "12"  # фиктивный порт — фактически выключен
                    self._emit_log("[INFO] Game Filter: выключен")

                resolved_args = []
                for arg in bat_args:
                    # Подставляем GameFilter плейсхолдеры
                    arg = arg.replace("__GAMEFILTER_TCP__", game_ports)
                    arg = arg.replace("__GAMEFILTER_UDP__", game_ports)
                    # Убираем лишние запятые если плейсхолдер был в середине
                    arg = arg.replace(",,", ",").rstrip(",")

                    if "=" in arg:
                        key, val = arg.split("=", 1)
                        val = val.strip('"').strip("'")
                        for search_dir in (bin_dir, lists_dir):
                            candidate = search_dir / val
                            if candidate.exists():
                                val = str(candidate)
                                break
                            candidate2 = search_dir / Path(val).name
                            if candidate2.exists():
                                val = str(candidate2)
                                break
                        resolved_args.append(f"{key}={val}")
                    else:
                        resolved_args.append(arg)

                if not self.executable.exists():
                    self._emit_log(f"[ERROR] winws.exe не найден: {self.executable}")
                    self._set_state(ServiceState.ERROR)
                    return False

                cmd = [str(self.executable)] + resolved_args
                cwd = bin_dir
                self._emit_log(f"[DEBUG] Аргументы: {' '.join(resolved_args[:5])}…")
            else:
                if not self.executable.exists():
                    self._emit_log(f"[ERROR] Исполняемый файл не найден: {self.executable}")
                    self._set_state(ServiceState.ERROR)
                    return False
                self._emit_log(f"[INFO] Режим: прямой запуск winws.exe")
                cmd = [str(self.executable)] + self._args
                cwd = self.executable.parent

            # STARTUPINFO скрывает окно дочерних процессов запущенных через "start"
            # внутри bat файла (winws.exe запускается именно так)
            si = None
            if os.name == "nt":
                si = subprocess.STARTUPINFO()
                si.dwFlags = subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = 0  # SW_HIDE

            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=str(cwd),
                creationflags=0x08000000 if os.name == "nt" else 0,
                startupinfo=si,
            )
        except Exception as exc:
            self._emit_log(f"[ERROR] Не удалось запустить: {exc}")
            self._set_state(ServiceState.ERROR)
            return False

        self._set_state(ServiceState.RUNNING)
        self._emit_log(f"[INFO] {self.name} запущен (PID {self._process.pid})")

        self._log_thread = threading.Thread(
            target=self._read_output,
            daemon=True,
            name=f"{self.name}-log-reader",
        )
        self._log_thread.start()

        threading.Thread(
            target=self._watch_process,
            daemon=True,
            name=f"{self.name}-watcher",
        ).start()

        return True

    def stop(self) -> bool:
        """Остановить сервис — убивает winws.exe напрямую."""
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
                self._emit_log(f"[INFO] winws.exe завершён")
            except Exception as e:
                self._emit_log(f"[WARN] Ошибка при остановке: {e}")

        self._set_state(ServiceState.STOPPED)
        self._emit_log(f"[INFO] {self.name} остановлен")
        return True

    def restart(self, args: list[str] | None = None, bat_path: "Path | None" = None) -> bool:
        """Рестарт: стоп → старт с теми же (или новыми) аргументами/bat-файлом."""
        self._emit_log(f"[INFO] Рестарт {self.name}...")
        self.stop()
        return self.start(args or self._args, bat_path=bat_path or self._bat_path)

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

    def _find_winws_pid(self) -> Optional[int]:
        """Найти PID процесса winws.exe через tasklist."""
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq winws.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True,
                encoding="cp866", errors="replace",
                creationflags=0x08000000,
            )
            for line in result.stdout.strip().splitlines():
                parts = line.strip('"').split('","')
                if len(parts) >= 2:
                    try:
                        return int(parts[1])
                    except ValueError:
                        pass
        except Exception as e:
            self._emit_log(f"[WARN] tasklist error: {e}")
        return None

    def _watch_process(self) -> None:
        """Фоновый поток: ждёт завершения процесса и меняет состояние."""
        if not self._process:
            return
        return_code = self._process.wait()

        if not self._stop_event.is_set():
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

    def start(self, args: list[str] | None = None, bat_path: "Path | None" = None) -> bool:
        return self.zapret.start(args, bat_path=bat_path)

    def stop(self) -> bool:
        return self.zapret.stop()

    def restart(self, args: list[str] | None = None, bat_path: "Path | None" = None) -> bool:
        return self.zapret.restart(args, bat_path=bat_path)

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
