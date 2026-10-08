"""
core/zapret/manager.py
----------------------
Управление жизненным циклом процесса zapret (winws.exe): запуск, остановка,
рестарт. Команду запуска из пресета собирает core/zapret/winws.build_winws_cmd —
та же, что у проверки пресетов. Запускает winws core/zapret/runner: через фоновую
службу FlowZap или (запасной режим, FlowZap от администратора) напрямую.

Для UI есть *_async-варианты: операции выполняются по очереди в одном
фоновом потоке (tasklist/taskkill и ожидание завершения winws занимают до
нескольких секунд — раньше окно на это время подвисало). Результат UI
узнаёт через on_state_change. Синхронные start/stop — для выхода из
приложения и фоновых потоков.
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Optional

from core.zapret import runner
from core.service import client as service_client
from core.system import winproc
from core.zapret.runner import WinwsHandle
from core.service.client import ServiceError
from core.zapret.winws import WINWS_EXE, build_winws_cmd, warmup_windivert

logger = logging.getLogger(__name__)


class ServiceState(Enum):
    STOPPED  = auto()
    STARTING = auto()
    RUNNING  = auto()
    STOPPING = auto()
    ERROR    = auto()


class ServiceManager:
    """
    Управляет одним процессом winws.exe.

    on_log(str) — каждая строка лога и вывода winws; on_state_change(state) —
    смена состояния. Оба зовутся из фоновых потоков — UI маршалит их через Signal.
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

        self._process:  Optional[WinwsHandle] = None
        self._state:    ServiceState = ServiceState.STOPPED
        # Текст причины последней ошибки запуска для пользователя (нет службы,
        # нет пресета…); None — winws запустился, но завершился с ошибкой
        self.last_error: Optional[str] = None
        self._bat_path: Optional[Path] = None
        self._stop_event = threading.Event()
        # start/stop/restart не должны идти параллельно (из очереди *_async и,
        # при выходе, синхронно из GUI-потока)
        self._lock = threading.RLock()
        self._ops = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"{name}-ops")

    # ── Свойства ──────────────────────────────

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

    @property
    def bat_path(self) -> Optional[Path]:
        """Пресет (.bat), с которым сервис запускали последний раз. Сохраняется и после stop()."""
        return self._bat_path

    # ── Управление (синхронно) ────────────────

    def start(self, bat_path: "Path | None" = None, allow_install: bool = False) -> bool:
        """Запустить winws.exe с аргументами пресета (по умолчанию — последнего).
        allow_install — по нажатию пользователя: нет службы → предложить её
        установить (окно UAC). Автозапуск окно UAC не показывает."""
        with self._lock:
            if self._state in (ServiceState.RUNNING, ServiceState.STARTING):
                self._emit_log(f"[WARN] {self.name} уже запущен (state={self._state.name})")
                return False

            bat = Path(bat_path) if bat_path else self._bat_path
            self._bat_path = bat
            self._stop_event.clear()
            self.last_error = None
            self._set_state(ServiceState.STARTING)
            self._emit_log(f"[START] Запуск {self.name}...")

            try:
                if bat is None or not bat.exists():
                    return self._fail(f"Пресет не найден: {bat}")
                if not self.executable.exists():
                    return self._fail(f"winws.exe не найден: {self.executable}")
                built = build_winws_cmd(bat, self.executable)
                if built is None:
                    return self._fail(f"Не удалось прочитать аргументы winws из {bat.name}")
                cmd, cwd = built
                self._emit_log(f"[INFO] Пресет: {bat.name}")
                self._emit_log(f"[DEBUG] Аргументы: {' '.join(cmd[1:6])}…")
                self._process = runner.launch(cmd, cwd, on_line=self._on_winws_line,
                                              allow_install=allow_install)
            except ServiceError as exc:
                return self._fail(str(exc), user_text=str(exc))
            except Exception as exc:
                return self._fail(f"Не удалось запустить: {exc}")

            self._set_state(ServiceState.RUNNING)
            via = "через фоновую службу" if service_client.service_installed() else "напрямую"
            self._emit_log(f"[INFO] {self.name} запущен {via} (PID {self._process.pid})")
            threading.Thread(target=self._watch_process, args=(self._process,),
                             daemon=True, name=f"{self.name}-watcher").start()
            return True

    def stop(self) -> bool:
        """Остановить winws.exe."""
        with self._lock:
            if self._state not in (ServiceState.RUNNING, ServiceState.STARTING):
                return False
            self._set_state(ServiceState.STOPPING)
            self._emit_log(f"[STOP] Остановка {self.name}...")
            self._terminate()
            self._set_state(ServiceState.STOPPED)
            self._emit_log(f"[INFO] {self.name} остановлен")
            return True

    def restart(self, bat_path: "Path | None" = None) -> bool:
        """Перезапуск с тем же (или новым) пресетом. Промежуточное «остановлен»
        подписчику не отправляем — плитка не мигает «Выключен» между запусками."""
        with self._lock:
            self._emit_log(f"[INFO] Рестарт {self.name}...")
            if self._state in (ServiceState.RUNNING, ServiceState.STARTING):
                self._terminate()
            self._state = ServiceState.STOPPED
            return self.start(bat_path)

    # ── Управление (в фоне, для UI) ───────────

    def start_async(self, bat_path: "Path | None" = None, allow_install: bool = False) -> None:
        self._submit(self.start, bat_path, allow_install)

    def stop_async(self) -> None:
        self._submit(self.stop)

    def restart_async(self, bat_path: "Path | None" = None) -> None:
        self._submit(self.restart, bat_path)

    def _submit(self, fn, *args) -> None:
        def _run() -> None:
            try:
                fn(*args)
            except Exception as exc:   # из пула исключение никто бы не увидел
                logger.error(f"{self.name}: {fn.__name__}: {exc}", exc_info=True)
        self._ops.submit(_run)

    def warmup_windivert(self, on_done: Optional[Callable[[], None]] = None) -> None:
        """Прогрев драйвера WinDivert (~3 с) в фоновом потоке, если winws
        ещё нигде не запущен (см. core/zapret/winws.warmup_windivert). on_done
        зовётся из фонового потока."""

        def _worker() -> None:
            try:
                if not self.executable.exists():
                    self._emit_log("[DEBUG] Прогрев WinDivert: winws.exe не найден, пропуск")
                elif runner.uses_service() or not service_client.is_admin():
                    # Через службу прогрев не нужен: проверка пресетов ждёт
                    # готовности winws («capture is started»)
                    pass
                elif self.is_running or winproc.is_running(WINWS_EXE):
                    self._emit_log("[DEBUG] Прогрев WinDivert: winws.exe уже запущен, пропуск")
                else:
                    warmup_windivert(self.executable)
            finally:
                if on_done:
                    on_done()

        threading.Thread(target=_worker, daemon=True, name=f"{self.name}-windivert-warmup").start()

    # ── Внутреннее ────────────────────────────

    def _fail(self, message: str, user_text: Optional[str] = None) -> bool:
        self.last_error = user_text
        self._emit_log(f"[ERROR] {message}")
        self._set_state(ServiceState.ERROR)
        return False

    def _terminate(self) -> None:
        self._stop_event.set()
        if not self._process:
            return
        try:
            self._process.terminate()
            self._emit_log("[INFO] winws.exe завершён")
        except Exception as e:
            self._emit_log(f"[WARN] Ошибка при остановке: {e}")

    def _set_state(self, new_state: ServiceState) -> None:
        if self._state != new_state:
            self._state = new_state
            self.on_state_change(new_state)

    def _emit_log(self, message: str) -> None:
        """Строка лога подписчику + в Python-логгер. Уровень логгера — по
        префиксу сообщения, иначе в собранном exe (level=INFO) терялись бы ошибки."""
        full_msg = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
        if message.startswith("[ERROR]"):
            logger.error(full_msg)
        elif message.startswith("[WARN]"):
            logger.warning(full_msg)
        elif message.startswith("[DEBUG]"):
            logger.debug(full_msg)
        else:
            logger.info(full_msg)
        self.on_log(full_msg)

    def _on_winws_line(self, line: str) -> None:
        if not self._stop_event.is_set():
            self.on_log(line)

    def _watch_process(self, process: WinwsHandle) -> None:
        """Фоновый поток: ждёт завершения процесса и меняет состояние.
        process передаётся аргументом: поток, следящий за СТАРЫМ процессом
        после restart(), не должен трогать состояние нового. Проверка — под
        той же блокировкой, что start/stop: иначе посреди restart() (флаг
        остановки уже сброшен, новый процесс ещё не создан) завершение
        старого выглядело как падение и плитка мигала ошибкой."""
        return_code = process.wait()
        with self._lock:
            if self._process is not process or self._stop_event.is_set():
                return
            if return_code != 0:
                self._emit_log(f"[ERROR] {self.name} завершился с кодом {return_code}")
                self._set_state(ServiceState.ERROR)
            else:
                self._emit_log(f"[INFO] {self.name} завершился (код 0)")
                self._set_state(ServiceState.STOPPED)


class ZapretManager:
    """Фасад над ServiceManager zapret. Создаётся один раз в main.py и передаётся в UI."""

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

    def start(self, bat_path: "Path | None" = None, allow_install: bool = False) -> bool:
        return self.zapret.start(bat_path, allow_install)

    def stop(self) -> bool:
        return self.zapret.stop()

    def restart(self, bat_path: "Path | None" = None) -> bool:
        return self.zapret.restart(bat_path)

    def start_async(self, bat_path: "Path | None" = None, allow_install: bool = False) -> None:
        self.zapret.start_async(bat_path, allow_install)

    def stop_async(self) -> None:
        self.zapret.stop_async()

    def restart_async(self, bat_path: "Path | None" = None) -> None:
        self.zapret.restart_async(bat_path)

    def warmup_windivert(self, on_done: Optional[Callable[[], None]] = None) -> None:
        self.zapret.warmup_windivert(on_done)

    @property
    def state(self) -> ServiceState:
        return self.zapret.state

    @property
    def is_running(self) -> bool:
        return self.zapret.is_running

    @property
    def pid(self) -> Optional[int]:
        return self.zapret.pid

    @property
    def bat_path(self) -> Optional[Path]:
        return self.zapret.bat_path

    def shutdown_all(self) -> None:
        """Остановить все сервисы — вызывать при закрытии приложения (синхронно)."""
        self.zapret.stop()
