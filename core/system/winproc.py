"""
core/system/winproc.py
----------------------
Поиск и завершение процессов по имени exe (tasklist / taskkill) — общие
для zapret (winws.exe), проверки пресетов и обновления Core.
Синхронные: вызывать из фонового потока.

process_names / image_path / kill_pid — то же через WinAPI, без запуска
tasklist: быстро (миллисекунды), годится для опроса раз в несколько секунд
(TG Proxy смотрит, запущен ли Telegram). Прав администратора не требуют
для процессов того же пользователя.
"""

import ctypes
import logging
import subprocess
from ctypes import wintypes
from typing import Optional

logger = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000

_TH32CS_SNAPPROCESS = 0x00000002
_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_INVALID_HANDLE = ctypes.c_void_p(-1).value


class _ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def _kernel32():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ProcessEntry)]
    k32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ProcessEntry)]
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, ctypes.c_wchar_p, ctypes.POINTER(wintypes.DWORD)]
    k32.TerminateProcess.argtypes = [ctypes.c_void_p, wintypes.UINT]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    return k32


def process_names() -> dict[int, str]:
    """{PID: имя exe} всех процессов системы. Пусто — если снимок не удался."""
    k32 = _kernel32()
    snap = k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snap or snap == _INVALID_HANDLE:
        return {}
    result: dict[int, str] = {}
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(_ProcessEntry)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            result[entry.th32ProcessID] = entry.szExeFile
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return result


def image_path(pid: int) -> Optional[str]:
    """Полный путь к exe процесса или None (нет процесса / нет доступа)."""
    k32 = _kernel32()
    handle = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return None
    finally:
        k32.CloseHandle(handle)


def kill_pid(pid: int) -> bool:
    """Завершить процесс по PID. True — удалось (или его уже нет)."""
    k32 = _kernel32()
    handle = k32.OpenProcess(_PROCESS_TERMINATE, False, pid)
    if not handle:
        return pid not in process_names()
    try:
        return bool(k32.TerminateProcess(handle, 1))
    finally:
        k32.CloseHandle(handle)


def find_pid(image: str) -> Optional[int]:
    """PID первого процесса с таким именем exe или None."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"],
            capture_output=True, encoding="cp866", errors="replace",
            creationflags=CREATE_NO_WINDOW, timeout=5,
        )
    except Exception as e:
        logger.warning(f"tasklist {image}: {e}")
        return None
    for line in result.stdout.strip().splitlines():
        parts = line.strip().strip('"').split('","')
        if len(parts) >= 2 and parts[0].lower() == image.lower():
            try:
                return int(parts[1])
            except ValueError:
                pass
    return None


def is_running(image: str) -> bool:
    return find_pid(image) is not None


def kill(image: str) -> None:
    """Принудительно завершить все процессы с таким именем exe. Если их
    нет — taskkill просто ничего не делает."""
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", image],
            capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=5,
        )
    except Exception as e:
        logger.warning(f"taskkill {image}: {e}")
