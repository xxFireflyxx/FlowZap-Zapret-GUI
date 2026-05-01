"""
core/ping_checker.py
--------------------
Статусы пресетов на основе результатов тестов zapret.

Логика статусов (из строки ANALYTICS):
  UNSUP не считается ошибкой — это просто "не поддерживается"
  
  🟢 OK    — ERR == 0 и Fail == 0
  🟡 WARN  — ERR > 0 но ERR < HTTP_OK  (частично работает)
  🔴 FAIL  — ERR >= HTTP_OK или (ERR > 0 и HTTP_OK == 0)

Источники данных (в порядке приоритета):
  1. Последний файл test_results_*.txt в папке utils/test results/
  2. Запуск ps1 скрипта (занимает несколько минут)
"""

import re
import threading
import subprocess
import time
import logging
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Регулярка для строки аналитики:
# "general (ALT2).bat : HTTP OK: 8, ERR: 15, UNSUP: 11, Ping OK: 16, Fail: 1"
_ANALYTICS_RE = re.compile(
    r'^(?P<name>.+?)\s*:\s*HTTP OK:\s*(?P<ok>\d+),\s*ERR:\s*(?P<err>\d+),'
    r'\s*UNSUP:\s*(?P<unsup>\d+),\s*Ping OK:\s*(?P<ping_ok>\d+),\s*Fail:\s*(?P<fail>\d+)',
    re.MULTILINE,
)


class PingStatus(Enum):
    UNKNOWN  = auto()   # нет данных
    CHECKING = auto()   # идёт проверка
    OK       = auto()   # всё работает
    WARN     = auto()   # частично работает
    FAIL     = auto()   # не работает


def _classify(ok: int, err: int, fail: int) -> PingStatus:
    """Определить статус по числам из строки ANALYTICS."""
    if err == 0 and fail == 0:
        return PingStatus.OK
    if err > 0 and ok > 0 and err < ok:
        return PingStatus.WARN
    return PingStatus.FAIL


def parse_results_file(path: Path) -> dict[str, PingStatus]:
    """
    Распарсить файл test_results_*.txt и вернуть словарь
    {имя_пресета_без_.bat: PingStatus}.
    """
    results: dict[str, PingStatus] = {}
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        for m in _ANALYTICS_RE.finditer(text):
            name_raw = m.group("name").strip()
            # Убираем .bat из имени если есть
            name = re.sub(r'\.bat$', '', name_raw, flags=re.IGNORECASE).strip()
            ok   = int(m.group("ok"))
            err  = int(m.group("err"))
            fail = int(m.group("fail"))
            results[name] = _classify(ok, err, fail)
    except Exception as e:
        logger.error(f"Ошибка парсинга {path}: {e}")
    return results


def find_latest_results(results_dir: Path) -> Optional[Path]:
    """Найти самый свежий файл test_results_*.txt."""
    files = sorted(results_dir.glob("test_results_*.txt"), key=lambda f: f.stat().st_mtime, reverse=True)
    return files[0] if files else None


class PresetPingManager:
    """
    Управляет статусами пинга для всех пресетов.

    Два режима:
    - load_cached()  — мгновенно загружает из последнего файла результатов
    - run_tests()    — запускает ps1 скрипт (долго, ~минуты), обновляет по завершении
    """

    def __init__(
        self,
        zapret_dir: Path,
        on_update: Optional[Callable[[str, PingStatus], None]] = None,
        on_tests_done: Optional[Callable[[bool, str], None]] = None,
    ) -> None:
        self._zapret_dir   = zapret_dir
        self._results_dir  = zapret_dir / "utils" / "test results"
        self._ps1_script   = zapret_dir / "utils" / "test zapret.ps1"
        self._on_update    = on_update
        self._on_tests_done = on_tests_done
        self._statuses: dict[str, PingStatus] = {}
        self._testing = False

    def get_status(self, preset_name: str) -> PingStatus:
        return self._statuses.get(preset_name, PingStatus.UNKNOWN)

    @property
    def is_testing(self) -> bool:
        return self._testing

    def load_cached(self) -> bool:
        """
        Загрузить результаты из последнего файла test_results_*.txt.
        Возвращает True если файл найден и распарсен.
        """
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

    def run_tests(self) -> None:
        """
        Запустить ps1 скрипт тестирования в фоновом потоке.
        По завершении вызывает on_tests_done и on_update для каждого пресета.
        """
        if self._testing:
            logger.warning("Тесты уже запущены")
            return
        if not self._ps1_script.exists():
            msg = f"Скрипт не найден: {self._ps1_script}"
            logger.error(msg)
            if self._on_tests_done:
                self._on_tests_done(False, msg)
            return

        self._testing = True
        threading.Thread(target=self._worker, daemon=True, name="zapret-tests").start()

    def _worker(self) -> None:
        try:
            logger.info("Запускаем тест zapret.ps1...")
            start_time = time.time()

            # Передаём ответы на интерактивные вопросы ps1 через stdin:
            # "1" → тип теста standard, "1" → все конфиги (All configs)
            stdin_input = "1\n1\n"

            cmd = [
                "powershell.exe",
                "-ExecutionPolicy", "Bypass",
                "-File", str(self._ps1_script),
            ]

            CREATE_NO_WINDOW = 0x08000000
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(self._ps1_script.parent),
                creationflags=CREATE_NO_WINDOW,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            logger.info("ps1 запущен (stdin: auto-select all), ждём завершения…")

            try:
                stdout, _ = proc.communicate(input=stdin_input, timeout=600)
            except subprocess.TimeoutExpired:
                proc.kill()
                raise RuntimeError("Тест завершился по таймауту (>10 мин)")

            logger.debug(f"ps1 завершился с кодом {proc.returncode}")

            # Ищем новый файл результатов
            new_file = self._find_new_results(start_time)
            if not new_file:
                # Пробуем распарсить stdout напрямую
                inline = self._parse_stdout(stdout)
                if inline:
                    self._statuses.update(inline)
                    if self._on_update:
                        for name, status in inline.items():
                            self._on_update(name, status)
                    if self._on_tests_done:
                        self._on_tests_done(True, f"Готово (из вывода). {len(inline)} пресетов")
                    self._testing = False
                    return
                # Нет результатов и нет stdout — сообщаем об ошибке
                if self._on_tests_done:
                    self._on_tests_done(False,
                        f"ps1 завершился с кодом {proc.returncode}. "
                        "Убедитесь что FlowZap запущен от имени администратора.")
                self._testing = False
                return

            # Ждём новый файл результатов — проверяем каждые 5 секунд
            for _ in range(120):
                if new_file:
                    break
                time.sleep(5)
                new_file = self._find_new_results(start_time)
            if new_file:
                logger.info(f"Найден файл результатов: {new_file.name}")

            if new_file:
                results = parse_results_file(new_file)
                self._statuses.update(results)
                if self._on_update:
                    for name, status in results.items():
                        self._on_update(name, status)
                if self._on_tests_done:
                    self._on_tests_done(True, f"Готово. {len(results)} пресетов проверено")
            else:
                if self._on_tests_done:
                    self._on_tests_done(False, "Файл результатов не появился (тест не завершён?)")
        except Exception as e:
            logger.error(f"Ошибка запуска тестов: {e}")
            if self._on_tests_done:
                self._on_tests_done(False, str(e))
        finally:
            self._testing = False

    def _find_new_results(self, after_timestamp: float) -> Optional[Path]:
        """Найти файл результатов созданный после указанного времени."""
        if not self._results_dir.exists():
            return None
        candidates = [
            f for f in self._results_dir.glob("test_results_*.txt")
            if f.stat().st_mtime >= after_timestamp - 5
        ]
        return max(candidates, key=lambda f: f.stat().st_mtime) if candidates else None

    def _parse_stdout(self, stdout: str) -> dict[str, PingStatus]:
        """Парсим вывод ps1 напрямую если файл не нашёлся."""
        results: dict[str, PingStatus] = {}
        for m in _ANALYTICS_RE.finditer(stdout):
            name = re.sub(r'\.bat$', '', m.group("name").strip(), flags=re.IGNORECASE).strip()
            results[name] = _classify(int(m.group("ok")), int(m.group("err")), int(m.group("fail")))
        return results
