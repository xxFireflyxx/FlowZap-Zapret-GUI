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

GUI_VERSION = "0.2.2"
FLOWZAP_REPO = "xxFireflyxx/Flowzap-gui-zapret-dns-tgwsproxy"


_github_token: Optional[str] = None


def set_github_token(token: str) -> None:
    """Установить GitHub токен для API запросов."""
    global _github_token
    _github_token = token.strip() if token else None


def _github_headers() -> dict:
    """Заголовки для GitHub API с токеном если задан."""
    headers = {"User-Agent": "FlowZap/1.0"}
    if _github_token:
        headers["Authorization"] = f"token {_github_token}"
    return headers


def get_latest_release(repo: str = FLOWZAP_REPO) -> Optional[dict]:
    try:
        import urllib.request, json
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        req = urllib.request.Request(url, headers=_github_headers())
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except Exception as e:
        logger.error(f"Ошибка проверки обновлений: {e}")
        return None


def find_exe_asset(release: dict) -> Optional[dict]:
    """
    Найти asset для обновления FlowZap GUI.
    Приоритеты:
      1. flowzap-vX.X.X.zip / flowzap-vX.X.X.rar
      2. flowzap-*.zip / flowzap-*.rar
      3. release-*.zip / release-*.rar (старый формат)
      4. release.zip / release.rar
      5. любой zip/rar
      6. прямой exe
    """
    assets = release.get("assets", [])

    real_assets = [
        a for a in assets
        if "/archive/" not in a.get("browser_download_url", "")
        and a.get("browser_download_url", "")
    ]

    # Приоритет 1: flowzap-vX.X.X.zip или flowzap-vX.X.X.rar
    for asset in real_assets:
        name = asset.get("name", "").lower()
        if name.startswith("flowzap-v") and (name.endswith(".zip") or name.endswith(".rar")):
            return asset

    # Приоритет 2: flowzap-*.zip или flowzap-*.rar
    for asset in real_assets:
        name = asset.get("name", "").lower()
        if name.startswith("flowzap-") and (name.endswith(".zip") or name.endswith(".rar")):
            return asset

    # Приоритет 3: release-*.zip или release-*.rar (старый формат)
    for asset in real_assets:
        name = asset.get("name", "").lower()
        if name.startswith("release-") and (name.endswith(".zip") or name.endswith(".rar")):
            return asset

    # Приоритет 4: release.zip или release.rar
    for asset in real_assets:
        name = asset.get("name", "").lower()
        if name in ("release.zip", "release.rar"):
            return asset

    # Приоритет 5: любой zip/rar
    for asset in real_assets:
        name = asset.get("name", "").lower()
        if name.endswith(".zip") or name.endswith(".rar"):
            return asset

    # Fallback: прямой exe
    for asset in real_assets:
        if asset.get("name", "").lower().endswith(".exe"):
            return asset

    return None


def _extract_exe_from_zip(data: bytes) -> Optional[tuple]:
    """Извлечь главный exe из zip архива. Возвращает (имя, байты) или None."""
    import zipfile, io
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            candidates = []
            for member in zf.namelist():
                name_lower = member.lower()
                if (name_lower.endswith(".exe") and
                        "__pycache__" not in name_lower and
                        "/lib/" not in name_lower and
                        "\\lib\\" not in name_lower):
                    candidates.append(member)

            if not candidates:
                return None

            root_exe = next(
                (m for m in candidates if "/" not in m and "\\" not in m),
                candidates[0]
            )
            logger.info(f"Найден exe в архиве: {root_exe}")
            return Path(root_exe).name, zf.read(root_exe)
    except Exception as e:
        logger.error(f"Ошибка распаковки zip: {e}")
    return None


def download_and_install_exe(
    install_dir: Path,
    repo: str = FLOWZAP_REPO,
    on_progress: Optional[Callable[[str], None]] = None,
    on_done: Optional[Callable[[bool, str], None]] = None,
) -> None:
    def _log(msg: str) -> None:
        logger.info(msg)
        if on_progress:
            on_progress(msg)

    def _worker() -> None:
        try:
            import urllib.request, sys

            _log("Получаем информацию о последнем релизе...")
            release = get_latest_release(repo)
            if not release:
                raise ValueError("Не удалось получить информацию о релизе")

            tag = release.get("tag_name", "unknown")
            asset = find_exe_asset(release)
            if not asset:
                raise ValueError(f"Файл для обновления не найден в релизе {tag}")

            dl_url = asset["browser_download_url"]
            asset_name = asset["name"]
            size_mb = asset.get("size", 0) / 1024 / 1024
            _log(f"Скачиваем {asset_name} ({tag}, {size_mb:.1f} МБ)...")

            req = urllib.request.Request(dl_url, headers=_github_headers())
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()

            current_exe = (
                Path(sys.executable)
                if getattr(sys, "frozen", False)
                else install_dir / "FlowZap.exe"
            )
            target_dir = current_exe.parent
            asset_lower = asset_name.lower()

            if asset_lower.endswith(".zip"):
                _log("Распаковываем zip архив...")
                result = _extract_exe_from_zip(data)
                if not result:
                    raise ValueError("exe не найден внутри zip архива")
                exe_name, exe_data = result
                new_exe = target_dir / exe_name
                new_exe.write_bytes(exe_data)

            elif asset_lower.endswith(".rar"):
                import tempfile, subprocess
                _log("Распаковываем rar архив...")
                with tempfile.TemporaryDirectory() as tmp:
                    rar_path = Path(tmp) / asset_name
                    rar_path.write_bytes(data)
                    extracted = False

                    # Пробуем WinRAR, затем 7-Zip
                    tools = [
                        r"C:\Program Files\WinRAR\Rar.exe",
                        r"C:\Program Files (x86)\WinRAR\Rar.exe",
                        r"C:\Program Files\WinRAR\WinRAR.exe",
                        "7z.exe",
                        r"C:\Program Files\7-Zip\7z.exe",
                    ]
                    for tool in tools:
                        try:
                            r = subprocess.run(
                                [tool, "e", str(rar_path), tmp, "-y"],
                                capture_output=True, timeout=30,
                            )
                            if r.returncode in (0, 1):
                                extracted = True
                                break
                        except Exception:
                            continue

                    if not extracted:
                        raise ValueError("Не удалось распаковать rar. Установите WinRAR или 7-Zip.")

                    exe_files = [
                        f for f in Path(tmp).iterdir()
                        if f.suffix.lower() == ".exe"
                    ]
                    if not exe_files:
                        raise ValueError("exe не найден в rar архиве")

                    new_exe = target_dir / exe_files[0].name
                    shutil.copy2(exe_files[0], new_exe)

            else:
                new_exe = target_dir / asset_name
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


# ── Core (zapret) ──────────────────────────────────────────────────────

def get_installed_core_version(zapret_dir: Path) -> Optional[str]:
    ver_file = zapret_dir / "version.txt"
    if ver_file.exists():
        try:
            return ver_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return None


def find_zip_asset(release: dict) -> Optional[dict]:
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

            req = urllib.request.Request(dl_url, headers=_github_headers())
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()

            _log("Распаковываем архив...")
            with tempfile.TemporaryDirectory() as tmp:
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    zf.extractall(tmp)

                entries = os.listdir(tmp)
                if len(entries) == 1 and os.path.isdir(os.path.join(tmp, entries[0])):
                    src = Path(tmp) / entries[0]
                else:
                    src = Path(tmp)

                zapret_dir.mkdir(parents=True, exist_ok=True)
                for item in src.iterdir():
                    dst = zapret_dir / item.name
                    if item.is_dir():
                        if dst.exists():
                            shutil.rmtree(dst)
                        shutil.copytree(item, dst)
                    else:
                        shutil.copy2(item, dst)

                (zapret_dir / "version.txt").write_text(tag, encoding="utf-8")

            _log(f"✓ zapret обновлён до {tag}.")
            if on_done:
                on_done(True, f"zapret обновлён до {tag}")

        except Exception as exc:
            logger.error(f"Ошибка обновления Core: {exc}")
            _log(f"✗ Ошибка: {exc}")
            if on_done:
                on_done(False, str(exc))

    threading.Thread(target=_worker, daemon=True, name="core-updater").start()
