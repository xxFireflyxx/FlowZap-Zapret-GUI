@echo off
chcp 65001 >nul
title FlowZap

echo Проверка Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ОШИБКА] Python не найден.
    echo Скачай и установи Python 3.11+ с https://python.org
    echo При установке обязательно поставь галочку "Add Python to PATH"
    echo.
    pause
    exit /b 1
)

echo Установка зависимостей...
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo.
    echo [ОШИБКА] Не удалось установить зависимости.
    echo Попробуй запустить от имени администратора.
    echo.
    pause
    exit /b 1
)

echo Запуск FlowZap...
python main.py
