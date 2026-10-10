"""
core/updates/releases.py
------------------------
Общее для всех обновлений: последний релиз с GitHub (кэш, токен, GitLab —
запасной источник для самого FlowZap), скачивание файла релиза с проверкой
размера и sha256 (зеркало — если GitHub недоступен) и фоновая обвязка
download_and_install_* (run_install).
"""
import hashlib
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

logger = logging.getLogger(__name__)

FLOWZAP_REPO      = "xxFireflyxx/FlowZap-Zapret-GUI"
FLOWZAP_GITLAB_ID = "xx_firefly_xx%2Fflowzap"


_ssl_ctx = None


def ssl_context():
    """SSL-контекст с сертификатами certifi для urlopen.

    На части систем (особенно Windows 11 в изолированном окружении
    PyInstaller) отсутствует нужное системное хранилище CA-сертификатов,
    и urlopen падает с CERTIFICATE_VERIFY_FAILED. certifi — обязательная
    зависимость (requirements.txt), поэтому fallback ниже — просто на
    случай, если её всё же не окажется в окружении.
    """
    global _ssl_ctx
    if _ssl_ctx is None:
        import ssl
        try:
            import certifi
            _ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception as e:
            logger.warning(f"certifi недоступен ({e}), используем системный SSL-контекст")
            _ssl_ctx = ssl.create_default_context()
    return _ssl_ctx


_github_token: Optional[str] = None


def set_github_token(token: str) -> None:
    """Установить GitHub токен для API запросов."""
    global _github_token
    _github_token = token.strip() if token else None


def github_headers() -> dict:
    """Заголовки для GitHub API с токеном если задан."""
    headers = {"User-Agent": "FlowZap/1.0"}
    if _github_token:
        headers["Authorization"] = f"token {_github_token}"
    return headers


# Специальное исключение для rate limit
class RateLimitError(Exception):
    pass


class NetworkError(ValueError):
    """Не удалось связаться с GitHub или скачать файл — повод повторить без
    своего DNS (core.dns.manager.retry_without_dns)."""


def is_network_error(e: Exception) -> bool:
    return isinstance(e, NetworkError)


def retry_without_dns(attempt, should_retry=is_network_error, log=None):
    """core.dns.manager.retry_without_dns (импорт здесь — без цикла импортов)."""
    from core.dns.manager import retry_without_dns as retry
    return retry(attempt, should_retry, log)


def _verify_download(data: bytes, asset: dict, what: str) -> None:
    """Сверить скачанный файл с данными GitHub API: размер и sha256 (поле
    digest). Годится и для зеркал — там тот же файл того же релиза.
    Нет данных (релиз из GitLab-fallback) — проверка пропускается.
    Несовпадение — ValueError: повреждённый файл не ставим, тем более что
    он запускается с правами администратора."""
    size = asset.get("size") or 0
    if size and len(data) != size:
        logger.error(f"{what}: размер {len(data)} вместо {size}")
        raise ValueError("Файл скачался не полностью — попробуйте ещё раз")
    digest = (asset.get("digest") or "").lower()
    if not digest.startswith("sha256:"):
        logger.info(f"{what}: контрольной суммы в релизе нет — проверка пропущена")
        return
    actual = hashlib.sha256(data).hexdigest()
    if actual != digest[len("sha256:"):]:
        logger.error(f"{what}: sha256 {actual} не совпадает с {digest}")
        raise ValueError("Скачанный файл повреждён (не совпала контрольная сумма) — попробуйте ещё раз")
    logger.info(f"{what}: sha256 совпадает")


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
    - transfer_timeout — таймаут сокета в urlopen: действует на каждое
      чтение, поэтому обрывает только реальное зависание передачи (нет новых
      байт дольше transfer_timeout секунд), а не общий лимит на весь файл.
    """
    result: dict = {}

    def _connect() -> None:
        try:
            req = urllib.request.Request(url, headers=headers)
            result["response"] = urllib.request.urlopen(req, timeout=transfer_timeout, context=ssl_context())
        except Exception as e:
            result["error"] = e

    t = threading.Thread(target=_connect, daemon=True)
    t.start()
    t.join(connect_timeout)
    if t.is_alive():
        raise TimeoutError(f"Нет ответа от сервера за {connect_timeout} сек")
    if "error" in result:
        raise result["error"]

    with result["response"] as r:
        return r.read()


def _fetch(url: str, timeout: float = 120) -> bytes:
    """Скачать файл с зеркала (без токена GitHub)."""
    req = urllib.request.Request(url, headers={"User-Agent": "FlowZap/1.0"})
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as r:
        return r.read()


def get_from_gitlab() -> Optional[dict]:
    """GitLab fallback - возвращает данные в GitHub-совместимом формате."""
    try:
        url = f"https://gitlab.com/api/v4/projects/{FLOWZAP_GITLAB_ID}/releases"
        req = urllib.request.Request(url, headers={"User-Agent": "FlowZap/1.0"})
        with urllib.request.urlopen(req, timeout=10, context=ssl_context()) as r:
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

# Бета-канал: строка beta = true в [updater] config.toml (в интерфейсе её нет).
# FlowZap видит и пре-релизы самого FlowZap — автор публикует новую версию
# пре-релизом (publish_release.py → 2), обновляется на неё обычной кнопкой и
# только потом открывает её всем (→ 3). Пре-релизы есть только на GitHub.
_beta_channel = False


def set_beta_channel(enabled: bool) -> None:
    global _beta_channel
    _beta_channel = bool(enabled)
    _release_cache.pop(FLOWZAP_REPO, None)
    if _beta_channel:
        logger.info("Бета-канал: FlowZap видит и пре-релизы")


def _version_key(tag: str) -> tuple[int, ...]:
    import re
    m = re.match(r"\s*[vV]?(\d+(?:\.\d+)*)", tag or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def _newest_including_prereleases(repo: str) -> dict:
    """Самый новый по номеру версии релиз или пре-релиз (черновики — нет)."""
    url = f"https://api.github.com/repos/{repo}/releases?per_page=20"
    req = urllib.request.Request(url, headers=github_headers())
    with urllib.request.urlopen(req, timeout=10, context=ssl_context()) as r:
        releases = [x for x in json.load(r) if isinstance(x, dict) and not x.get("draft")]
    if not releases:
        raise ValueError("на GitHub нет релизов")
    return max(releases, key=lambda x: _version_key(x.get("tag_name", "")))


def get_latest_release(repo: str = FLOWZAP_REPO, force: bool = False,
                       gitlab: bool = True) -> Optional[dict]:
    """Последний релиз с GitHub (кэш на 3 часа). Для самого FlowZap при
    любой ошибке GitHub (лимит запросов, блокировка, 404) — релиз с GitLab;
    gitlab=False — без него (и без взятого с него в кэше): см.
    latest_release_for_user."""
    if not force and repo in _release_cache:
        ts, cached = _release_cache[repo]
        from_gitlab = isinstance(cached, dict) and cached.get("_source") == "gitlab"
        if time.time() - ts < _CACHE_TTL and (gitlab or not from_gitlab):
            logger.debug(f"Релиз из кэша: {repo}")
            return cached

    try:
        if _beta_channel and repo == FLOWZAP_REPO:
            result = _newest_including_prereleases(repo)
        else:
            url = f"https://api.github.com/repos/{repo}/releases/latest"
            req = urllib.request.Request(url, headers=github_headers())
            with urllib.request.urlopen(req, timeout=10, context=ssl_context()) as r:
                result = json.load(r)
    except Exception as e:
        if isinstance(e, urllib.error.HTTPError) and e.code == 403:
            logger.warning(f"GitHub: лимит запросов ({repo})")
        else:
            logger.error(f"Ошибка проверки обновлений {repo}: {e}")
        if repo != FLOWZAP_REPO or not gitlab:
            return None
        logger.info("Пробуем GitLab...")
        result = get_from_gitlab()
        if not result:
            return None
    _release_cache[repo] = (time.time(), result)
    return result


def latest_release_for_user(repo: str) -> dict:
    """Последний релиз, когда его просит пользователь («Проверить»,
    «Обновить», установка): GitHub → GitHub без своего DNS (на пару секунд:
    через DNS-прокси GitHub часто отвечает «лимит запросов» — один адрес на
    многих) → для самого FlowZap GitLab. Не сразу GitLab: там нет
    пре-релизов (бета-канал) и он мог отстать. Автопроверка при запуске DNS
    не трогает — она зовёт get_latest_release. NetworkError — нигде нет."""
    def _attempt() -> dict:
        release = get_latest_release(repo, gitlab=False)
        if release is None:
            raise NetworkError("Не удалось получить информацию о релизе")
        return release

    try:
        return retry_without_dns(_attempt)
    except NetworkError:
        if repo != FLOWZAP_REPO:
            raise
    logger.info("Пробуем GitLab...")
    release = get_from_gitlab()
    if not release:
        raise NetworkError("Не удалось получить информацию о релизе")
    _release_cache[repo] = (time.time(), release)
    return release


def check_release_async(
    repo: str,
    on_done: Callable[[Optional[dict]], None],
) -> None:
    """
    Проверить последний релиз в фоновом потоке — get_latest_release() блокирующий
    (до ~20 с с учётом GitLab-fallback). Сам создаёт поток и зовёт on_done(release | None),
    как download_and_install_*: вызывающий UI-код только оборачивает on_done в Signal.emit.
    """
    def _worker() -> None:
        try:
            release = latest_release_for_user(repo)
        except Exception:
            release = None
        on_done(release)

    threading.Thread(target=_worker, daemon=True, name="release-check").start()


def sourceforge_url(project: str, tag: str, filename: str) -> str:
    """
    Точная ссылка на конкретный файл конкретного релиза на SourceForge.
    /files/latest/download НЕ годится — SourceForge отдаёт по ней первый
    файл в списке релиза (часто это "Source code.tar.gz", а не нужный
    exe/zip), независимо от того, какая версия реально нужна.
    """
    return f"https://sourceforge.net/projects/{project}/files/{tag}/{filename}/download"


_DOWNLOAD_FAILED = ("Не удалось скачать обновление с GitHub. Резервный "
                    "источник тоже не дал результата. Попробуйте позже.")


def download_asset(
    asset: dict,
    mirror: Optional[Callable[[], tuple[str, bool]]] = None,
    mirror_name: str = "зеркало",
) -> bytes:
    """Скачать файл релиза: GitHub, при неудаче — зеркало (молча, без ошибки
    пользователю). mirror() → (url, тот же ли это файл того же релиза);
    тот же — сверяем с размером и sha256 из GitHub, как и загрузку с GitHub."""
    name = asset["name"]
    try:
        # 15 с ждём ответа, дальше таймаут шире — обрывает только зависание закачки.
        # Токен GitHub — только для его API (архив исходников TG Proxy): файлы
        # релиза публичные, а заголовок ушёл бы и по перенаправлению на
        # хранилище, и на GitLab (релиз из запасного источника)
        url = asset["browser_download_url"]
        headers = github_headers() if url.startswith("https://api.github.com/") else {"User-Agent": "FlowZap/1.0"}
        data = _download_with_grace(url, headers)
        _verify_download(data, asset, name)
        return data
    except Exception as e:
        github_error = e
        logger.warning(f"Скачивание {name} с GitHub не удалось: {e}")
    if mirror is not None:
        logger.info(f"Пробуем {mirror_name}...")
        try:
            url, same_file = mirror()
            data = _fetch(url)
            logger.info(f"{mirror_name}: скачано {len(data)} байт")
            if same_file:
                _verify_download(data, asset, f"{name} ({mirror_name})")
            return data
        except Exception as e:
            logger.error(f"GitHub: {github_error}. {mirror_name}: {e}")
    raise NetworkError(_DOWNLOAD_FAILED)


def latest_asset(repo: str, find: Callable[[dict], Optional[dict]], what: str,
                  log: Callable[[str], None]) -> tuple[str, dict]:
    """(тег, файл) последнего релиза; ValueError с текстом для пользователя."""
    log("Проверяем обновления...")
    release = latest_release_for_user(repo)
    tag = release.get("tag_name", "unknown")
    asset = find(release)
    if not asset:
        raise ValueError(f"{what} не найден в релизе {tag}")
    log("Скачивается...")
    return tag, asset


def run_install(
    thread_name: str,
    error_label: str,
    body: Callable[[Callable[[str], None]], str],
    on_progress: Optional[Callable[[str], None]],
    on_done: Optional[Callable[[bool, str], None]],
) -> None:
    """Общая обвязка download_and_install_*: body(log) в фоновом потоке,
    возвращает текст успеха; исключение — ошибка. on_progress/on_done
    зовутся из фонового потока — UI маршалит их через Signal."""

    def _log(msg: str) -> None:
        logger.info(msg)
        if on_progress:
            on_progress(msg)

    def _worker() -> None:
        try:
            # Не скачалось при включённом DNS — повтор без него (см. retry_without_dns);
            # пользователю это не показываем, только в лог
            message = retry_without_dns(lambda: body(_log))
        except Exception as exc:
            logger.error(f"{error_label}: {exc}")
            _log(f"✗ Ошибка: {exc}")
            if on_done:
                on_done(False, str(exc))
            return
        if on_done:
            on_done(True, message)

    threading.Thread(target=_worker, daemon=True, name=thread_name).start()
