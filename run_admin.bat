@echo off
:: Проверяем — уже запущены от админа?
net session >nul 2>&1
if %errorlevel% == 0 (
    :: Уже администратор — просто запускаем
    cd /d "%~dp0"
    python main.py
    exit /b
)

:: Не администратор — перезапускаем себя через UAC (один раз)
powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs -Wait"
