"""
core/tgproxy/host.py
--------------------
Сервер TG WS Proxy в отдельном скрытом процессе FlowZap — без окна, значка
в трее и своих проверок обновлений:

    FlowZap.exe --tgproxy --src <папка> --log <файл> --parent <PID> -- <параметры сервера>
    FlowZap.exe --tgproxy-probe --src <папка> --out <файл.json>

(из исходников — python main.py с теми же параметрами). main.py передаёт
управление сюда до импорта PySide6: этому процессу Qt не нужен.

Код сервера — папки proxy/ и utils/ из исходников Flowseal/tg-ws-proxy
(лицензия MIT), их ставит core/updates/tgproxy.py в tgproxy/src. Отсюда
вызывается только то, что автор считает внешним интерфейсом, —
proxy.tg_ws_proxy.main() и его параметры командной строки: внутренности
сервера от версии к версии меняются.

--parent — PID FlowZap: когда он завершается (в том числе аварийно), сервер
выходит следом и не держит порт.
--tgproxy-probe — пробный запуск новой версии перед установкой: импортирует
сервер (заодно проверяет, что все нужные ему библиотеки есть в FlowZap) и
записывает, какие параметры командной строки он знает.
"""

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000

SERVE_FLAG = "--tgproxy"
PROBE_FLAG = "--tgproxy-probe"


def self_command() -> list[str]:
    """Как запустить FlowZap ещё раз: exe сборки или python main.py."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, str(Path(__file__).resolve().parents[2] / "main.py")]


# ── Сторона FlowZap ──────────────────────────────────────────────────────

def serve_command(src: Path, log_file: Path, args: list[str]) -> list[str]:
    return self_command() + [SERVE_FLAG, "--src", str(src), "--log", str(log_file),
                             "--parent", str(os.getpid()), "--", *args]


def probe(src: Path, timeout: float = 60) -> dict:
    """Пробный запуск сервера из src в отдельном процессе. {"version",
    "options"} или {"error": текст}. Синхронный — вызывать из фонового потока."""
    out = src.parent / f"{src.name}.probe.json"
    try:
        out.unlink(missing_ok=True)
        subprocess.run(
            self_command() + [PROBE_FLAG, "--src", str(src), "--out", str(out)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW, timeout=timeout,
        )
        result = json.loads(out.read_text(encoding="utf-8"))
    except subprocess.TimeoutExpired:
        return {"error": f"пробный запуск не уложился в {timeout:.0f} с"}
    except (OSError, ValueError) as exc:
        return {"error": f"пробный запуск не дал ответа ({exc})"}
    finally:
        try:
            out.unlink(missing_ok=True)
        except OSError:
            pass
    if not isinstance(result, dict) or (not result.get("options") and not result.get("error")):
        return {"error": "пробный запуск вернул непонятный ответ"}
    return result


# ── Сторона скрытого процесса ────────────────────────────────────────────

def run(argv: list[str]) -> int:
    """Точка входа скрытого процесса; argv — sys.argv[1:]."""
    mode, own, rest = argv[0], {}, []
    i = 1
    while i < len(argv):
        if argv[i] == "--":
            rest = argv[i + 1:]
            break
        own[argv[i]] = argv[i + 1] if i + 1 < len(argv) else ""
        i += 2

    # Вывода у процесса нет; main.py подменил пустые потоки буфером в памяти —
    # сервер писал бы туда свой лог всё время работы. Пусть уходит в никуда.
    sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")

    src = own.get("--src", "")
    if not src or not (Path(src) / "proxy").is_dir():
        return 2
    sys.path.insert(0, src)

    if mode == PROBE_FLAG:
        return _probe(Path(own.get("--out", "")))
    return _serve(Path(own.get("--log", "")), int(own.get("--parent") or 0), rest)


def _watch_parent(pid: int) -> None:
    """Завершиться, как только завершится FlowZap."""
    if not pid:
        return
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    handle = k32.OpenProcess(0x00100000, False, pid)        # SYNCHRONIZE
    if not handle:
        os._exit(0)                                         # FlowZap уже закрылся

    def _wait() -> None:
        k32.WaitForSingleObject(handle, 0xFFFFFFFF)
        os._exit(0)

    threading.Thread(target=_wait, daemon=True, name="parent-watch").start()


def _secret_from(args: list[str]) -> str:
    for arg in args:
        if arg.startswith("--secret="):
            return arg.split("=", 1)[1]
    return ""


def _serve(log_file: Path, parent_pid: int, args: list[str]) -> int:
    import logging

    _watch_parent(parent_pid)

    log_file.parent.mkdir(parents=True, exist_ok=True)
    from core.system.logfiles import ArchivedLogHandler
    handler = ArchivedLogHandler(log_file, max_bytes=2 * 1024 * 1024, backups=1)   # копия — в logs/archive/
    handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-5s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    secret = _secret_from(args)

    class _HideSecret(logging.Filter):
        """Секрет в логе — как пароль: лог могут прислать, разбираясь с ошибкой."""

        def filter(self, record: logging.LogRecord) -> bool:
            if secret:
                text = record.getMessage()
                if secret in text:
                    record.msg, record.args = text.replace(secret, secret[:4] + "…"), ()
            return True

    handler.addFilter(_HideSecret())
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    log = logging.getLogger("flowzap.tgproxy")

    try:
        import proxy
        from proxy import tg_ws_proxy
    except BaseException:
        log.exception("Не удалось загрузить сервер TG WS Proxy")
        return 3
    # Как у автора: домены Cloudflare в логе частично скрыты
    censor = getattr(tg_ws_proxy, "DomainCensorFilter", None)
    if censor is not None:
        try:
            handler.addFilter(censor())
        except Exception:
            pass
    log.info(f"FlowZap запускает TG WS Proxy {getattr(proxy, '__version__', '?')}")

    sys.argv = ["tg-ws-proxy", *args]
    try:
        tg_ws_proxy.main()
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    except BaseException:
        log.exception("TG WS Proxy завершился с ошибкой")
        return 1
    return 0


def _probe(out: Path) -> int:
    import argparse

    result: dict = {}

    class _Stop(Exception):
        pass

    def _capture(parser, *_args, **_kwargs):
        result["options"] = sorted(parser._option_string_actions)
        raise _Stop

    try:
        import proxy
        from proxy import tg_ws_proxy
        result["version"] = getattr(proxy, "__version__", "")
        argparse.ArgumentParser.parse_args = _capture
        sys.argv = ["tg-ws-proxy"]
        try:
            tg_ws_proxy.main()
        except _Stop:
            pass
        if "options" not in result:
            result["error"] = "сервер не разбирает параметры командной строки"
    except BaseException as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    try:
        out.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return 4
    return 0 if "options" in result else 1
