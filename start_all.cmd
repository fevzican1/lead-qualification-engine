@echo off
cd /d C:\Users\Lenovo\lead-qualification-engine
set PLAYWRIGHT_BROWSERS_PATH=C:\Users\Lenovo\lead-qualification-engine\.playwright
set NODE_OPTIONS=--use-system-ca
where ollama >nul 2>&1 && start "Ollama" /MIN ollama serve
timeout /t 3 /nobreak >nul
start "DevSolve Telegram Bot" /MIN "C:\Users\Lenovo\lead-qualification-engine\.venv\Scripts\python.exe" "C:\Users\Lenovo\lead-qualification-engine\telegram_sales_bot.py"
start "DevSolve Auto Runner" /MIN "C:\Users\Lenovo\lead-qualification-engine\.venv\Scripts\python.exe" "C:\Users\Lenovo\lead-qualification-engine\auto_runner.py"
