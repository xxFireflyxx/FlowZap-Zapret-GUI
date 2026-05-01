@echo off
echo ============================================
echo  FlowZap — сборка exe
echo ============================================

:: Проверяем что PyInstaller установлен
python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo [!] PyInstaller не найден. Устанавливаем...
    pip install pyinstaller
)

:: Проверяем зависимости
echo [*] Проверка зависимостей...
pip install -r requirements.txt --quiet

:: Очищаем прошлую сборку
if exist "dist\FlowZap.exe" del /f "dist\FlowZap.exe"
if exist "build" rmdir /s /q build

:: Собираем
echo [*] Сборка...
python -m PyInstaller FlowZap.spec

if errorlevel 1 (
    echo.
    echo [!] Ошибка сборки! Проверьте вывод выше.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  Готово! Файл: dist\FlowZap.exe
echo  
echo  ВАЖНО: папку zapret\ скопируйте рядом с exe
echo  Структура должна быть:
echo    dist\
echo      FlowZap.exe
echo      zapret\        ^<-- скопируйте сюда
echo        bin\
echo        utils\
echo        *.bat
echo ============================================
pause
