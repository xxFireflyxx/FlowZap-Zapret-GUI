"""
core/updater.py
---------------
Проверка и загрузка обновлений FlowZap.
"""
import logging
import threading
import shutil
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger(__name__)

GUI_VERSION = "0.1.1"
FLOWZAP_REPO = "xxFireflyxx/Flowzap-gui-zapret-dns-tgwsproxy"


def get_latest_release(repo: str = FLOWZAP_REPO) -> Optional[dict]:
    """Вернуть информацию о последнем релизе или None при ошибке."""
    try:
        import urllib.request, json
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        req = urllib.request.Request(url, headers={"User-Agent": "FlowZap/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except Exception as e:
        logger.error(f"Ошибка проверки обновлений: {e}")
        return None


def find_exe_asset(release: dict) -> Optional[dict]:
    """Найти .exe файл в ассетах релиза."""
    for asset in release.get("assets", []):
        if asset.get("name", "").lower().endswith(".exe"):
            return asset
    return None


def download_and_install_exe(
    install_dir: Path,
    repo: str = FLOWZAP_REPO,
    on_progress: Optional[Callable[[str], None]] = None,
    on_done: Optional[Callable[[bool, str], None]] = None,
) -> None:
    """
    Скачать новый FlowZap.exe из последнего релиза GitHub
    и заменить текущий exe. Не трогает zapret/ и config.toml.
    Запускается в отдельном потоке.
    """
    def _log(msg: str) -> None:
        logger.info(msg)
        if on_progress:
            on_progress(msg)

    def _worker() -> None:
        try:
            import urllib.request, json, sys

            _log("Получаем информацию о последнем релизе...")
            release = get_latest_release(repo)
            if not release:
                raise ValueError("Не удалось получить информацию о релизе")

            tag = release.get("tag_name", "unknown")
            asset = find_exe_asset(release)

            if not asset:
                raise ValueError(f"Exe-файл не найден в релизе {tag}")

            dl_url = asset["browser_download_url"]
            exe_name = asset["name"]
            _log(f"Скачиваем {exe_name} ({tag})...")

            req = urllib.request.Request(dl_url, headers={"User-Agent": "FlowZap/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()

            # Сохраняем новый exe рядом со старым
            current_exe = Path(sys.executable) if getattr(sys, 'frozen', False) else install_dir / "FlowZap.exe"
            new_exe = current_exe.parent / exe_name
            new_exe.write_bytes(data)

            _log(f"✓ FlowZap обновлён до {tag}. Перезапустите приложение.")
            if on_done:
                on_done(True, f"Обновлено до {tag}. Перезапустите приложение.")

        except Exception as exc:
            logger.error(f"Ошибка обновления: {exc}")
            _log(f"✗ Ошибка: {exc}")
            if on_done:
                on_done(False, str(exc))

    threading.Thread(target=_worker, daemon=True, name="flowzap-updater").start()


# ── Обратная совместимость с settings_tab ──────────────────────────────

def get_installed_core_version(zapret_dir: Path) -> Optional[str]:
    """Прочитать версию установленного Core из version.txt."""
    ver_file = zapret_dir / "version.txt"
    if ver_file.exists():
        try:
            return ver_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return None


def find_zip_asset(release: dict) -> Optional[dict]:
    """Найти zip архив zapret в ассетах релиза."""
    for asset in release.get("assets", []):
        name = asset.get("name", "").lower()
        if name.startswith("zapret") and name.endswith(".zip"):
            return asset
    return None


def download_and_install_core(
    zapret_dir: Path,
    repo: str = "Flowseal/zapret-discord-youtube",
    on_progress: Optional[Callable[[str], None]] = None,
    on_done: Optional[Callable[[bool, str], None]] = None,
) -> None:
    """
    Скачать последний релиз zapret-discord-youtube и распаковать в zapret_dir.
    Запускается в отдельном потоке.
    """
    def _log(msg: str) -> None:
        logger.info(msg)
        if on_progress:
            on_progress(msg)

    def _worker() -> None:
        try:
            import urllib.request, json, zipfile, io, tempfile, os

            _log("Получаем информацию о последнем релизе zapret...")
            release = get_latest_release(repo)
            if not release:
                raise ValueError("Не удалось получить информацию о релизе")

            tag = release.get("tag_name", "unknown")
            asset = find_zip_asset(release)
            if not asset:
                raise ValueError(f"Архив zapret не найден в релизе {tag}")

            dl_url = asset["browser_download_url"]
            zip_name = asset["name"]
            size_mb = asset.get("size", 0) / 1024 / 1024
            _log(f"Скачиваем {zip_name} ({tag}, {size_mb:.1f} МБ)...")

            req = urllib.request.Request(dl_url, headers={"User-Agent": "FlowZap/1.0"})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()

            _log("Распаковываем архив...")

            # Распаковываем во временную папку
            with tempfile.TemporaryDirectory() as tmp:
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    zf.extractall(tmp)

                # Находим корневую папку внутри архива
                entries = os.listdir(tmp)
                if len(entries) == 1 and os.path.isdir(os.path.join(tmp, entries[0])):
                    src = Path(tmp) / entries[0]
                else:
                    src = Path(tmp)

                # Копируем файлы в zapret_dir (перезаписываем)
                zapret_dir.mkdir(parents=True, exist_ok=True)
                for item in src.iterdir():
                    dst = zapret_dir / item.name
                    if item.is_dir():
                        if dst.exists():
                            shutil.rmtree(dst)
                        shutil.copytree(item, dst)
                    else:
                        shutil.copy2(item, dst)

                # Сохраняем версию
                ver_file = zapret_dir / "version.txt"
                ver_file.write_text(tag, encoding="utf-8")

            _log(f"✓ zapret обновлён до {tag}.")
            if on_done:
                on_done(True, f"zapret обновлён до {tag}")

        except Exception as exc:
            logger.error(f"Ошибка обновления Core: {exc}")
            _log(f"✗ Ошибка: {exc}")
            if on_done:
                on_done(False, str(exc))

    threading.Thread(target=_worker, daemon=True, name="core-updater").start()
