"""Замена папки компонента с откатом и восстановлением после прерывания."""
import json
import logging
import os
import shutil
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


def transaction_dir(target: Path) -> Path:
    return target.with_name(f".{target.name}.flowzap-update")


def _check_paths(target: Path, work: Path, component: str) -> None:
    # Не переименовываем и не удаляем каталоги через символьные ссылки/junction.
    for path in (target, work, work / "old", work / "new", work / "rejected"):
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            logger.error("Папка обновления %s является ссылкой: %s", component, path)
            raise ValueError(f"Не удалось безопасно заменить папку {component} — подробности в логе")


def _retire(target: Path) -> Path:
    """Убираем журнал атомарно; прерванная уборка больше не вызовет откат."""
    work = transaction_dir(target)
    retired = work.with_name(work.name + ".cleanup-" + uuid.uuid4().hex)
    work.rename(retired)
    return retired


def cleanup_retired(target: Path, component: str = "zapret") -> None:
    """Только в установщике: удаление резервной копии не задерживает запуск UI."""
    prefix = transaction_dir(target).name + ".cleanup-"
    try:
        paths = list(target.parent.iterdir())
    except OSError:
        logger.warning("Не удалось проверить временные файлы %s", component, exc_info=True)
        return
    for path in paths:
        # Имя target может содержать скобки: не используем его как glob.
        suffix = path.name.removeprefix(prefix)
        if not path.name.startswith(prefix) or len(suffix) != 32 or any(c not in "0123456789abcdef" for c in suffix):
            continue
        if path.is_dir() and not path.is_symlink() and not getattr(path, "is_junction", lambda: False)():
            try:
                shutil.rmtree(path)
            except OSError:
                logger.warning("Не удалось убрать временные файлы %s: %s", component, path, exc_info=True)


def discard_preparation(target: Path, component: str = "zapret") -> None:
    """При отказе подготовки можно удалить только данные без начатой замены."""
    work = transaction_dir(target)
    if not work.exists():
        return
    _check_paths(target, work, component)
    if not (work / "state.json").exists() and not (work / "old").exists():
        _retire(target)


def recover(target: Path, component: str = "zapret") -> None:
    """До запуска компонента: незавершённая замена возвращает прежнюю установку."""
    work = transaction_dir(target)
    if not work.exists():
        return
    _check_paths(target, work, component)
    journal = work / "state.json"
    old = work / "old"
    if not journal.exists():
        # Журнал пишется до первого переименования рабочей установки.
        if old.exists():
            raise ValueError(f"Не удалось восстановить {component} — резервная копия сохранена")
        _retire(target)
        return
    state = json.loads(journal.read_text(encoding="utf-8"))
    if state not in ({"had_old": True}, {"had_old": False}):
        raise ValueError(f"Не удалось прочитать состояние обновления {component}")
    if (work / "committed").exists():
        if not target.is_dir():
            raise ValueError(f"Папка {component} недоступна — резервная копия сохранена")
    elif old.exists():
        # Даже если новый каталог уже на месте, без отметки завершения откатываем.
        if target.exists():
            target.rename(work / "rejected")
        old.rename(target)
        logger.warning("После прерывания обновления возвращена предыдущая версия %s", component)
    elif state["had_old"]:
        if not target.is_dir():
            raise ValueError(f"Не удалось восстановить {component} — резервная копия не найдена")
    elif target.exists():
        # Первая установка была прервана после переноса новой папки.
        target.rename(work / "rejected")
    _retire(target)


def commit(target: Path, component: str = "zapret") -> None:
    """work/new полностью подготовлена. До committed любой сбой означает откат."""
    work = transaction_dir(target)
    _check_paths(target, work, component)
    had_old = target.exists()
    # Недописанный журнал не должен мешать следующему запуску, если рабочую
    # установку ещё не трогали.
    state_tmp = work / "state.tmp"
    with state_tmp.open("w", encoding="utf-8") as stream:
        json.dump({"had_old": had_old}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    state_tmp.replace(work / "state.json")
    try:
        if had_old:
            target.rename(work / "old")
        (work / "new").rename(target)
        with (work / "committed").open("wb") as stream:
            stream.write(b"1")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        logger.exception("Замена %s не удалась, возвращаем предыдущую установку", component)
        # Пустая/недописанная отметка не должна означать завершение установки.
        (work / "committed").unlink(missing_ok=True)
        try:
            recover(target, component)
        except Exception:
            logger.exception("Откат %s не завершён; резервная копия сохранена", component)
            raise ValueError(f"Не удалось завершить обновление {component}. Перезапустите FlowZap для восстановления") from None
        raise ValueError(f"Не удалось заменить файлы {component} — предыдущая установка сохранена") from None
    # Отказ уборки уже не делает успешную замену неудачной.
    try:
        _retire(target)
    except OSError:
        logger.warning("Временные файлы %s будут убраны позже", component, exc_info=True)
