$ErrorActionPreference = "SilentlyContinue"
$root = "C:\Users\Lenovo\lead-qualification-engine"
$py = Join-Path $root ".venv\Scripts\python.exe"

function Test-ScriptRunning([string]$scriptName) {
    $hit = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object { $_.CommandLine -and ($_.CommandLine -like "*$scriptName*") }
    return [bool]$hit
}

function Start-Script([string]$scriptName) {
    if (Test-ScriptRunning $scriptName) {
        return
    }
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $root ".playwright"
    $env:NODE_OPTIONS = "--use-system-ca"
    Start-Process -FilePath $py -ArgumentList "`"$root\$scriptName`"" -WorkingDirectory $root -WindowStyle Minimized
}

if (-not (Test-Path $py)) { exit 0 }
$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollama -and -not (Get-Process -Name "ollama" -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath $ollama.Source -ArgumentList "serve" -WindowStyle Minimized
}
Start-Script "telegram_sales_bot.py"
Start-Script "auto_runner.py"
