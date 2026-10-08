"""
core/system/idle.py
-------------------
Сколько секунд пользователь не трогал мышь и клавиатуру (GetLastInputInfo).
"""
import ctypes
from ctypes import wintypes


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def get_idle_seconds() -> float:
    """Return seconds since the last user input (keyboard/mouse) on Windows."""
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    kernel32 = ctypes.windll.kernel32
    kernel32.GetTickCount.restype = wintypes.DWORD
    elapsed_ms = (kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF
    return elapsed_ms / 1000.0
