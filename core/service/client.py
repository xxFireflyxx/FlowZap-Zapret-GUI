"""
core/service/client.py
----------------------
Связь с фоновой службой FlowZap (service/FlowZapService.cs): установка и
удаление (одно окно UAC), состояние через диспетчер служб Windows и
команды по именованному каналу \\\\.\\pipe\\flowzap-service.

Протокол: «4 байта длины (little-endian) + JSON в UTF-8» в обе стороны.
Подлинность службы проверяется по PID: процесс на том конце канала должен
быть процессом службы FlowZapService из диспетчера служб — подставной
канал другой программы не примет наших команд.

Сессия одна на всё приложение и держится открытой: служба гасит winws,
когда отключается FlowZap, который его запустил (закрыли или упал).
Синхронно: вызывать из фоновых потоков.
"""

import base64
import ctypes
import json
import logging
import os
import struct
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SERVICE_NAME = "FlowZapService"
PIPE_NAME = "flowzap-service"
PROTOCOL = 1
SERVICE_DIR = Path(os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles") or r"C:\Program Files") \
    / "FlowZap" / "Service"

_IO_TIMEOUT_MS = 20_000
_MAX_REPLY = 4 * 1024 * 1024

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)

ERROR_FILE_NOT_FOUND = 2
ERROR_SERVICE_ALREADY_RUNNING = 1056
ERROR_SERVICE_DOES_NOT_EXIST = 1060
ERROR_PIPE_BUSY = 231
ERROR_IO_PENDING = 997
ERROR_CANCELLED = 1223


class ServiceError(RuntimeError):
    """Ошибка службы с текстом для пользователя."""


def bundled_service_exe() -> Path:
    """FlowZapService.exe, который идёт с этой версией FlowZap."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "service" / "FlowZapService.exe"


def _exe_version(path: Path) -> Optional[tuple[int, ...]]:
    from core.system.fileinfo import get_exe_version
    text = get_exe_version(path) if path.exists() else None
    try:
        return tuple(int(x) for x in text.split(".")) if text else None
    except ValueError:
        return None


def bundled_version() -> Optional[tuple[int, ...]]:
    """Версия службы, которая идёт с этой версией FlowZap."""
    return _exe_version(bundled_service_exe())


def installed_version() -> Optional[tuple[int, ...]]:
    """Версия установленной службы (её exe пользователям доступен на чтение)."""
    return _exe_version(SERVICE_DIR / "FlowZapService.exe")


def update_available() -> bool:
    """Установленная служба старее той, что идёт с FlowZap: нужна
    переустановка (одно окно UAC) — бывает, только когда меняется код службы."""
    new, old = bundled_version(), installed_version()
    return bool(new and old and old < new)


def version_text(v: Optional[tuple[int, ...]]) -> str:
    return ".".join(str(x) for x in v) if v else "?"


def is_admin() -> bool:
    try:
        return bool(_shell32.IsUserAnAdmin())
    except Exception:
        return False


# ── Диспетчер служб ─────────────────────────────────────────────────────

_SC_MANAGER_CONNECT = 0x0001
_SERVICE_QUERY_STATUS = 0x0004
_SERVICE_START = 0x0010
_SC_STATUS_PROCESS_INFO = 0

_STATES = {1: "stopped", 2: "starting", 3: "stopping", 4: "running", 5: "continuing", 6: "pausing", 7: "paused"}


class _ServiceStatusProcess(ctypes.Structure):
    _fields_ = [
        ("dwServiceType", wintypes.DWORD), ("dwCurrentState", wintypes.DWORD),
        ("dwControlsAccepted", wintypes.DWORD), ("dwWin32ExitCode", wintypes.DWORD),
        ("dwServiceSpecificExitCode", wintypes.DWORD), ("dwCheckPoint", wintypes.DWORD),
        ("dwWaitHint", wintypes.DWORD), ("dwProcessId", wintypes.DWORD), ("dwServiceFlags", wintypes.DWORD),
    ]


_advapi32.OpenSCManagerW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
_advapi32.OpenSCManagerW.restype = wintypes.HANDLE
_advapi32.OpenServiceW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD]
_advapi32.OpenServiceW.restype = wintypes.HANDLE
_advapi32.QueryServiceStatusEx.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                           ctypes.POINTER(wintypes.DWORD)]
_advapi32.StartServiceW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p]
_advapi32.CloseServiceHandle.argtypes = [wintypes.HANDLE]


def query_service(start: bool = False) -> tuple[str, int]:
    """(состояние, PID процесса службы). Состояние: missing / stopped /
    starting / running / … ; start=True — запустить, если остановлена
    (вошедшим пользователям это разрешено правами службы)."""
    scm = _advapi32.OpenSCManagerW(None, None, _SC_MANAGER_CONNECT)
    if not scm:
        raise ctypes.WinError(ctypes.get_last_error())
    svc = None
    try:
        svc = _advapi32.OpenServiceW(scm, SERVICE_NAME, _SERVICE_QUERY_STATUS | (_SERVICE_START if start else 0))
        if not svc:
            err = ctypes.get_last_error()
            if err == ERROR_SERVICE_DOES_NOT_EXIST:
                return "missing", 0
            raise ctypes.WinError(err)
        if start and not _advapi32.StartServiceW(svc, 0, None):
            err = ctypes.get_last_error()
            if err != ERROR_SERVICE_ALREADY_RUNNING:
                raise ctypes.WinError(err)
        status = _ServiceStatusProcess()
        needed = wintypes.DWORD()
        if not _advapi32.QueryServiceStatusEx(svc, _SC_STATUS_PROCESS_INFO, ctypes.byref(status),
                                              ctypes.sizeof(status), ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        return _STATES.get(status.dwCurrentState, "unknown"), int(status.dwProcessId)
    finally:
        if svc:
            _advapi32.CloseServiceHandle(svc)
        _advapi32.CloseServiceHandle(scm)


def service_installed() -> bool:
    try:
        return query_service()[0] != "missing"
    except OSError:
        return False


def _wait_running(timeout: float) -> int:
    """Запустить службу (если остановлена) и дождаться её. PID процесса."""
    deadline = time.monotonic() + timeout
    state, pid = query_service(start=True)
    while state != "running":
        if state == "missing":
            raise ServiceError("Фоновая служба FlowZap не установлена")
        if time.monotonic() > deadline:
            raise ServiceError("Фоновая служба FlowZap не запускается — переустановите её в настройках")
        time.sleep(0.2)
        state, pid = query_service()
    return pid


# ── Канал ───────────────────────────────────────────────────────────────

class _Overlapped(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD), ("hEvent", wintypes.HANDLE)]


_kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
_kernel32.CreateFileW.restype = wintypes.HANDLE
_kernel32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
_kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateEventW.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
_kernel32.GetOverlappedResult.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Overlapped),
                                          ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
_kernel32.GetNamedPipeServerProcessId.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
for _fn in (_kernel32.ReadFile, _kernel32.WriteFile):
    _fn.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                    ctypes.POINTER(_Overlapped)]

_INVALID_HANDLE = wintypes.HANDLE(-1).value
# Минимальные права клиента канала: чтение, запись, атрибуты, ожидание
_PIPE_ACCESS = 0x0001 | 0x0002 | 0x0080 | 0x00100000
_OPEN_EXISTING = 3
# Асинхронный ввод-вывод (для тайм-аутов) + уровень олицетворения
# «только опознание»: подставной сервер не сможет действовать от нашего имени
_PIPE_FLAGS = 0x40000000 | 0x00100000 | 0x00010000


class _Pipe:
    def __init__(self, name: str, server_pid: Optional[int]) -> None:
        path = "\\\\.\\pipe\\" + name
        deadline = time.monotonic() + 5
        while True:
            handle = _kernel32.CreateFileW(path, _PIPE_ACCESS, 0, None, _OPEN_EXISTING, _PIPE_FLAGS, None)
            if handle != _INVALID_HANDLE:
                break
            err = ctypes.get_last_error()
            if err == ERROR_PIPE_BUSY:
                _kernel32.WaitNamedPipeW(path, 1000)
            elif err == ERROR_FILE_NOT_FOUND and time.monotonic() < deadline:
                time.sleep(0.15)        # служба только что запустилась и ещё создаёт канал
            else:
                raise ctypes.WinError(err)
            if time.monotonic() > deadline:
                raise ServiceError("Фоновая служба FlowZap не отвечает")
        self._handle = handle
        if server_pid is not None:
            pid = wintypes.ULONG()
            if not _kernel32.GetNamedPipeServerProcessId(self._handle, ctypes.byref(pid)) or pid.value != server_pid:
                self.close()
                raise ServiceError("Не удалось подтвердить подлинность фоновой службы FlowZap")

    def close(self) -> None:
        if self._handle:
            _kernel32.CloseHandle(self._handle)
            self._handle = None

    def _io(self, buffer, size: int, write: bool, timeout_ms: int = _IO_TIMEOUT_MS) -> int:
        event = _kernel32.CreateEventW(None, True, False, None)
        if not event:
            raise ctypes.WinError(ctypes.get_last_error())
        ov = _Overlapped(hEvent=event)
        done = wintypes.DWORD()
        try:
            fn = _kernel32.WriteFile if write else _kernel32.ReadFile
            if not fn(self._handle, buffer, size, ctypes.byref(done), ctypes.byref(ov)):
                err = ctypes.get_last_error()
                if err != ERROR_IO_PENDING:
                    raise ConnectionError(f"Связь со службой прервалась ({err})")
                if _kernel32.WaitForSingleObject(event, timeout_ms) != 0:
                    _kernel32.CancelIoEx(self._handle, ctypes.byref(ov))
                    _kernel32.GetOverlappedResult(self._handle, ctypes.byref(ov), ctypes.byref(done), True)
                    raise TimeoutError("Фоновая служба не ответила вовремя")
                if not _kernel32.GetOverlappedResult(self._handle, ctypes.byref(ov), ctypes.byref(done), False):
                    raise ConnectionError(f"Связь со службой прервалась ({ctypes.get_last_error()})")
            if done.value == 0:
                raise ConnectionError("Служба закрыла соединение")
            return done.value
        finally:
            _kernel32.CloseHandle(event)

    def _write_all(self, data: bytes) -> None:
        sent = 0
        while sent < len(data):
            chunk = data[sent:sent + 65536]
            buf = ctypes.create_string_buffer(chunk, len(chunk))
            sent += self._io(buf, len(chunk), True)

    def _read_exact(self, size: int, timeout_ms: int = _IO_TIMEOUT_MS) -> bytes:
        out = bytearray()
        while len(out) < size:
            want = min(65536, size - len(out))
            buf = ctypes.create_string_buffer(want)
            got = self._io(buf, want, False, timeout_ms)
            out += buf.raw[:got]
        return bytes(out)

    def request(self, message: dict, timeout: Optional[float] = None) -> dict:
        """timeout — сколько ждать ответа (по умолчанию 20 с; обновление
        winws службой качает zapret — ему дольше)."""
        body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._write_all(struct.pack("<i", len(body)) + body)
        size, = struct.unpack("<i", self._read_exact(4, int(timeout * 1000) if timeout else _IO_TIMEOUT_MS))
        if not 0 < size <= _MAX_REPLY:
            raise ConnectionError("Некорректный ответ службы")
        return json.loads(self._read_exact(size).decode("utf-8"))


class ServiceSession:
    """Одно постоянное соединение со службой на всё приложение."""

    def __init__(self, pipe_name: str = PIPE_NAME, authenticate: bool = True) -> None:
        self._pipe_name = pipe_name
        self._authenticate = authenticate
        self._pipe: Optional[_Pipe] = None
        self._lock = threading.RLock()
        self.info: dict = {}

    def connect(self) -> dict:
        with self._lock:
            if self._pipe is None:
                server_pid = _wait_running(15) if self._authenticate else None
                self._pipe = _Pipe(self._pipe_name, server_pid)
                hello = self._send({"op": "hello"})
                if hello.get("protocol") != PROTOCOL:
                    self.close()
                    raise ServiceError("Версия фоновой службы не подходит к FlowZap — переустановите её в настройках")
                self.info = hello
                logger.info(f"Связь со службой: версия {hello.get('version')}, zapret {hello.get('engine_version') or '—'}")
            return self.info

    def close(self) -> None:
        with self._lock:
            if self._pipe is not None:
                self._pipe.close()
                self._pipe = None

    def _send(self, message: dict, timeout: Optional[float] = None) -> dict:
        try:
            reply = self._pipe.request(message, timeout)
        except (OSError, ConnectionError, ValueError) as e:
            self.close()
            raise ServiceError(f"Связь с фоновой службой прервалась: {e}") from e
        if not reply.get("ok"):
            error = reply.get("error") or "Фоновая служба отказала"
            if error.startswith("Неизвестная команда"):
                error = "Фоновая служба устарела — обновите её: «Настройки» → «Фоновая служба»"
            raise ServiceError(error)
        return reply

    def request(self, message: dict, timeout: Optional[float] = None) -> dict:
        with self._lock:
            self.connect()
            return self._send(message, timeout)

    # ── команды ──
    def status(self, since: int = -1) -> dict:
        return self.request({"op": "status", "since": since})

    def start_winws(self, args: list, files: list) -> dict:
        return self.request({"op": "start", "args": args, "files": files})

    def stop_winws(self) -> dict:
        return self.request({"op": "stop"})

    def update_engine(self, tag: str) -> str:
        """Служба сама скачивает zapret этой версии (GitHub / SourceForge),
        сверяет sha256 и ставит себе winws. Без UAC. Версия, что встала."""
        reply = self.request({"op": "engine-update", "tag": tag or ""}, timeout=300)
        return reply.get("engine_version") or ""


session = ServiceSession()


def service_request_from_cmd(cmd: list[str]) -> tuple[list, list]:
    """Команду winws (core/zapret/winws.build_winws_cmd) — в запрос службе:
    аргументы-файлы заменяются ссылками, а сами файлы читаются здесь (с
    правами пользователя) и уходят содержимым. Путь к winws.exe не
    передаётся — служба запускает свой."""
    args: list = []
    files: list = []
    index: dict[str, int] = {}
    for arg in cmd[1:]:
        name, sep, value = arg.partition("=")
        prefix, target = "", value
        if value.startswith("@"):
            prefix, target = "@", value[1:]
        path = Path(target) if sep and target else None
        if path is not None and ("\\" in target or "/" in target) and path.is_file():
            key = str(path.resolve()).lower()
            if key not in index:
                index[key] = len(files)
                files.append({"name": path.name, "data": base64.b64encode(path.read_bytes()).decode("ascii")})
            args.append({"opt": name, "prefix": prefix, "file": index[key]})
        else:
            args.append(arg)
    return args, files


# ── Установка / удаление (одно окно UAC) ────────────────────────────────

class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
                ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]


_shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_ShellExecuteInfo)]
_kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]

_SEE_MASK_NOCLOSEPROCESS = 0x40
_SEE_MASK_NOASYNC = 0x100

_INSTALL_ERRORS = {
    2: "Нужны права администратора",
    4: "Ошибка установки — подробности в C:\\Program Files\\FlowZap\\Service\\logs\\install.log",
}


def _run_elevated(args: list[str], timeout: float = 180) -> int:
    """Запустить FlowZapService.exe с правами администратора (окно UAC) и
    дождаться. Код выхода; отказ в UAC — ServiceError."""
    exe = bundled_service_exe()
    if not exe.exists():
        raise ServiceError(f"Не найден {exe.name} — переустановите FlowZap")
    info = _ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = _SEE_MASK_NOCLOSEPROCESS | _SEE_MASK_NOASYNC
    info.lpVerb = "runas"
    info.lpFile = str(exe)
    info.lpParameters = subprocess.list2cmdline(args)
    info.lpDirectory = str(exe.parent)
    info.nShow = 0
    if not _shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.get_last_error()
        if err == ERROR_CANCELLED:
            raise ServiceError("Установка фоновой службы отменена — без неё обход не запустится")
        raise ServiceError(f"Не удалось запустить установку службы ({err})")
    try:
        if _kernel32.WaitForSingleObject(info.hProcess, int(timeout * 1000)) != 0:
            raise ServiceError("Установка фоновой службы не завершилась вовремя")
        code = wintypes.DWORD()
        _kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return int(code.value)
    finally:
        _kernel32.CloseHandle(info.hProcess)


def install(engine_dir: Path, core_version: str = "") -> None:
    """Установить (или переустановить) службу: winws из engine_dir (папка
    zapret/bin) копируется в защищённую папку службы; если zapret ещё не
    скачан — служба ставится без winws (DNS работает). Одно окно UAC."""
    from core.system import autostart
    session.close()
    logger.info("Установка фоновой службы FlowZap…")
    # Установщик удаляет задачу автозапуска от FlowZap до 1.0 (она требует
    # администратора) — если она была, переносим автозапуск в реестр
    had_legacy_task = autostart.legacy_task_exists()
    code = _run_elevated(["--install", str(Path(engine_dir).resolve()), core_version or ""])
    if code != 0:
        raise ServiceError(_INSTALL_ERRORS.get(code, f"Не удалось установить службу (код {code})"))
    _wait_running(20)
    logger.info("Фоновая служба установлена")
    if had_legacy_task and not autostart.legacy_task_exists():
        try:
            autostart.set_autostart(True)
            logger.info("Автозапуск перенесён из Планировщика в реестр пользователя")
        except Exception as e:
            logger.warning(f"Не удалось перенести автозапуск: {e}")


def uninstall() -> None:
    session.close()
    logger.info("Удаление фоновой службы FlowZap…")
    code = _run_elevated(["--uninstall"])
    if code != 0:
        raise ServiceError(_INSTALL_ERRORS.get(code, f"Не удалось удалить службу (код {code})"))
    logger.info("Фоновая служба удалена")
