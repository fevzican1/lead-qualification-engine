param(
    [Parameter(Mandatory = $true)]
    [string]$Ip,
    [string]$User = "ubuntu",
    [string]$Key = "",
    [string]$AppDir = "/opt/devsolve",
    [switch]$SkipSetup
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Ssh = "C:\Windows\System32\OpenSSH\ssh.exe"
$Scp = "C:\Windows\System32\OpenSSH\scp.exe"

if (-not $Key) {
    $candidates = @(
        (Join-Path $env:USERPROFILE "Downloads\ssh-key-2026-08-22 (2).key"),
        (Join-Path $env:USERPROFILE "Downloads\ssh-key-2026-08-22 (1).key"),
        (Join-Path $env:USERPROFILE "Downloads\ssh-key-2026-08-22.key")
    )
    $Key = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $Key -or -not (Test-Path $Key)) {
    throw "SSH private key not found. Pass -Key path."
}

# OpenSSH on Windows refuses a key that other accounts can read.
icacls $Key /inheritance:r | Out-Null
icacls $Key /grant:r "${env:USERNAME}:(R)" | Out-Null

$sshArgs = @(
    "-i", $Key,
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "IdentitiesOnly=yes",
    "-o", "ConnectTimeout=20"
)

Write-Host "Checking SSH $User@$Ip ..."
& $Ssh @sshArgs "${User}@${Ip}" "uname -a && df -h / && free -h"
if ($LASTEXITCODE -ne 0) {
    throw "SSH failed. Check Public IP, security list port 22, and that this key matches the instance."
}

$stage = Join-Path $env:TEMP "devsolve-oracle"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Path $stage | Out-Null

$include = @(
    "auto_runner.py", "collector.py", "config.py", "form_submitter.py",
    "lead_finder.py", "ollama_client.py", "pipeline.py", "site_signals.py",
    "prefilter.py", "browser.py", "knowledge.py",
    "domain_store.py", "lead_discovery.py", "pacing.py", "risk_guard.py",
    "qualification_analyzer.py", "telegram_sales_bot.py", "telegram_handoff.py",
    "telegram_sessions.py", "proof_card.py",
    "easy_score.py", "feed_ingest.py", "stack_fingerprint.py",
    "optout.py", "owner_notify.py", "bounded_agents.py", "target_pool.py",
    "requirements.txt", "targets.txt", ".env", ".env.example",
    "Dockerfile", "docker-compose.yml"
)
foreach ($name in $include) {
    $src = Join-Path $Root $name
    if (Test-Path $src) { Copy-Item $src $stage }
}
$knowledgeDir = Join-Path $Root "knowledge"
if (Test-Path $knowledgeDir) {
    Copy-Item $knowledgeDir (Join-Path $stage "knowledge") -Recurse -Force
}
$feedsDir = Join-Path $Root "feeds"
if (Test-Path $feedsDir) {
    Copy-Item $feedsDir (Join-Path $stage "feeds") -Recurse -Force
}
$setupSrc = Join-Path $Root "scripts\oracle-setup.sh"
$setupText = [IO.File]::ReadAllText($setupSrc) -replace "`r`n", "`n" -replace "`r", "`n"
[IO.File]::WriteAllText((Join-Path $stage "oracle-setup.sh"), $setupText, (New-Object System.Text.UTF8Encoding $false))
if (Test-Path (Join-Path $Root "optouts.json")) {
    Copy-Item (Join-Path $Root "optouts.json") $stage
}

$archive = Join-Path $env:TEMP "devsolve-oracle.tgz"
if (Test-Path $archive) { Remove-Item $archive -Force }
Push-Location $stage
try {
    & tar.exe -czf $archive .
} finally {
    Pop-Location
}

Write-Host "Uploading project ..."
& $Scp @sshArgs $archive "${User}@${Ip}:/tmp/devsolve-oracle.tgz"
if ($LASTEXITCODE -ne 0) { throw "scp failed" }

$extract = "sudo mkdir -p $AppDir && sudo tar -xzf /tmp/devsolve-oracle.tgz -C $AppDir && sudo chown -R ${User}:${User} $AppDir && mkdir -p $AppDir/scripts && if [ -f $AppDir/oracle-setup.sh ]; then mv -f $AppDir/oracle-setup.sh $AppDir/scripts/oracle-setup.sh; fi && sed -i 's/\r$//' $AppDir/scripts/oracle-setup.sh && chmod +x $AppDir/scripts/oracle-setup.sh"
if ($SkipSetup) {
    $clamp = "sed -i 's|^OLLAMA_HOST=.*|OLLAMA_HOST=http://127.0.0.1:11434|' $AppDir/.env || true; if grep -q '^PLAYWRIGHT_BROWSERS_PATH=' $AppDir/.env; then sed -i 's|^PLAYWRIGHT_BROWSERS_PATH=.*|PLAYWRIGHT_BROWSERS_PATH=$AppDir/.playwright|' $AppDir/.env; else printf '\nPLAYWRIGHT_BROWSERS_PATH=$AppDir/.playwright\n' >> $AppDir/.env; fi; grep -q '^HOURLY_SUBMIT_LIMIT=' $AppDir/.env && sed -i 's|^HOURLY_SUBMIT_LIMIT=.*|HOURLY_SUBMIT_LIMIT=32|' $AppDir/.env || printf '\nHOURLY_SUBMIT_LIMIT=32\n' >> $AppDir/.env; grep -q '^DAILY_SUBMIT_LIMIT=' $AppDir/.env && sed -i 's|^DAILY_SUBMIT_LIMIT=.*|DAILY_SUBMIT_LIMIT=300|' $AppDir/.env || printf '\nDAILY_SUBMIT_LIMIT=300\n' >> $AppDir/.env; grep -q '^QUEUE_MAX=' $AppDir/.env && sed -i 's|^QUEUE_MAX=.*|QUEUE_MAX=1500|' $AppDir/.env || printf '\nQUEUE_MAX=1500\n' >> $AppDir/.env; grep -q '^QUEUE_TARGET=' $AppDir/.env && sed -i 's|^QUEUE_TARGET=.*|QUEUE_TARGET=400|' $AppDir/.env || printf '\nQUEUE_TARGET=400\n' >> $AppDir/.env; grep -q '^CHROMIUM_BATCH=' $AppDir/.env && sed -i 's|^CHROMIUM_BATCH=.*|CHROMIUM_BATCH=32|' $AppDir/.env || printf '\nCHROMIUM_BATCH=32\n' >> $AppDir/.env; grep -q '^FEED_MIN_SCORE=' $AppDir/.env && sed -i 's|^FEED_MIN_SCORE=.*|FEED_MIN_SCORE=80|' $AppDir/.env || printf '\nFEED_MIN_SCORE=80\n' >> $AppDir/.env; grep -q '^PIPELINE_TIMEOUT_SECONDS=' $AppDir/.env && sed -i 's|^PIPELINE_TIMEOUT_SECONDS=.*|PIPELINE_TIMEOUT_SECONDS=180|' $AppDir/.env || printf '\nPIPELINE_TIMEOUT_SECONDS=180\n' >> $AppDir/.env"
    $clamp += "; grep -q '^PIPELINE_TIMEOUT_SECONDS=' $AppDir/.env && sed -i 's|^PIPELINE_TIMEOUT_SECONDS=.*|PIPELINE_TIMEOUT_SECONDS=30|' $AppDir/.env || printf '\nPIPELINE_TIMEOUT_SECONDS=30\n' >> $AppDir/.env; grep -q '^SITE_TIMEOUT_SECONDS=' $AppDir/.env && sed -i 's|^SITE_TIMEOUT_SECONDS=.*|SITE_TIMEOUT_SECONDS=20|' $AppDir/.env || printf '\nSITE_TIMEOUT_SECONDS=20\n' >> $AppDir/.env; grep -q '^FORM_DELAY_STRICT_MIN_SECONDS=' $AppDir/.env && sed -i 's|^FORM_DELAY_STRICT_MIN_SECONDS=.*|FORM_DELAY_STRICT_MIN_SECONDS=8|' $AppDir/.env || printf '\nFORM_DELAY_STRICT_MIN_SECONDS=8\n' >> $AppDir/.env; grep -q '^FORM_DELAY_STRICT_MAX_SECONDS=' $AppDir/.env && sed -i 's|^FORM_DELAY_STRICT_MAX_SECONDS=.*|FORM_DELAY_STRICT_MAX_SECONDS=12|' $AppDir/.env || printf '\nFORM_DELAY_STRICT_MAX_SECONDS=12\n' >> $AppDir/.env"
    $clamp += "; grep -q '^DAILY_SUBMIT_LIMIT=' $AppDir/.env && sed -i 's|^DAILY_SUBMIT_LIMIT=.*|DAILY_SUBMIT_LIMIT=400|' $AppDir/.env || printf '\nDAILY_SUBMIT_LIMIT=400\n' >> $AppDir/.env"
    $pip = "cd $AppDir && .venv/bin/pip install -q pillow"
    $remote = "$extract && $clamp && $pip && sudo systemctl restart devsolve-bot.service devsolve-runner.service && systemctl is-active devsolve-bot.service devsolve-runner.service"
    Write-Host "Uploading code and restarting services (no apt, no Ollama pull, no shape change) ..."
} else {
    $remote = "$extract && sudo APP_DIR=$AppDir bash $AppDir/scripts/oracle-setup.sh"
    Write-Host "Installing on the VM (Ollama + Python + systemd). This can take 10-20 minutes ..."
}
& $Ssh @sshArgs "${User}@${Ip}" $remote
if ($LASTEXITCODE -ne 0) { throw "Remote setup failed" }

Write-Host "Deploy finished. Bot and auto_runner should be running on the VM."
