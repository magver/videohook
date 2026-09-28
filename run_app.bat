@echo off
chcp 65001 > nul
title VideoHook — AI Генератор вирусных клипов 9:16
cd /d "%~dp0"

echo ========================================================
echo   🎬 VideoHook — AI Генератор клипов 9:16 (Shorts/Reels)
echo ========================================================
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ОШИБКА] Python не найден в системе.
    echo Пожалуйста, установите Python с python.org и поставьте галочку 'Add Python to PATH'.
    pause
    exit /b 1
)

echo [1/2] Проверка зависимостей...
python -m pip install -r requirements.txt --quiet

echo [2/2] Запуск приложения...
python main.py
if %errorlevel% neq 0 (
    echo.
    echo [ВНИМАНИЕ] Программа завершилась с ошибкой.
    pause
)
