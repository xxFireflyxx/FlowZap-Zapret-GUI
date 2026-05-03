"""
release.py — Сборщик релиза FlowZap
=====================================
Запускать из корневой папки проекта: python release.py

Что делает:
  1. Спрашивает номер версии
  2. Прописывает версию в core/updater.py
  3. Собирает исходники для GitHub (без личных данных)
  4. Собирает FlowZap.exe через PyInstaller
  5. Сохраняет всё на Рабочий стол в папку FlowZap-vX.X.X/
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ── Настройки ─────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.resolve()
UPDATER_FILE = PROJECT_ROOT / "core" / "updater.py"

# Файлы/папки которые НЕ включаем в исходники для GitHub
EXCLUDE_FROM_SOURCES = {
    "dist",
    "build",
    "__pycache__",
    ".git",
    "logs",
    "zapret",            # чужой проект, пользователь скачивает сам
    "config.toml",       # личные настройки (DNS серверы)
    "release.py",        # сам этот скрипт не нужен в репо
    "ui/main_window.py.bak",
    "ui/parameters.py.bak",
    "build.bat",         # файл сборки — не нужен в исходниках
    "FlowZap.spec",      # конфиг PyInstaller — не нужен в исходниках
    "FlowZap.manifest",  # манифест сборки — не нужен в исходниках
}

# Файлы которые включаем явно (даже если расширение не .py)
INCLUDE_EXTENSIONS = {".py", ".bat", ".spec", ".txt", ".md", ".manifest", ".toml", ".gitignore", ".png", ".ico", ".jpg", ".svg"}

# config.toml — включаем шаблон без личных данных
CONFIG_TEMPLATE = """\
[zapret]
exe_path = "zapret/bin/winws.exe"
presets_dir = "zapret"
last_preset = "general"
args = []
autostart = false

[ui]
theme = "dark"
remember_tab = true

[updater]
repo = "xxFireflyxx/Flowzap-gui-zapret-dns-tgwsproxy"
check_on_start = true

[dns]
servers = []
"""
# ──────────────────────────────────────────────────────────────────────


def ask_version() -> str:
    """Спросить номер версии у пользователя."""
    current = get_current_version()
    print(f"\n{'='*50}")
    print("  FlowZap — Сборщик релиза")
    print(f"{'='*50}")
    print(f"  Текущая версия: {current}")
    version = input(f"  Введите новую версию (Enter = оставить {current}): ").strip()
    if not version:
        version = current
    if not re.match(r"^\d+\.\d+\.\d+$", version):
        print(f"  [WARN] Версия '{version}' не соответствует формату X.Y.Z, продолжаем...")
    return version


def get_current_version() -> str:
    """Прочитать текущую версию из core/updater.py."""
    content = UPDATER_FILE.read_text(encoding="utf-8")
    m = re.search(r'GUI_VERSION\s*=\s*"([^"]+)"', content)
    return m.group(1) if m else "0.0.0"


def set_version(version: str) -> None:
    """Записать новую версию в core/updater.py."""
    content = UPDATER_FILE.read_text(encoding="utf-8")
    new_content = re.sub(
        r'GUI_VERSION\s*=\s*"[^"]+"',
        f'GUI_VERSION = "{version}"',
        content,
    )
    UPDATER_FILE.write_text(new_content, encoding="utf-8")
    print(f"  [OK] Версия обновлена → {version} в core/updater.py")


def get_desktop() -> Path:
    """Получить путь к рабочему столу."""
    return Path.home() / "Desktop"


def collect_sources(dest: Path) -> None:
    """Скопировать исходники проекта в dest, исключая личные данные."""
    print("\n  Сбор исходников...")
    dest.mkdir(parents=True, exist_ok=True)

    for item in PROJECT_ROOT.rglob("*"):
        # Пропускаем исключённые папки и файлы
        rel = item.relative_to(PROJECT_ROOT)
        parts = rel.parts

        skip = False
        for exc in EXCLUDE_FROM_SOURCES:
            exc_parts = Path(exc).parts
            if parts[:len(exc_parts)] == exc_parts or parts[0] == exc:
                skip = True
                break
        if skip:
            continue

        if item.is_file():
            if item.suffix not in INCLUDE_EXTENSIONS:
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)

    # Добавляем шаблон config.toml без личных данных
    (dest / "config.toml").write_text(CONFIG_TEMPLATE, encoding="utf-8")
    print(f"  [OK] Исходники собраны → {dest}")


def build_exe(version: str, dest: Path) -> bool:
    """Запустить PyInstaller и скопировать exe в dest."""
    print("\n  Сборка exe (PyInstaller)...")
    spec_file = PROJECT_ROOT / "FlowZap.spec"

    if not spec_file.exists():
        print("  [WARN] FlowZap.spec не найден, запускаем через main.py")
        cmd = [
            sys.executable, "-m", "PyInstaller",
            "--onefile", "--windowed", "--name", "FlowZap",
            str(PROJECT_ROOT / "main.py"),
        ]
    else:
        cmd = [sys.executable, "-m", "PyInstaller", str(spec_file), "--noconfirm"]

    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        print("  [ERROR] PyInstaller завершился с ошибкой")
        return False

    # Ищем собранный exe
    exe_candidates = [
        PROJECT_ROOT / "dist" / "FlowZap" / "FlowZap.exe",
        PROJECT_ROOT / "dist" / "FlowZap.exe",
    ]
    exe_path = None
    for candidate in exe_candidates:
        if candidate.exists():
            exe_path = candidate
            break

    if not exe_path:
        print("  [ERROR] FlowZap.exe не найден после сборки")
        return False

    # Если сборка onedir — копируем всю папку целиком
    onedir = PROJECT_ROOT / "dist" / "FlowZap"
    if onedir.is_dir():
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(onedir, dest)
        print(f"  [OK] Папка FlowZap → {dest}")
    else:
        # onefile — копируем только exe
        dest_exe = dest / "FlowZap.exe"
        shutil.copy2(exe_path, dest_exe)
        print(f"  [OK] FlowZap.exe → {dest_exe}")
    return True


def main() -> None:
    version = ask_version()
    tag = f"v{version}"

    desktop = get_desktop()
    release_dir = desktop / f"FlowZap-{tag}"
    sources_dir = release_dir / "sources"
    exe_dir     = release_dir / "release"

    print(f"\n  Папка релиза: {release_dir}")

    # Удаляем старую папку если есть
    if release_dir.exists():
        shutil.rmtree(release_dir)

    # 1. Обновляем версию в коде
    set_version(version)

    # 2. Собираем исходники
    collect_sources(sources_dir)

    # 3. Собираем exe
    exe_dir.mkdir(parents=True, exist_ok=True)
    exe_ok = build_exe(version, exe_dir)

    # Итог
    print(f"\n{'='*50}")
    print(f"  Готово! FlowZap {tag}")
    print(f"{'='*50}")
    print(f"  Исходники для GitHub : {sources_dir}")
    if exe_ok:
        print(f"  Exe для релиза       : {exe_dir / 'FlowZap.exe'}")
    else:
        print("  [!] Exe не собран — проверьте PyInstaller")
    print(f"{'='*50}\n")

    input("  Нажмите Enter для выхода...")


if __name__ == "__main__":
    main()
