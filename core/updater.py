"""
core/updater.py
---------------
Проверка и загрузка обновлений для Core (Flowseal / zapret-discord-youtube).
"""
import logging
import threading
import zipfile
import io
import shutil
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger(__name__)

GUI_VERSION = "0.1.0"
FLOWSEAL_REPO = "Flowseal/zapret-discord-youtube"


def get_latest_release(repo: str = FLOWSEAL_REPO) -> Optional[str]:
    """Вернуть последний тег релиза или None при ошибке."""
    try:
        import urllib.request, json
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        req = urllib.request.Request(url, headers={"User-Agent": "FlowZap/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r)
            return data.get("tag_name")
    except Exception as e:
        logger.error(f"Ошибка проверки обновлений: {e}")
        return None


def get_installed_core_version(zapret_dir: Path) -> Optional[str]:
    """
    Прочитать версию установленного Core из version.txt в папке zapret.
    """
    ver_file = zapret_dir / "version.txt"
    if ver_file.exists():
        try:
            return ver_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return None


def download_and_install_core(
    zapret_dir: Path,
    repo: str = FLOWSEAL_REPO,
    on_progress: Optional[Callable[[str], None]] = None,
    on_done: Optional[Callable[[bool, str], None]] = None,
) -> None:
    """
    Скачать последний релиз Core с GitHub и установить в папку zapret_dir.
    Запускается в отдельном потоке — не блокирует UI.

    Параметры
    ---------
    zapret_dir : Path
        Папка назначения (обычно ROOT / "zapret").
    on_progress : Callable[[str], None]
        Коллбэк для строк прогресса.
    on_done : Callable[[bool, str], None]
        Вызывается по завершении: (success: bool, message: str).
    """
    def _log(msg: str) -> None:
        logger.info(msg)
        if on_progress:
            on_progress(msg)

    def _worker() -> None:
        try:
            import urllib.request, json

            _log("Получаем информацию о последнем релизе...")
            url = f"https://api.github.com/repos/{repo}/releases/latest"
            req = urllib.request.Request(url, headers={"User-Agent": "FlowZap/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                release = json.load(r)

            tag = release.get("tag_name", "unknown")
            assets = release.get("assets", [])

            # Ищем zip-архив с Core
            zip_asset = None
            for asset in assets:
                name = asset.get("name", "").lower()
                if name.endswith(".zip") and "zapret" in name:
                    zip_asset = asset
                    break
            # fallback — любой zip
            if not zip_asset:
                for asset in assets:
                    if asset.get("name", "").lower().endswith(".zip"):
                        zip_asset = asset
                        break

            if not zip_asset:
                raise ValueError(f"Zip-архив не найден в релизе {tag}")

            dl_url = zip_asset["browser_download_url"]
            _log(f"Скачиваем {zip_asset['name']} ({tag})...")

            req2 = urllib.request.Request(dl_url, headers={"User-Agent": "FlowZap/1.0"})
            with urllib.request.urlopen(req2, timeout=60) as r:
                data = r.read()

            _log("Распаковываем архив...")
            zapret_dir.mkdir(parents=True, exist_ok=True)

            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                # Пробуем найти bat-файлы и bin/ в архиве
                members = zf.namelist()
                # Определяем корневой prefix в архиве
                prefix = ""
                for m in members:
                    if "general.bat" in m or "/bin/winws.exe" in m.lower():
                        parts = m.split("/")
                        if len(parts) > 1:
                            prefix = parts[0] + "/"
                        break

                for member in members:
                    # Убираем prefix из пути
                    rel = member[len(prefix):] if prefix and member.startswith(prefix) else member
                    if not rel or rel.endswith("/"):
                        continue
                    target = zapret_dir / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)

            # Сохраняем версию
            (zapret_dir / "version.txt").write_text(tag, encoding="utf-8")
            _log(f"✓ Core обновлён до версии {tag}")
            if on_done:
                on_done(True, f"Core обновлён до {tag}")

        except Exception as exc:
            logger.error(f"Ошибка обновления Core: {exc}")
            _log(f"✗ Ошибка: {exc}")
            if on_done:
                on_done(False, str(exc))

    threading.Thread(target=_worker, daemon=True, name="core-updater").start()
