@echo off
chcp 65001 >nul

rem Проверяем - уже запущены от админа?
rem fltmc работает только с правами администратора и не зависит от службы Сервер,
rem в отличие от net session: если эта служба отключена, net session всегда даёт
rem ошибку, и скрипт бесконечно просил бы UAC.
fltmc >nul 2>&1
if %errorlevel% == 0 (
    rem Уже администратор
    cd /d "%~dp0"

    rem Проверяем Python
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

    rem Устанавливаем зависимости
    echo Проверка зависимостей...
    pip install -r requirements.txt --quiet

    rem Запускаем
    echo Запуск FlowZap...
    python main.py
    if errorlevel 1 (
        echo.
        echo [ОШИБКА] FlowZap завершился с ошибкой - текст выше.
        echo.
        pause
    )
    exit /b
)

rem Права уже запрашивали, но получить не вышло - не зацикливаемся
if "%~1" == "elevated" (
    echo.
    echo [ОШИБКА] Не удалось получить права администратора.
    echo.
    pause
    exit /b 1
)

rem Не администратор - перезапускаем себя через UAC, один раз
echo Запрашиваю права администратора...
rem The path goes through a variable and is passed to cmd in quotes. A folder name
rem with a caret or an ampersand breaks an unquoted path and the admin window dies silently.
set "FZ_BAT=%~f0"
powershell -NoProfile -Command "$q=[char]34; Start-Process -FilePath 'cmd.exe' -ArgumentList ('/c ' + $q + $q + $env:FZ_BAT + $q + ' elevated' + $q) -Verb RunAs -Wait"
if errorlevel 1 (
    echo.
    echo [ОШИБКА] Запуск от администратора отменён или не удался.
    echo.
    pause
)
