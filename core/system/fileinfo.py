"""
core/system/fileinfo.py
-----------------------
Версия из ресурсов exe (FileVersion).
"""
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def get_exe_version(exe_path: Path) -> Optional[str]:
    """
    Читает версию, зашитую в PE-ресурсы exe (FileVersion). Не зависит
    от того, откуда скачан файл — с GitHub или с зеркала — поэтому
    надёжнее сверки размера с API релиза.
    """
    import ctypes

    class _FixedFileInfo(ctypes.Structure):
        _fields_ = [
            ("dwSignature",        ctypes.c_uint32),
            ("dwStrucVersion",     ctypes.c_uint32),
            ("dwFileVersionMS",    ctypes.c_uint32),
            ("dwFileVersionLS",    ctypes.c_uint32),
            ("dwProductVersionMS", ctypes.c_uint32),
            ("dwProductVersionLS", ctypes.c_uint32),
        ]

    try:
        path = str(exe_path)
        size = ctypes.windll.version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        res = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(path, 0, size, res):
            return None
        r = ctypes.c_void_p()
        l = ctypes.c_uint()
        if not ctypes.windll.version.VerQueryValueW(res, "\\", ctypes.byref(r), ctypes.byref(l)):
            return None
        ffi = _FixedFileInfo.from_address(r.value)
        parts = [
            ffi.dwFileVersionMS >> 16, ffi.dwFileVersionMS & 0xFFFF,
            ffi.dwFileVersionLS >> 16, ffi.dwFileVersionLS & 0xFFFF,
        ]
        if parts[-1] == 0:
            parts = parts[:3]  # убираем нулевую 4-ю часть — как в теге на GitHub
        return ".".join(str(p) for p in parts)
    except Exception as e:
        logger.debug(f"Не удалось прочитать версию из exe: {e}")
        return None
