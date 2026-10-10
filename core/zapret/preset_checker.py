"""
core/zapret/preset_checker.py
-----------------------------
Статусы пресетов на основе HTTP-проверок через winws.exe.

Запускает winws.exe напрямую (без bat/cmd — нет иконок в панели задач).
Проверки начинаются, как только winws сообщит «capture is started» (а не
по фиксированной паузе). Все сайты проверяются параллельно через
ThreadPoolExecutor, пресеты — последовательно (WinDivert не допускает двух
экземпляров). Ping в оценке не участвует: zapret не трогает ICMP, а
потерянный ping делал красным рабочий пресет.
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

from core.zapret import runner
from core.zapret.runner import WinwsHandle
from core.service.client import ServiceError
from core.zapret.winws import build_winws_cmd

logger = logging.getLogger(__name__)

# Регулярка для строки аналитики в файле результатов
_ANALYTICS_RE = re.compile(
    r'^(?P<name>.+?)\s*:\s*HTTP OK:\s*(?P<ok>\d+),\s*ERR:\s*(?P<err>\d+),'
    r'\s*UNSUP:\s*(?P<unsup>\d+),\s*Ping OK:\s*(?P<ping_ok>\d+),\s*Fail:\s*(?P<fail>\d+)'
    r'(?:,\s*RETRY:(?P<retry>\d))?'
    r'(?:,\s*TIME:\s*(?P<ms>\d+))?'
    r'(?:,\s*SERVICES:\s*(?P<services>[^\r\n]*))?',
    re.MULTILINE,
)
_SERVICE_RE = re.compile(r'([^=;]+)=(\d+)/(\d+)')

# Что проверяем — по сервисам, как в utils/targets.txt Flowseal (его и
# читаем, см. load_targets; этот список — если файла нет). Итог по каждому
# сервису отдельно: «5 из 8» не говорило, что именно не работает.
DEFAULT_SERVICES: list[tuple[str, list[str]]] = [
    ("Discord", ["https://discord.com", "https://gateway.discord.gg",
                 "https://cdn.discordapp.com", "https://updates.discord.com"]),
    ("YouTube", ["https://www.youtube.com", "https://youtu.be",
                 "https://i.ytimg.com", "https://redirector.googlevideo.com"]),
    ("Google", ["https://www.google.com", "https://www.gstatic.com"]),
    ("Cloudflare", ["https://www.cloudflare.com", "https://cdnjs.cloudflare.com"]),
]
# Ради них FlowZap и нужен — они важнее при выборе лучшего пресета
MAIN_SERVICES = ("Discord", "YouTube")
_TARGET_LINE_RE = re.compile(r'^\s*\w+\s*=\s*"(https://[^"\s]+)"')


def load_targets(zapret_dir: Path) -> list[tuple[str, list[str]]]:
    """Сервисы и их адреса из utils/targets.txt Flowseal (секции «### Имя»,
    строки «Ключ = "https://…"»; PING-строки не нужны — zapret не трогает
    ICMP). Нет файла или в нём нечего проверять — DEFAULT_SERVICES."""
    path = zapret_dir / "utils" / "targets.txt"
    services: list[tuple[str, list[str]]] = []
    try:
        current = None
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            if line.startswith("###"):
                current = (line.strip("# \t") or "Прочее", [])
                services.append(current)
            elif (m := _TARGET_LINE_RE.match(line)):
                if current is None:
                    current = ("Прочее", [])
                    services.append(current)
                current[1].append(m.group(1))
    except OSError:
        return DEFAULT_SERVICES
    services = [s for s in services if s[1]]
    return services or DEFAULT_SERVICES

# Таймаут одной HTTP проверки (сек)
HTTP_TIMEOUT = 4
# Сколько ждать от winws «capture is started» (сек). Первый пресет — дольше:
# после перезагрузки драйвер WinDivert грузится в ядро. Не дождались — всё
# равно проверяем (раньше была фиксированная пауза 7–9 с на каждый пресет).
WINWS_READY_TIMEOUT_FIRST = 7
WINWS_READY_TIMEOUT = 4
# После сигнала готовности — короткая пауза, чтобы фильтр точно применился
WINWS_SETTLE = 0.5
WINWS_READY_MARK = "capture is started"
# Максимум параллельных HTTP проверок (все адреса пресета разом)
MAX_WORKERS = 16


class PingStatus(Enum):
    UNKNOWN  = auto()   # нет данных
    CHECKING = auto()   # идёт проверка
    OK       = auto()   # всё работает
    WARN     = auto()   # частично работает
    FAIL     = auto()   # не работает


def _classify(ok: int, err: int, needed_retry: bool = False) -> PingStatus:
    """
    Оценка пресета только по HTTP:
      🟢 OK   — все сайты открылись
      🟡 WARN — открылось больше половины; или ничего не проверено; или была повторная попытка
      🔴 FAIL — открылось половина или меньше
    Ping (колонки «Ping OK / Fail» в старых файлах результатов) не учитывается.
    """
    if err == 0:
        if ok == 0:
            return PingStatus.WARN      # ничего не проверено — непонятно, работает ли
        return PingStatus.WARN if needed_retry else PingStatus.OK
    if ok > err:
        return PingStatus.WARN
    return PingStatus.FAIL


Services = dict[str, tuple[int, int]]      # {сервис: (открылось, проверялось)}


def format_services(services: Services) -> str:
    return ";".join(f"{name}={ok}/{total}" for name, (ok, total) in services.items())


def parse_services(text: Optional[str]) -> Optional[Services]:
    if not text:
        return None
    found = {m.group(1).strip(): (int(m.group(2)), int(m.group(3)))
             for m in _SERVICE_RE.finditer(text)}
    return found or None


def parse_results_details(path: Path) -> dict[str, tuple[PingStatus, int, int, Optional[int],
                                                         Optional[Services]]]:
    """Распарсить файл test_results_*.txt: {пресет: (статус, HTTP OK, HTTP всего,
    среднее время ответа в мс, итог по сервисам)}. В старых файлах времени
    и сервисов нет — None."""
    results = {}
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        for m in _ANALYTICS_RE.finditer(text):
            name = re.sub(r'\.bat$', '', m.group("name").strip(), flags=re.IGNORECASE).strip()
            retry = m.group("retry")
            needed_retry = retry == "1" if retry is not None else False
            ok, err = int(m.group("ok")), int(m.group("err"))
            status = _classify(ok, err, needed_retry=needed_retry)
            ms = int(m.group("ms")) if m.group("ms") else None
            results[name] = (status, ok, ok + err, ms, parse_services(m.group("services")))
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

def _check_http(url: str, timeout: int = HTTP_TIMEOUT) -> tuple[str, Optional[float], Optional[float]]:
    """
    Проверить HTTP доступность URL через curl.exe — он корректно работает через WinDivert/winws.
    Возвращает ('OK' | 'ERR' | 'UNSUP', время ответа в секундах или None,
    из него — поиск адреса (DNS) в секундах или None; для лога).
    Время — от начала соединения до ответа сервера (DNS + TCP + TLS + ответ):
    на нём и сказывается стратегия обхода.
    """
    try:
        result = subprocess.run(
            [
                "curl.exe",
                "-I", "-s",
                "-m", str(timeout),
                "-o", "NUL",
                "-w", "%{http_code} %{time_total} %{time_namelookup}",
                "--http1.1",
                "--show-error",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 2,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        code, took, dns = (result.stdout.strip().split(" ") + ["", "", ""])[:3]
        stderr = result.stderr.strip().lower()

        # Проверяем unsupported (TLS/protocol issues)
        if result.returncode == 35 or any(x in stderr for x in (
            "does not support", "not supported", "unsupported protocol",
            "tls", "ssl", "schannel", "unrecognized option",
        )):
            return "UNSUP", None, None

        if result.returncode == 0 and code.isdigit() and int(code) < 500:
            try:
                return "OK", float(took.replace(",", ".")), float(dns.replace(",", "."))
            except ValueError:
                return "OK", None, None
        return "ERR", None, None
    except FileNotFoundError:
        # curl.exe не найден — fallback на urllib
        import urllib.request, urllib.error
        started = time.monotonic()
        try:
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                code = resp.getcode()
        except urllib.error.HTTPError as e:
            code = e.code
        except Exception:
            return "ERR", None, None
        return ("OK", time.monotonic() - started, None) if code < 500 else ("ERR", None, None)
    except Exception:
        return "ERR", None, None


def _run_checks_parallel(targets: list[tuple[str, list[str]]]
                         ) -> tuple[int, int, Optional[int], Optional[int], Services]:
    """Параллельно проверить все адреса всех сервисов. Возвращает (открылось,
    не открылось, среднее время ответа открывшихся в мс или None, из него
    DNS в мс — для лога, итог по сервисам)."""
    http_ok = http_err = 0
    times: list[float] = []
    dns_times: list[float] = []
    services: Services = {name: (0, len(urls)) for name, urls in targets}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_check_http, url): name
                   for name, urls in targets for url in urls}
        for future in as_completed(futures):
            result, took, dns = future.result()
            if result == "OK":
                http_ok += 1
                ok, total = services[futures[future]]
                services[futures[future]] = (ok + 1, total)
                if took is not None:
                    times.append(took)
                if dns is not None:
                    dns_times.append(dns)
            else:
                http_err += 1
    avg_ms = round(sum(times) / len(times) * 1000) if times else None
    dns_ms = round(sum(dns_times) / len(dns_times) * 1000) if dns_times else None
    return http_ok, http_err, avg_ms, dns_ms, services


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
        self._counts: dict[str, tuple[int, int]] = {}   # (HTTP OK, HTTP всего)
        self._times: dict[str, int] = {}                 # среднее время ответа сайтов, мс
        self._services: dict[str, Services] = {}         # итог по сервисам
        self._checked_at: Optional[float] = None         # время последней проверки (unix)
        self._testing = False
        self._stop_event = threading.Event()

    def get_status(self, preset_name: str) -> PingStatus:
        return self._statuses.get(preset_name, PingStatus.UNKNOWN)

    def get_counts(self, preset_name: str) -> Optional[tuple[int, int]]:
        """(сколько сайтов открылось, сколько проверялось) или None, если не проверялся."""
        return self._counts.get(preset_name)

    def get_ms(self, preset_name: str) -> Optional[int]:
        """Среднее время ответа сайтов через пресет (мс) или None."""
        return self._times.get(preset_name)

    def get_services(self, preset_name: str) -> Optional[Services]:
        """{сервис: (открылось, проверялось)} или None — не проверялся или
        результат из старого файла (до проверки по сервисам)."""
        return self._services.get(preset_name)

    @property
    def checked_at(self) -> Optional[float]:
        return self._checked_at

    @property
    def is_testing(self) -> bool:
        return self._testing

    def load_cached(self) -> bool:
        """Загрузить результаты из последнего файла test_results_*.txt."""
        latest = find_latest_results(self._results_dir)
        if not latest:
            logger.debug("Нет сохранённых результатов тестов")
            return False

        details = parse_results_details(latest)
        if not details:
            return False

        results = {name: d[0] for name, d in details.items()}
        self._statuses.update(results)
        self._counts.update({name: (d[1], d[2]) for name, d in details.items()})
        self._times.update({name: d[3] for name, d in details.items() if d[3] is not None})
        self._services.update({name: d[4] for name, d in details.items() if d[4]})
        self._checked_at = latest.stat().st_mtime
        logger.info(f"Загружены результаты из {latest.name} ({len(results)} пресетов)")

        if self._on_update:
            for name, status in results.items():
                self._on_update(name, status)
        return True

    def run_tests(self, presets: list[dict] | None = None, allow_install: bool = False) -> None:
        """
        Запустить тесты всех пресетов в фоновом потоке.
        presets — список из presets.list_presets(). Если None — загружает сам.
        allow_install — проверку запустил пользователь: нет фоновой службы →
        предложить её установить (окно UAC). Фоновая проверка — без окна.
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
            args=(presets, allow_install),
            daemon=True,
            name="preset-tester",
        ).start()

    def stop_tests(self) -> None:
        """Прервать текущее тестирование."""
        self._stop_event.set()

    def stop_tests_and_wait(self, timeout: float = 10.0) -> None:
        """Синхронно прервать тесты и убедиться, что тестовый winws.exe убит —
        для выхода из приложения. stop_tests() лишь ставит флаг: воркер не
        убирает winws в finally своего daemon-потока, который умрёт вместе с
        процессом — тестовый winws остался бы висеть. Ждём воркера (паузы он
        ждёт через stop_event, так что выходит быстро — максимум пока
        доработают HTTP-проверки, ~6 с), чтобы он прибрался сам и не записал в
        кэш результат недотестированного пресета; по таймауту убиваем принудительно."""
        if not self._testing:
            return
        self._stop_event.set()
        deadline = time.monotonic() + timeout
        while self._testing and time.monotonic() < deadline:
            time.sleep(0.05)
        if self._testing:
            logger.warning(f"Тесты не завершились за {timeout} с — принудительно убиваю winws")
            runner.stop_all()

    def _worker(self, presets: list[dict] | None, allow_install: bool) -> None:
        from core.zapret.presets import list_presets

        try:
            runner.ensure_backend(self._winws_exe.parent, allow_install)
        except ServiceError as e:
            self._testing = False
            if self._on_tests_done:
                self._on_tests_done(False, str(e))
            return

        try:
            if presets is None:
                presets = list_presets(self._zapret_dir)

            if not presets:
                if self._on_tests_done:
                    self._on_tests_done(False, "Пресеты не найдены")
                return

            targets = load_targets(self._zapret_dir)
            total_urls = sum(len(urls) for _, urls in targets)
            logger.info(f"Начинаем тесты: {len(presets)} пресетов, "
                        + ", ".join(f"{name} ({len(urls)})" for name, urls in targets))
            analytics: dict[str, dict] = {}
            start_time = time.time()

            for i, preset in enumerate(presets):
                if self._stop_event.is_set():
                    logger.info("Тесты прерваны пользователем")
                    break

                name = preset["name"]
                # Та же команда, что у «Запустить» (игровые списки, текущий Game Filter)
                built = build_winws_cmd(Path(preset["path"]), self._winws_exe)

                logger.info(f"[{i+1}/{len(presets)}] Тестируем: {name}")

                # Запомнить старый статус — откатим к нему если winws не запустится
                prev_status = self._statuses.get(name, PingStatus.UNKNOWN)

                # Уведомить UI что пресет проверяется
                self._statuses[name] = PingStatus.CHECKING
                if self._on_update:
                    self._on_update(name, PingStatus.CHECKING)

                # Запустить winws (через службу или напрямую) и дождаться его
                # сигнала готовности
                proc = None if built is None else self._start_winws(
                    *built, WINWS_READY_TIMEOUT_FIRST if i == 0 else WINWS_READY_TIMEOUT)
                if self._stop_event.is_set():
                    if proc is not None:
                        self._stop_winws(proc)
                    self._statuses[name] = prev_status
                    if self._on_update:
                        self._on_update(name, prev_status)
                    logger.info("Тесты прерваны пользователем")
                    break
                if proc is None:
                    logger.warning(f"Не удалось запустить winws для {name}")
                    # Откатываем к предыдущему статусу — не затираем кэш ложным FAIL
                    rollback = prev_status if prev_status != PingStatus.UNKNOWN else PingStatus.FAIL
                    self._statuses[name] = rollback
                    if self._on_update:
                        self._on_update(name, rollback)
                    analytics[name] = {"ok": 0, "err": 1}
                    continue

                try:
                    http_ok, http_err, avg_ms, dns_ms, services = _run_checks_parallel(targets)
                    if self._stop_event.is_set():
                        # Прервали посреди проверки — результат неполный, не сохраняем
                        self._statuses[name] = prev_status
                        if self._on_update:
                            self._on_update(name, prev_status)
                        logger.info("Тесты прерваны пользователем")
                        break
                    if proc.poll() is not None:
                        # winws упал уже во время проверки — сайты открывались без обхода
                        logger.warning(f"  {name}: winws завершился во время проверки (код {proc.returncode})")
                        http_ok, http_err, avg_ms = 0, total_urls, None
                        services = {svc: (0, total) for svc, (_ok, total) in services.items()}

                    analytics[name] = {"ok": http_ok, "err": http_err, "ms": avg_ms, "services": services}

                    status = _classify(http_ok, http_err)
                    self._statuses[name] = status
                    self._counts[name] = (http_ok, http_ok + http_err)
                    self._services[name] = services
                    if avg_ms is None:
                        self._times.pop(name, None)
                    else:
                        self._times[name] = avg_ms
                    if self._on_update:
                        self._on_update(name, status)

                    logger.info(f"  {name}: {format_services(services)} "
                                f"время={avg_ms if avg_ms is not None else '—'} мс "
                                f"(DNS {dns_ms if dns_ms is not None else '—'} мс) → {status.name}")

                finally:
                    # Останавливаем winws перед следующим пресетом
                    self._stop_winws(proc)

            # Сохраняем результаты в файл
            if analytics:
                self._save_results(analytics)
                self._checked_at = time.time()

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
            runner.stop_all()  # Гарантированно убиваем winws

    def _start_winws(self, cmd: list[str], cwd: Path, ready_timeout: float) -> Optional[WinwsHandle]:
        """Запустить winws (core/zapret/runner) и дождаться строки «capture is
        started» (WinDivert захватывает трафик) — максимум ready_timeout
        секунд; не дождались — всё равно отдаём процесс, проверки покажут
        результат. None — winws не запустился или сразу упал. Паузы ждёт
        через stop_event — «Остановить» срабатывает сразу."""
        logger.debug(f"winws cmd ({len(cmd)} args): {cmd[0]} {' '.join(cmd[1:3])}...")
        try:
            proc = runner.launch(cmd, cwd)
        except (ServiceError, OSError) as e:
            logger.error(f"Ошибка запуска winws: {e}")
            return None

        started = time.monotonic()
        while (not proc.ready.is_set() and proc.poll() is None
               and time.monotonic() - started < ready_timeout):
            if self._stop_event.wait(0.1):
                return proc            # прервали — воркер сам остановит процесс
        if proc.poll() is not None:
            logger.error(f"winws завершился сразу (код {proc.returncode}): {' | '.join(proc.tail) or '—'}")
            return None
        if proc.ready.is_set():
            logger.debug(f"winws готов за {time.monotonic() - started:.1f} с (PID {proc.pid})")
            self._stop_event.wait(WINWS_SETTLE)
        else:
            logger.debug(f"winws не прислал «{WINWS_READY_MARK}» за {ready_timeout} с — проверяем так")
        return proc

    def _stop_winws(self, proc: WinwsHandle) -> None:
        """Остановить winws перед следующим пресетом."""
        proc.terminate()

    def _save_results(self, analytics: dict) -> None:
        """Сохранить результаты в файл test_results_*.txt."""
        try:
            self._results_dir.mkdir(parents=True, exist_ok=True)
            from datetime import datetime
            date_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            result_file = self._results_dir / f"test_results_{date_str}.txt"

            # Формат строки прежний (его же пишет test zapret.bat Flowseal) —
            # колонки UNSUP/Ping/RETRY больше не используются и пишутся нулями;
            # в конце — среднее время ответа (TIME, мс), если сайты открывались,
            # и итог по сервисам (SERVICES: Discord=4/4;YouTube=3/4;…).
            lines = ["=== ANALYTICS ==="]
            for name, a in analytics.items():
                tail = f", TIME: {a['ms']}" if a.get("ms") is not None else ""
                if a.get("services"):
                    tail += f", SERVICES: {format_services(a['services'])}"
                lines.append(
                    f"{name}.bat : HTTP OK: {a['ok']}, ERR: {a['err']}, "
                    f"UNSUP: 0, Ping OK: 0, Fail: 0, RETRY:0{tail}"
                )

            result_file.write_text("\n".join(lines), encoding="utf-8")
            logger.info(f"Результаты сохранены: {result_file.name}")
        except Exception as e:
            logger.error(f"Ошибка сохранения результатов: {e}")
