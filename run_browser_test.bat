@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Запуск веб-интерфейса и тестов VideoHook в браузере (http://127.0.0.1:8765)...
python browser_test.py
pause
