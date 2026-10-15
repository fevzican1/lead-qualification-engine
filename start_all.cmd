@echo off
REM Nirvana Whitelabel - Windows gelistirme baslatici (tmux karsiligi).
REM Manifest kapisi: kritik kapilar FALSE ise servisler baslatilmaz.
cd /d C:\Users\Lenovo\lead-qualification-engine-2
set PLAYWRIGHT_BROWSERS_PATH=C:\Users\Lenovo\lead-qualification-engine-2\.playwright
set NODE_OPTIONS=--use-system-ca
REM Python secimi: .venv varsa o, yoksa sistem python'u (.env ortak okunur).
set PY=C:\Users\Lenovo\lead-qualification-engine-2\.venv\Scripts\python.exe
if not exist "%PY%" set PY=python
echo [1/2] Manifest kapisi (dev toleransli)...
"%PY%" "C:\Users\Lenovo\lead-qualification-engine-2\manifest_check.py"
if errorlevel 1 (
  echo MANIFEST FALSE - servisler baslatilmadi. manifest_report.json bak.
  pause
  exit /b 1
)
where ollama >nul 2>&1 && start "Ollama" /MIN ollama serve
timeout /t 3 /nobreak >nul
start "DevSolve Webchat" /MIN "%PY%" -m uvicorn webchat_server:app --host 127.0.0.1 --port 8765
start "DevSolve Telegram Bot" /MIN "%PY%" "C:\Users\Lenovo\lead-qualification-engine-2\telegram_sales_bot.py"
start "DevSolve Auto Runner" /MIN "%PY%" "C:\Users\Lenovo\lead-qualification-engine-2\auto_runner.py"
echo Tum servisler manifest TRUE ile baslatildi.
