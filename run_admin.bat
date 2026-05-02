@echo off
chcp 65001 >nul

:: Проверяем — уже запущены от админа?
net session >nul 2>&1
if %errorlevel% == 0 (
    :: Уже администратор
    cd /d "%~dp0"

    :: Проверяем Python
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

    :: Устанавливаем зависимости
    echo Проверка зависимостей...
    pip install -r requirements.txt --quiet

    :: Запускаем
    echo Запуск FlowZap...
    python main.py
    exit /b
)

:: Не администратор — перезапускаем себя через UAC (один раз)
powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs -Wait"
