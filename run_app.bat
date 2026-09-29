@echo off
chcp 65001 > nul
title VideoHook
cd /d "%~dp0"

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ОШИБКА] Python не найден. Установите Python 3.10+ с python.org ^(галочка "Add Python to PATH"^).
    pause
    exit /b 1
)

echo [1/2] Проверка зависимостей...
python -m pip install -r requirements.txt --quiet --upgrade
echo [2/2] Запуск VideoHook...
start "" pythonw main.py
