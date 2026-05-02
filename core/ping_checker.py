"""
core/ping_checker.py
--------------------
Статусы пресетов на основе HTTP/ping проверок через winws.exe.

Запускает winws.exe напрямую (без bat/cmd — нет иконок в панели задач).
Все хосты проверяются параллельно через ThreadPoolExecutor.
Пресеты — последовательно (WinDivert не допускает двух экземпляров).
"""

import os
import re
import threading
import subprocess
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Регулярка для строки аналитики в файле результатов
_ANALYTICS_RE = re.compile(
    r'^(?P<name>.+?)\s*:\s*HTTP OK:\s*(?P<ok>\d+),\s*ERR:\s*(?P<err>\d+),'
    r'\s*UNSUP:\s*(?P<unsup>\d+),\s*Ping OK:\s*(?P<ping_ok>\d+),\s*Fail:\s*(?P<fail>\d+)',
    re.MULTILINE,
)

# Хосты для проверки — HTTP и ping
# Хосты которые заблокированы в РФ — именно их zapret должен разблокировать
DEFAULT_HTTP_TARGETS = [
    "https://discord.com",
    "https://gateway.discord.gg",
    "https://cdn.discordapp.com",
    "https://updates.discord.com",
    "https://www.youtube.com",
    "https://youtu.be",
    "https://i.ytimg.com",
    "https://redirector.googlevideo.com",
]

DEFAULT_PING_TARGETS = [
    "1.1.1.1",
    "8.8.8.8",
    "8.8.4.4",
]

# Таймаут одной HTTP проверки (сек)
HTTP_TIMEOUT = 5
# Пауза после запуска winws перед проверками (сек)
WINWS_INIT_DELAY = 5
# Максимум параллельных HTTP проверок
MAX_WORKERS = 10


class PingStatus(Enum):
    UNKNOWN  = auto()   # нет данных
    CHECKING = auto()   # идёт проверка
    OK       = auto()   # всё работает
    WARN     = auto()   # частично работает
    FAIL     = auto()   # не работает


def _classify(ok: int, err: int, fail: int) -> PingStatus:
    if err == 0 and fail == 0:
        return PingStatus.OK
    if err > 0 and ok > 0 and err < ok:
        return PingStatus.WARN
    return PingStatus.FAIL


def parse_results_file(path: Path) -> dict[str, PingStatus]:
    """Распарсить файл test_results_*.txt."""
    results: dict[str, PingStatus] = {}
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        for m in _ANALYTICS_RE.finditer(text):
            name = re.sub(r'\.bat$', '', m.group("name").strip(), flags=re.IGNORECASE).strip()
            results[name] = _classify(int(m.group("ok")), int(m.group("err")), int(m.group("fail")))
    except Exception as e:
        logger.error(f"Ошибка парсинга {path}: {e}")
    return results


def find_latest_results(results_dir: Path) -> Optional[Path]:
    """Найти самый свежий файл test_results_*.txt."""
    files = sorted(
        results_dir.glob("test_results_*.txt"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    return files[0] if files else None


# ─────────────────────────────────────────────
#  Проверка одного хоста
# ─────────────────────────────────────────────

def _check_http(url: str, timeout: int = HTTP_TIMEOUT) -> str:
    """
    Проверить HTTP доступность URL через curl.exe — он корректно работает через WinDivert/winws.
    Возвращает 'OK', 'ERR' или 'UNSUP'.
    """
    try:
        result = subprocess.run(
            [
                "curl.exe",
                "-I", "-s",
                "-m", str(timeout),
                "-o", "NUL",
                "-w", "%{http_code}",
                "--http1.1",
                "--show-error",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 2,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        code = result.stdout.strip()
        stderr = result.stderr.strip().lower()

        # Проверяем unsupported (TLS/protocol issues)
        if result.returncode == 35 or any(x in stderr for x in (
            "does not support", "not supported", "unsupported protocol",
            "tls", "ssl", "schannel", "unrecognized option",
        )):
            return "UNSUP"

        if result.returncode == 0 and code.isdigit():
            return "OK" if int(code) < 500 else "ERR"

        return "ERR"
    except FileNotFoundError:
        # curl.exe не найден — fallback на urllib
        try:
            import urllib.request, urllib.error
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return "OK" if resp.getcode() < 500 else "ERR"
        except urllib.error.HTTPError as e:
            return "OK" if e.code < 500 else "ERR"
        except Exception:
            return "ERR"
    except Exception:
        return "ERR"


def _check_ping(host: str, timeout: int = HTTP_TIMEOUT) -> bool:
    """Проверить ping до хоста."""
    try:
        result = subprocess.run(
            ["ping", "-n", "1", "-w", str(timeout * 1000), host],
            capture_output=True,
            timeout=timeout + 2,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return result.returncode == 0
    except Exception:
        return False


def _run_checks_parallel(
    http_targets: list[str],
    ping_targets: list[str],
) -> tuple[int, int, int, int]:
    """
    Параллельно проверить все хосты.
    Возвращает (http_ok, http_err, ping_ok, ping_fail).
    """
    http_ok = http_err = ping_ok = ping_fail = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # HTTP задачи
        http_futures = {executor.submit(_check_http, url): url for url in http_targets}
        # Ping задачи
        ping_futures = {executor.submit(_check_ping, host): host for host in ping_targets}

        for future in as_completed(http_futures):
            result = future.result()
            if result == "OK":
                http_ok += 1
            else:
                http_err += 1

        for future in as_completed(ping_futures):
            if future.result():
                ping_ok += 1
            else:
                ping_fail += 1

    return http_ok, http_err, ping_ok, ping_fail


# ─────────────────────────────────────────────
#  Менеджер пресетов
# ─────────────────────────────────────────────

class PresetPingManager:
    """
    Управляет статусами пинга для всех пресетов.

    Два режима:
    - load_cached()  — мгновенно загружает из последнего файла результатов
    - run_tests()    — запускает winws.exe напрямую (без окон), параллельные проверки
    """

    def __init__(
        self,
        zapret_dir: Path,
        on_update: Optional[Callable[[str, PingStatus], None]] = None,
        on_tests_done: Optional[Callable[[bool, str], None]] = None,
    ) -> None:
        self._zapret_dir   = zapret_dir
        self._winws_exe    = zapret_dir / "bin" / "winws.exe"
        self._results_dir  = zapret_dir / "utils" / "test results"
        self._on_update    = on_update
        self._on_tests_done = on_tests_done
        self._statuses: dict[str, PingStatus] = {}
        self._testing = False
        self._stop_event = threading.Event()

    def get_status(self, preset_name: str) -> PingStatus:
        return self._statuses.get(preset_name, PingStatus.UNKNOWN)

    @property
    def is_testing(self) -> bool:
        return self._testing

    def load_cached(self) -> bool:
        """Загрузить результаты из последнего файла test_results_*.txt."""
        latest = find_latest_results(self._results_dir)
        if not latest:
            logger.debug("Нет сохранённых результатов тестов")
            return False

        results = parse_results_file(latest)
        if not results:
            return False

        self._statuses.update(results)
        logger.info(f"Загружены результаты из {latest.name} ({len(results)} пресетов)")

        if self._on_update:
            for name, status in results.items():
                self._on_update(name, status)
        return True

    def run_tests(self, presets: list[dict] | None = None) -> None:
        """
        Запустить тесты всех пресетов в фоновом потоке.
        presets — список из bat_parser.list_presets(). Если None — загружает сам.
        """
        if self._testing:
            logger.warning("Тесты уже запущены")
            return

        if not self._winws_exe.exists():
            msg = f"winws.exe не найден: {self._winws_exe}"
            logger.error(msg)
            if self._on_tests_done:
                self._on_tests_done(False, msg)
            return

        self._testing = True
        self._stop_event.clear()

        threading.Thread(
            target=self._worker,
            args=(presets,),
            daemon=True,
            name="preset-tester",
        ).start()

    def stop_tests(self) -> None:
        """Прервать текущее тестирование."""
        self._stop_event.set()

    def _worker(self, presets: list[dict] | None) -> None:
        from core.bat_parser import list_presets

        try:
            if presets is None:
                presets = list_presets(self._zapret_dir)

            if not presets:
                if self._on_tests_done:
                    self._on_tests_done(False, "Пресеты не найдены")
                return

            logger.info(f"Начинаем тесты: {len(presets)} пресетов")
            analytics: dict[str, dict] = {}
            start_time = time.time()

            for i, preset in enumerate(presets):
                if self._stop_event.is_set():
                    logger.info("Тесты прерваны пользователем")
                    break

                name = preset["name"]
                args = preset.get("args", [])

                logger.info(f"[{i+1}/{len(presets)}] Тестируем: {name}")

                # Уведомить UI что пресет проверяется
                self._statuses[name] = PingStatus.CHECKING
                if self._on_update:
                    self._on_update(name, PingStatus.CHECKING)

                # Запустить winws.exe напрямую — без bat, без cmd, без окон
                proc = self._start_winws(args)
                if proc is None:
                    logger.warning(f"Не удалось запустить winws для {name}")
                    self._statuses[name] = PingStatus.FAIL
                    if self._on_update:
                        self._on_update(name, PingStatus.FAIL)
                    analytics[name] = {"ok": 0, "err": 1, "unsup": 0, "ping_ok": 0, "fail": 1}
                    continue

                try:
                    # Ждём инициализации WinDivert
                    time.sleep(WINWS_INIT_DELAY)

                    if self._stop_event.is_set():
                        break

                    # Параллельные проверки всех хостов
                    http_ok, http_err, ping_ok, ping_fail = _run_checks_parallel(
                        DEFAULT_HTTP_TARGETS,
                        DEFAULT_PING_TARGETS,
                    )

                    analytics[name] = {
                        "ok": http_ok, "err": http_err,
                        "unsup": 0,
                        "ping_ok": ping_ok, "fail": ping_fail,
                    }

                    status = _classify(http_ok, http_err, ping_fail)
                    self._statuses[name] = status
                    if self._on_update:
                        self._on_update(name, status)

                    logger.info(
                        f"  {name}: HTTP OK={http_ok} ERR={http_err} "
                        f"Ping OK={ping_ok} Fail={ping_fail} → {status.name}"
                    )

                finally:
                    # Останавливаем winws перед следующим пресетом
                    self._stop_winws(proc)

            # Сохраняем результаты в файл
            if analytics:
                self._save_results(analytics)

            elapsed = time.time() - start_time
            count = len(analytics)
            msg = f"Готово. {count} пресетов проверено за {elapsed:.0f} сек"
            logger.info(msg)
            if self._on_tests_done:
                self._on_tests_done(True, msg)

        except Exception as e:
            logger.error(f"Ошибка тестирования: {e}", exc_info=True)
            if self._on_tests_done:
                self._on_tests_done(False, str(e))
        finally:
            self._testing = False
            self._kill_winws()  # Гарантированно убиваем winws

    def _start_winws(self, args: list[str]) -> Optional[subprocess.Popen]:
        """Запустить winws.exe напрямую без окон."""
        try:
            cmd = [str(self._winws_exe)] + args
            logger.debug(f"winws cmd ({len(cmd)} args): {cmd[0]} {' '.join(cmd[1:3])}...")
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self._winws_exe.parent),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            # Даём 1 сек и проверяем не упал ли сразу
            import time as _t
            _t.sleep(1)
            if proc.poll() is not None:
                stdout = proc.stdout.read().decode("utf-8", errors="replace").strip()
                stderr = proc.stderr.read().decode("utf-8", errors="replace").strip()
                logger.error(f"winws завершился сразу (код {proc.returncode}): {stdout or stderr}")
                return None
            logger.debug(f"winws запущен PID={proc.pid}")
            return proc
        except Exception as e:
            logger.error(f"Ошибка запуска winws: {e}")
            return None

    def _stop_winws(self, proc: subprocess.Popen) -> None:
        """Остановить winws и подождать завершения."""
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        except Exception:
            pass
        # Дополнительно убиваем все winws.exe процессы
        self._kill_winws()

    def _kill_winws(self) -> None:
        """Принудительно завершить все winws.exe процессы."""
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "winws.exe"],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except Exception:
            pass

    def _save_results(self, analytics: dict) -> None:
        """Сохранить результаты в файл test_results_*.txt."""
        try:
            self._results_dir.mkdir(parents=True, exist_ok=True)
            from datetime import datetime
            date_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            result_file = self._results_dir / f"test_results_{date_str}.txt"

            lines = ["=== ANALYTICS ==="]
            for name, a in analytics.items():
                lines.append(
                    f"{name}.bat : HTTP OK: {a['ok']}, ERR: {a['err']}, "
                    f"UNSUP: {a['unsup']}, Ping OK: {a['ping_ok']}, Fail: {a['fail']}"
                )

            result_file.write_text("\n".join(lines), encoding="utf-8")
            logger.info(f"Результаты сохранены: {result_file.name}")
        except Exception as e:
            logger.error(f"Ошибка сохранения результатов: {e}")
