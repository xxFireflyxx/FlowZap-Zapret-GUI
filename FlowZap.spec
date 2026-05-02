# -*- mode: python ; coding: utf-8 -*-
"""
FlowZap.spec — PyInstaller spec файл
Сборка: pyinstaller FlowZap.spec
"""

import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Собираем данные customtkinter (темы, шрифты, изображения)
ctk_datas = collect_data_files("customtkinter")

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=[
        # customtkinter — обязательно, иначе темы не найдёт
        *ctk_datas,
        # config.toml рядом с exe
        ("config.toml", "."),
    ],
    hiddenimports=[
        "customtkinter",
        "tomllib",
        "tomli_w",
        "requests",
        "PIL",
        "PIL.Image",
        "PIL.ImageTk",
        *collect_submodules("customtkinter"),
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib", "numpy", "pandas", "scipy",
        "tkinter.test", "unittest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="FlowZap",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,               # сжатие (уменьшает размер)
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,           # без консольного окна
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Манифест — запрос прав администратора при запуске
    uac_admin=True,
    uac_uiaccess=False,
    # Иконка (раскомментируйте если добавите .ico файл в assets/)
    # icon="assets/icon.ico",
)
