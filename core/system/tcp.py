"""
core/system/tcp.py
------------------
Таблица TCP-соединений Windows (IPv4) с PID владельца — GetExtendedTcpTable,
то же, что показывает `netstat -ano`, но без запуска процесса и без прав
администратора. TG Proxy по ней видит, слушает ли сервер порт и кто к нему
подключён.
"""

import ctypes
import socket
import struct
from ctypes import wintypes
from typing import NamedTuple, Optional

LISTEN = 2
ESTABLISHED = 5

_AF_INET = 2
_TCP_TABLE_OWNER_PID_ALL = 5
_ERROR_INSUFFICIENT_BUFFER = 122


class TcpConn(NamedTuple):
    state: int
    local: tuple[str, int]
    remote: tuple[str, int]
    pid: int


class _Row(ctypes.Structure):
    _fields_ = [
        ("state", wintypes.DWORD),
        ("local_addr", wintypes.DWORD),
        ("local_port", wintypes.DWORD),
        ("remote_addr", wintypes.DWORD),
        ("remote_port", wintypes.DWORD),
        ("pid", wintypes.DWORD),
    ]


def _addr(value: int) -> str:
    # Адрес лежит в DWORD в сетевом порядке байт — упаковываем как есть
    return socket.inet_ntoa(struct.pack("<I", value))


def _port(value: int) -> int:
    return socket.ntohs(value & 0xFFFF)


def connections() -> list[TcpConn]:
    """Все TCP-соединения IPv4. OSError — если Windows не отдала таблицу."""
    fn = ctypes.WinDLL("iphlpapi").GetExtendedTcpTable
    fn.restype = wintypes.DWORD
    fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
                   wintypes.ULONG, ctypes.c_int, wintypes.ULONG]
    size = wintypes.DWORD(0)
    fn(None, ctypes.byref(size), False, _AF_INET, _TCP_TABLE_OWNER_PID_ALL, 0)
    # Таблица может вырасти между вызовами — несколько попыток с запасом
    for _ in range(5):
        buf = ctypes.create_string_buffer(size.value + 4096)
        size = wintypes.DWORD(len(buf))
        ret = fn(buf, ctypes.byref(size), False, _AF_INET, _TCP_TABLE_OWNER_PID_ALL, 0)
        if ret == 0:
            break
        if ret != _ERROR_INSUFFICIENT_BUFFER:
            raise OSError(f"GetExtendedTcpTable: код {ret}")
    else:
        raise OSError("GetExtendedTcpTable: таблица не помещается в буфер")

    count = wintypes.DWORD.from_buffer(buf).value
    rows = (_Row * count).from_buffer(buf, ctypes.sizeof(wintypes.DWORD))
    return [
        TcpConn(r.state, (_addr(r.local_addr), _port(r.local_port)),
                (_addr(r.remote_addr), _port(r.remote_port)), r.pid)
        for r in rows
    ]


def listener_pid(port: int) -> Optional[int]:
    """PID процесса, который слушает этот порт (на любом адресе), или None."""
    for conn in connections():
        if conn.state == LISTEN and conn.local[1] == port:
            return conn.pid
    return None
