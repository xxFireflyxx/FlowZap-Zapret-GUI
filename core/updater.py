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

GUI_VERSION = "0.5.0"
FLOWZAP_REPO      = "xxFireflyxx/FlowZap-Zapret-GUI"
FLOWZAP_GITLAB_ID = "xx_firefly_xx%2Fflowzap"


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


# Специальное исключение для rate limit
class RateLimitError(Exception):
    pass


def _download_with_grace(
    url: str,
    headers: dict,
    connect_timeout: float = 15,
    transfer_timeout: float = 120,
) -> bytes:
    """
    Качает файл по url с раздельными таймаутами:
    - connect_timeout — сколько ждём ОТВЕТА сервера целиком (не одну попытку
      соединения — если DNS вернул несколько адресов или сервер сначала
      редиректит на другой хост, urlopen(timeout=X) ограничивает только
      КАЖДУЮ такую попытку по отдельности, и они суммируются). Поэтому
      соединение выполняется в отдельном демон-потоке: не уложились в
      connect_timeout секунд — сразу TimeoutError, не дожидаясь, пока сокет
      переберёт все адреса. Поток-неудачник просто тихо доживает и
      завершается сам, ничего не блокируя.
    - transfer_timeout — если ответ получен и данные пошли, таймаут на
      чтение шире: обрывает только реальное зависание передачи (нет новых
      байт дольше transfer_timeout секунд), а не общий лимит на весь файл.
    """
    import urllib.request, threading

    result: dict = {}

    def _connect() -> None:
        try:
            req = urllib.request.Request(url, headers=headers)
            result["response"] = urllib.request.urlopen(req, timeout=transfer_timeout)
        except Exception as e:
            result["error"] = e

    t = threading.Thread(target=_connect, daemon=True)
    t.start()
    t.join(connect_timeout)
    if t.is_alive():
        raise TimeoutError(f"Нет ответа от сервера за {connect_timeout} сек")
    if "error" in result:
        raise result["error"]

    r = result["response"]
    try:
        r.fp.raw._sock.settimeout(transfer_timeout)
    except Exception:
        pass  # не удалось достать сокет — читаем с тем же (широким) таймаутом
    return r.read()


def _get_from_gitlab() -> Optional[dict]:
    """GitLab fallback - возвращает данные в GitHub-совместимом формате."""
    try:
        import urllib.request, json
        url = f"https://gitlab.com/api/v4/projects/{FLOWZAP_GITLAB_ID}/releases"
        req = urllib.request.Request(url, headers={"User-Agent": "FlowZap/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            releases = json.load(r)
        if not releases:
            return None
        rel = releases[0]
        assets = []
        for link in rel.get("assets", {}).get("links", []):
            assets.append({
                "name": link.get("name", ""),
                "browser_download_url": link.get("url", ""),
                "size": 0,
            })
        logger.info(f"GitLab fallback: {rel.get('tag_name')}")
        return {
            "tag_name": rel.get("tag_name", ""),
            "name":     rel.get("name", ""),
            "assets":   assets,
            "_source":  "gitlab",
        }
    except Exception as e:
        logger.error(f"GitLab fallback error: {e}")
        return None


# Кэш релизов: repo -> (timestamp, release_dict)
_release_cache: dict = {}
_CACHE_TTL = 3 * 3600  # 3 часа


def get_latest_release(repo: str = FLOWZAP_REPO, force: bool = False) -> Optional[dict]:
    import time, urllib.request, json

    # Проверяем in-memory кэш
    if not force and repo in _release_cache:
        ts, cached = _release_cache[repo]
        if time.time() - ts < _CACHE_TTL:
            logger.debug(f"Релиз из кэша: {repo}")
            return cached

    try:
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        req = urllib.request.Request(url, headers=_github_headers())
        with urllib.request.urlopen(req, timeout=10) as r:
            result = json.load(r)
        _release_cache[repo] = (time.time(), result)
        return result
    except Exception as e:
        err_str = str(e).lower()
        if "403" in err_str or "rate limit" in err_str or "404" in err_str:
            if repo == FLOWZAP_REPO:
                reason = "rate limit" if ("403" in err_str or "rate limit" in err_str) else "404"
                logger.warning(f"GitHub {reason} - switching to GitLab fallback")
                result = _get_from_gitlab()
                if result:
                    _release_cache[repo] = (time.time(), result)
                return result
        logger.error(f"Ошибка проверки обновлений: {e}")
        if repo == FLOWZAP_REPO:
            logger.info("Trying GitLab fallback after error...")
            result = _get_from_gitlab()
            if result:
                _release_cache[repo] = (time.time(), result)
            return result
        return None


def find_exe_asset(release: dict) -> Optional[dict]:
    """
    Найти asset для обновления FlowZap GUI.
    Приоритеты:
      1. flowzap-vX.X.X.zip
      2. flowzap-*.zip
      3. release-*.zip (старый формат)
      4. release.zip
      5. любой zip
      6. прямой exe
    """
    assets = release.get("assets", [])

    real_assets = [
        a for a in assets
        if "/archive/" not in a.get("browser_download_url", "")
        and a.get("browser_download_url", "")
    ]

    # Приоритет 1: flowzap-vX.X.X.zip
    for asset in real_assets:
        name = asset.get("name", "").lower()
        if name.startswith("flowzap-v") and name.endswith(".zip"):
            return asset

    # Приоритет 2: flowzap-*.zip
    for asset in real_assets:
        name = asset.get("name", "").lower()
        if name.startswith("flowzap-") and name.endswith(".zip"):
            return asset

    # Приоритет 3: release-*.zip (старый формат)
    for asset in real_assets:
        name = asset.get("name", "").lower()
        if name.startswith("release-") and name.endswith(".zip"):
            return asset

    # Приоритет 4: release.zip
    for asset in real_assets:
        name = asset.get("name", "").lower()
        if name == "release.zip":
            return asset

    # Приоритет 5: любой zip
    for asset in real_assets:
        name = asset.get("name", "").lower()
        if name.endswith(".zip"):
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

            _log("Проверяем обновления...")
            release = get_latest_release(repo)
            if not release:
                raise ValueError("Не удалось получить информацию о релизе")

            tag = release.get("tag_name", "unknown")
            asset = find_exe_asset(release)
            if not asset:
                raise ValueError(f"Файл для обновления не найден в релизе {tag}")

            dl_url = asset["browser_download_url"]
            asset_name = asset["name"]
            _log("Скачивается...")

            data = None
            github_error: Optional[Exception] = None

            # Попытка 1: GitHub напрямую (15 сек ждём ответа, дальше таймаут
            # шире — обрывает только зависание уже начавшейся закачки)
            try:
                data = _download_with_grace(dl_url, _github_headers())
            except Exception as e:
                github_error = e
                logger.warning(f"Скачивание с GitHub не удалось: {e}")

            # Попытка 2: зеркало GitLab — молча, без вывода ошибки пользователю
            if data is None:
                logger.info("Пробуем зеркало GitLab...")
                try:
                    gitlab_release = _get_from_gitlab()
                    if not gitlab_release:
                        raise ValueError("GitLab недоступен")
                    gitlab_asset = find_exe_asset(gitlab_release)
                    if not gitlab_asset:
                        raise ValueError("Файл для обновления не найден в релизе GitLab")
                    mirror_req = urllib.request.Request(
                        gitlab_asset["browser_download_url"],
                        headers={"User-Agent": "FlowZap/1.0"},
                    )
                    with urllib.request.urlopen(mirror_req, timeout=120) as r:
                        data = r.read()
                    asset_name = gitlab_asset["name"]
                    tag = gitlab_release.get("tag_name", tag)
                    logger.info(f"Зеркало GitLab: скачано {len(data)} байт")
                except Exception as mirror_error:
                    # Оба источника недоступны — вот теперь показываем ошибку пользователю
                    logger.error(f"GitHub: {github_error}. GitLab: {mirror_error}")
                    raise ValueError(
                        "Не удалось скачать обновление с GitHub. Резервный "
                        "источник тоже не дал результата. Попробуйте позже."
                    )

            current_exe = (
                Path(sys.executable)
                if getattr(sys, "frozen", False)
                else install_dir / "FlowZap.exe"
            )
            target_dir = current_exe.parent
            asset_lower = asset_name.lower()

            # Извлекаем новый exe
            if asset_lower.endswith(".zip"):
                _log("Распаковываем...")
                result = _extract_exe_from_zip(data)
                if not result:
                    raise ValueError("exe не найден внутри zip архива")
                exe_name, exe_data = result
            else:
                exe_name = asset_name
                exe_data = data

            # Сохраняем во временную папку системы
            import tempfile as _tempfile
            tmp_dir = Path(_tempfile.gettempdir())
            temp_exe = tmp_dir / "_flowzap_update_tmp.exe"
            temp_exe.write_bytes(exe_data)

            # Создаём bat с повторными попытками замены
            bat_path = tmp_dir / "_flowzap_update.bat"
            lines = [
                "@echo off",
                # Ждём закрытия приложения (до 15 сек, по 1 сек)
                "set /a attempts=0",
                ":wait_loop",
                "timeout /t 1 /nobreak >nul",
                'move /y "' + str(temp_exe) + '" "' + str(current_exe) + '" >nul 2>&1',
                "if errorlevel 1 (",
                "  set /a attempts+=1",
                "  if %attempts% lss 15 goto wait_loop",
                "  echo Failed to replace exe after 15 attempts",
                "  goto cleanup",
                ")",
                # Успешно заменили — запускаем новую версию
                'start "" "' + str(current_exe) + '"',
                ":cleanup",
                'del "%~f0"',
            ]
            bat_path.write_text("\r\n".join(lines), encoding="utf-8")

            # Запускаем bat с правами администратора через ShellExecute runas
            import subprocess as _sp, ctypes as _ct
            try:
                # ShellExecute с runas — покажет UAC если нужно
                _ct.windll.shell32.ShellExecuteW(
                    None, "runas", "cmd.exe",
                    f'/c "{bat_path}"',
                    None, 0  # SW_HIDE
                )
            except Exception:
                # Fallback без UAC
                _sp.Popen(
                    ["cmd.exe", "/c", str(bat_path)],
                    creationflags=0x08000000,
                    close_fds=True,
                )

            _log(f"✓ Обновление до {tag} готово. Приложение перезапустится...")
            if on_done:
                on_done(True, f"Обновлено до {tag}. Перезапускаем...")

        except Exception as exc:
            logger.error(f"Ошибка обновления: {exc}")
            _log(f"✗ Ошибка: {exc}")
            if on_done:
                on_done(False, str(exc))

    threading.Thread(target=_worker, daemon=True, name="flowzap-updater").start()


# ── Core (zapret) ──────────────────────────────────────────────────────

# Зеркало на SourceForge — точная копия Flowseal/zapret-discord-youtube,
# используется как молчаливый fallback, если GitHub недоступен из сети.
SOURCEFORGE_ZAPRET_PROJECT = "flowseal.mirror"


def _sourceforge_mirror_url(project: str, tag: str, filename: str) -> str:
    """
    Точная ссылка на конкретный файл конкретного релиза на SourceForge.
    /files/latest/download НЕ годится — SourceForge отдаёт по ней первый
    файл в списке релиза (часто это "Source code.tar.gz", а не нужный
    exe/zip), независимо от того, какая версия реально нужна.
    """
    return f"https://sourceforge.net/projects/{project}/files/{tag}/{filename}/download"


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



def _unload_windivert() -> None:
    """
    Выгрузить драйвер WinDivert из ядра перед обновлением.
    Без этого Windows блокирует перезапись WinDivert64.sys с WinError 5.
    """
    import subprocess, os, time
    if os.name != "nt":
        return
    flags = 0x08000000  # CREATE_NO_WINDOW

    # Убиваем все winws.exe — они держат хэндл на драйвер
    try:
        subprocess.run(["taskkill", "/F", "/IM", "winws.exe"],
                       capture_output=True, creationflags=flags)
    except Exception:
        pass

    time.sleep(1)

    # Останавливаем и удаляем службу WinDivert (возможны оба имени)
    for svc in ("WinDivert", "WinDivert14"):
        try:
            subprocess.run(["sc", "stop", svc],
                           capture_output=True, creationflags=flags, timeout=5)
        except Exception:
            pass
        try:
            subprocess.run(["sc", "delete", svc],
                           capture_output=True, creationflags=flags, timeout=5)
        except Exception:
            pass

    time.sleep(1)  # Ждём выгрузки драйвера из ядра
    logger.info("WinDivert выгружен перед обновлением")


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

            _log("Проверяем обновления...")
            release = get_latest_release(repo)
            if not release:
                raise ValueError("Не удалось получить информацию о релизе")

            tag = release.get("tag_name", "unknown")
            asset = find_zip_asset(release)
            if not asset:
                raise ValueError(f"Архив zapret не найден в релизе {tag}")

            dl_url = asset["browser_download_url"]
            _log("Скачивается...")

            data = None
            github_error: Optional[Exception] = None

            # Попытка 1: GitHub напрямую (15 сек ждём ответа, дальше таймаут
            # шире — обрывает только зависание уже начавшейся закачки)
            try:
                data = _download_with_grace(dl_url, _github_headers())
            except Exception as e:
                github_error = e
                logger.warning(f"Скачивание с GitHub не удалось: {e}")

            # Попытка 2: зеркало SourceForge — молча, без вывода ошибки пользователю.
            # URL строится из tag и имени файла с GitHub, поэтому если скачивание
            # успешно — это гарантированно тот же файл той же версии, отдельная
            # сверка версии не нужна.
            if data is None:
                logger.info("Пробуем зеркало SourceForge...")
                try:
                    mirror_url = _sourceforge_mirror_url(
                        SOURCEFORGE_ZAPRET_PROJECT, tag, asset["name"]
                    )
                    mirror_req = urllib.request.Request(
                        mirror_url,
                        headers={"User-Agent": "FlowZap/1.0"},
                    )
                    with urllib.request.urlopen(mirror_req, timeout=120) as r:
                        data = r.read()
                    logger.info(f"Зеркало SourceForge: скачано {len(data)} байт")
                except Exception as mirror_error:
                    # Оба источника недоступны — вот теперь показываем ошибку пользователю
                    logger.error(f"GitHub: {github_error}. SourceForge: {mirror_error}")
                    raise ValueError(
                        "Не удалось скачать обновление с GitHub. Резервный "
                        "источник тоже не дал результата. Попробуйте позже."
                    )

            logger.info("Останавливаем WinDivert...")
            _unload_windivert()
            _log("Распаковываем...")
            with tempfile.TemporaryDirectory() as tmp:
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    zf.extractall(tmp)

                entries = os.listdir(tmp)
                if len(entries) == 1 and os.path.isdir(os.path.join(tmp, entries[0])):
                    src = Path(tmp) / entries[0]
                else:
                    src = Path(tmp)

                zapret_dir.mkdir(parents=True, exist_ok=True)

                def _merge_copy(src_dir: Path, dst_dir: Path) -> None:
                    """Рекурсивно копирует файлы с заменой, не трогая лишние файлы в dst."""
                    dst_dir.mkdir(parents=True, exist_ok=True)
                    for item in src_dir.iterdir():
                        dst_item = dst_dir / item.name
                        if item.is_dir():
                            _merge_copy(item, dst_item)
                        else:
                            # Пропускаем *users.txt — пользовательские списки
                            if item.name.endswith("users.txt"):
                                logger.debug(f"Пропущен пользовательский файл: {item.name}")
                                continue
                            shutil.copy2(item, dst_item)

                _merge_copy(src, zapret_dir)

                (zapret_dir / "version.txt").write_text(tag, encoding="utf-8")

            # Создаём пустые пользовательские файлы —
            # winws.exe падает если файл указан в аргументах но не существует
            _lists_dir = zapret_dir / "lists"
            _lists_dir.mkdir(parents=True, exist_ok=True)
            for _uf in [
                "list-general-user.txt",
                "list-exclude-user.txt",
                "list-exclude.txt",
                "ipset-exclude-user.txt",
                "ipset-exclude.txt",
            ]:
                _fp = _lists_dir / _uf
                if not _fp.exists():
                    try:
                        _fp.touch()
                        logger.info(f"Создан пустой файл: {_uf}")
                    except Exception as _e:
                        logger.warning(f"Не удалось создать {_uf}: {_e}")

            _log(f"✓ zapret обновлён до {tag}.")

            # Прогрев WinDivert — первый запуск после установки требует
            # загрузки драйвера в ядро, иначе первый тест пресетов упадёт
            try:
                import subprocess, time
                winws = zapret_dir / "bin" / "winws.exe"
                if winws.exists():
                    logger.info("Инициализация WinDivert...")
                    proc = subprocess.Popen(
                        [str(winws), "--wf-tcp=80"],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        creationflags=0x08000000,
                    )
                    time.sleep(3)
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except Exception:
                        proc.kill()
                    logger.info("WinDivert инициализирован")
            except Exception as e:
                logger.debug(f"Прогрев WinDivert: {e}")

            if on_done:
                on_done(True, f"zapret обновлён до {tag}")

        except Exception as exc:
            logger.error(f"Ошибка обновления Core: {exc}")
            _log(f"✗ Ошибка: {exc}")
            if on_done:
                on_done(False, str(exc))

    threading.Thread(target=_worker, daemon=True, name="core-updater").start()


# ── TG WS Proxy ────────────────────────────────────────────────────────

TG_PROXY_REPO = "Flowseal/tg-ws-proxy"
TG_PROXY_EXE  = "TgWsProxy_windows.exe"

# Зеркало на SourceForge — точная копия exe из релиза Flowseal/tg-ws-proxy,
# используется как молчаливый fallback, если GitHub недоступен из сети.
SOURCEFORGE_TGPROXY_PROJECT = "tg-ws-proxy.mirror"


def get_installed_tg_proxy_version(tgproxy_dir: Path) -> Optional[str]:
    ver_file = tgproxy_dir / "version.txt"
    if ver_file.exists():
        try:
            return ver_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return None


def find_tg_proxy_asset(release: dict) -> Optional[dict]:
    """Найти exe для Windows в релизе tg-ws-proxy."""
    assets = release.get("assets", [])
    # Приоритет: TgWsProxy_windows.exe (Windows 10+)
    for asset in assets:
        name = asset.get("name", "").lower()
        if name == "tgwsproxy_windows.exe":
            return asset
    # Любой windows exe
    for asset in assets:
        name = asset.get("name", "").lower()
        if "windows" in name and name.endswith(".exe") and "7" not in name:
            return asset
    return None


def _get_exe_version(exe_path: Path) -> Optional[str]:
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


def download_and_install_tg_proxy(
    tgproxy_dir: Path,
    repo: str = TG_PROXY_REPO,
    on_progress: Optional[Callable[[str], None]] = None,
    on_done: Optional[Callable[[bool, str], None]] = None,
) -> None:
    def _log(msg: str) -> None:
        logger.info(msg)
        if on_progress:
            on_progress(msg)

    def _worker() -> None:
        try:
            import urllib.request

            _log("Проверяем обновления...")
            release = get_latest_release(repo)
            if not release:
                raise ValueError("Не удалось получить информацию о релизе")

            tag = release.get("tag_name", "unknown")
            asset = find_tg_proxy_asset(release)
            if not asset:
                raise ValueError(f"Файл TgWsProxy_windows.exe не найден в релизе {tag}")

            dl_url = asset["browser_download_url"]
            expected_size = asset.get("size", 0)
            _log("Скачивается...")

            data = None
            github_error: Optional[Exception] = None

            # Попытка 1: GitHub напрямую (15 сек ждём ответа, дальше таймаут
            # шире — обрывает только зависание уже начавшейся закачки)
            try:
                data = _download_with_grace(dl_url, _github_headers())
            except Exception as e:
                github_error = e
                logger.warning(f"Скачивание с GitHub не удалось: {e}")

            # Попытка 2: зеркало SourceForge — молча, без вывода ошибки пользователю.
            # URL строится из tag и имени файла с GitHub, поэтому если скачивание
            # успешно — это гарантированно тот же файл той же версии.
            if data is None:
                logger.info("Пробуем зеркало SourceForge...")
                try:
                    mirror_url = _sourceforge_mirror_url(
                        SOURCEFORGE_TGPROXY_PROJECT, tag, asset["name"]
                    )
                    mirror_req = urllib.request.Request(
                        mirror_url,
                        headers={"User-Agent": "FlowZap/1.0"},
                    )
                    with urllib.request.urlopen(mirror_req, timeout=120) as r:
                        data = r.read()
                    logger.info(f"Зеркало SourceForge: скачано {len(data)} байт")
                except Exception as mirror_error:
                    # Оба источника недоступны — вот теперь показываем ошибку пользователю
                    logger.error(f"GitHub: {github_error}. SourceForge: {mirror_error}")
                    raise ValueError(
                        "Не удалось скачать обновление с GitHub. Резервный "
                        "источник тоже не дал результата. Попробуйте позже."
                    )

            # Сверяем размер скачанного файла с ожидаемым из GitHub API — это
            # проверка на оборванную/повреждённую загрузку (с любого источника).
            # На версию не влияет: URL зеркала уже пинует нужный tag, поэтому
            # раз скачивание удалось — версия верна независимо от размера.
            if expected_size and len(data) != expected_size:
                logger.warning(
                    f"Размер скачанного файла ({len(data)}) не совпадает с "
                    f"ожидаемым из GitHub API ({expected_size}) — файл может "
                    f"быть повреждён"
                )

            tgproxy_dir.mkdir(parents=True, exist_ok=True)
            exe_path = tgproxy_dir / "TgWsProxy_windows.exe"
            exe_path.write_bytes(data)

            # Тег с GitHub — основной источник версии: URL зеркала уже
            # пинуется к конкретному tag+имени файла, так что любая успешная
            # загрузка (с GitHub или с зеркала) гарантированно соответствует
            # этому тегу. Версию из ресурсов exe используем только для
            # диагностики в логах — она иногда отстаёт от тега, если автор
            # релиза забыл обновить её внутри файла, и НЕ должна перебивать
            # тег (иначе апдейтер вечно считает, что доступно обновление,
            # даже после успешной установки).
            actual_version = _get_exe_version(exe_path)
            if actual_version and actual_version != tag.lstrip("v"):
                logger.info(
                    f"Тег релиза {tag}, но версия в ресурсах exe: "
                    f"{actual_version} (используем тег)"
                )
            installed_version = tag
            (tgproxy_dir / "version.txt").write_text(installed_version, encoding="utf-8")

            _log(f"✓ TG WS Proxy установлен ({installed_version})")
            if on_done:
                on_done(True, f"TG WS Proxy установлен ({installed_version})")

        except Exception as exc:
            logger.error(f"Ошибка установки TG Proxy: {exc}")
            _log(f"✗ Ошибка: {exc}")
            if on_done:
                on_done(False, str(exc))

    threading.Thread(target=_worker, daemon=True, name="tgproxy-updater").start()


# ── Gaming lists (medvedeff-true/ru-gaming-blocklist) ──────────────────

GAMING_LISTS_REPO_RAW = "https://raw.githubusercontent.com/medvedeff-true/ru-gaming-blocklist/main"
GAMING_LIST_DOMAINS        = "flowzap-game-list-all.txt"
GAMING_LIST_IPSET          = "flowzap-game-ipset.txt"
_GAMING_REMOTE_DOMAINS     = "medvedeff-game-list-all.txt"
_GAMING_REMOTE_IPSET       = "medvedeff-game-ipset.txt"
GAMING_UPDATE_INTERVAL = 6 * 3600  # 6 часов в секундах
_GAMING_STAMP_FILE     = "gaming_lists_updated.txt"


def update_gaming_lists(
    lists_dir: Path,
    force: bool = False,
    on_done: Optional[Callable[[bool, str], None]] = None,
) -> None:
    """
    Скачать игровые списки доменов и IP в фоновом потоке.
    Пропускает загрузку если с последнего обновления прошло менее 6 часов.
    force=True — игнорировать кэш.
    """
    def _worker() -> None:
        import urllib.request, time

        stamp_file = lists_dir / _GAMING_STAMP_FILE

        # Проверяем кэш
        if not force and stamp_file.exists():
            try:
                last = float(stamp_file.read_text(encoding="utf-8").strip())
                if time.time() - last < GAMING_UPDATE_INTERVAL:
                    logger.debug("Игровые списки актуальны, пропускаем загрузку")
                    if on_done:
                        on_done(True, "актуальны")
                    return
            except Exception:
                pass

        try:
            lists_dir.mkdir(parents=True, exist_ok=True)
            for remote, local in (
                (_GAMING_REMOTE_DOMAINS, GAMING_LIST_DOMAINS),
                (_GAMING_REMOTE_IPSET,   GAMING_LIST_IPSET),
            ):
                url = f"{GAMING_LISTS_REPO_RAW}/{remote}"
                logger.info(f"Обновление игрового списка: {remote} -> {local}")
                req = urllib.request.Request(url, headers={"User-Agent": "FlowZap/1.0"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = r.read()
                (lists_dir / local).write_bytes(data)
                logger.info(f"Сохранён: {local} ({len(data)} байт)")

            stamp_file.write_text(str(time.time()), encoding="utf-8")
            logger.info("Игровые списки обновлены")
            if on_done:
                on_done(True, "обновлены")

        except Exception as exc:
            logger.warning(f"Ошибка обновления игровых списков: {exc}")
            if on_done:
                on_done(False, str(exc))

    threading.Thread(target=_worker, daemon=True, name="gaming-lists-updater").start()
