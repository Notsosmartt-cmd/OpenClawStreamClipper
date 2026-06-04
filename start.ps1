#Requires -Version 5
<#
  start.ps1 — Bare-metal Windows launcher for the OpenClaw Stream Clipper.
  Replaces the Docker entrypoint.sh. Starts the Flask dashboard and the
  OpenClaw Discord gateway, both pointed at native LM Studio on localhost.

  Prereqs (one-time):
    - LM Studio running with "Serve on Local Network" on port 1234
    - Python venv:  py -3.12 -m venv .venv
                    .venv\Scripts\python.exe -m pip install torch==2.8.0+cu128 torchaudio==2.8.0+cu128 --index-url https://download.pytorch.org/whl/cu128
                    .venv\Scripts\python.exe -m pip install -r requirements-windows.txt
    - Node + openclaw:  npm install -g openclaw@latest
    - ffmpeg/ffprobe on PATH

  Usage:  powershell -ExecutionPolicy Bypass -File start.ps1
#>
$ErrorActionPreference = "Stop"
$Repo = $PSScriptRoot
$Py = Join-Path $Repo ".venv\Scripts\python.exe"

Write-Host "=== OpenClaw Stream Clipper (bare metal) ==="

if (-not (Test-Path $Py)) {
  Write-Host "ERROR: venv not found at $Py — see prereqs in this script's header." -ForegroundColor Red
  exit 1
}

# OpenClaw config home: ~/.openclaw -> repo config\ (mirrors the Docker
# ./config:/root/.openclaw mount). Directory junctions need no admin rights.
$OcHome = Join-Path $env:USERPROFILE ".openclaw"
if (-not (Test-Path $OcHome)) {
  New-Item -ItemType Junction -Path $OcHome -Target (Join-Path $Repo "config") | Out-Null
  Write-Host "Linked $OcHome -> config\"
}

# Discord token injection: only when openclaw.json still has the placeholder
# AND .env supplies the token (mirrors entrypoint.sh). If the token is already
# inline in openclaw.json this is a no-op.
$OcJson = Join-Path $Repo "config\openclaw.json"
$EnvFile = Join-Path $Repo ".env"
if ((Test-Path $EnvFile) -and (Select-String -Path $OcJson -Pattern "__DISCORD_BOT_TOKEN__" -Quiet)) {
  $line = Get-Content $EnvFile | Where-Object { $_ -match "^DISCORD_BOT_TOKEN=" } | Select-Object -First 1
  $tok = ($line -replace "^DISCORD_BOT_TOKEN=", "").Trim()
  if ($tok) {
    (Get-Content $OcJson -Raw).Replace("__DISCORD_BOT_TOKEN__", $tok) | Set-Content $OcJson -Encoding utf8
    Write-Host "Injected Discord bot token from .env"
  }
}

# Wait for LM Studio.
$LmUrl = "http://localhost:1234"
Write-Host "Waiting for LM Studio at $LmUrl ..."
$ok = $false
for ($i = 0; $i -lt 30; $i++) {
  try { Invoke-RestMethod -Uri "$LmUrl/v1/models" -TimeoutSec 3 | Out-Null; $ok = $true; break }
  catch { Start-Sleep -Seconds 2 }
}
if ($ok) { Write-Host "LM Studio is reachable." -ForegroundColor Green }
else { Write-Host "WARNING: LM Studio not reachable — start it with 'Serve on Local Network'." -ForegroundColor Yellow }

# Dashboard (background) — http://localhost:5001
Write-Host "Starting dashboard on http://localhost:5001 ..."
Start-Process -FilePath $Py -ArgumentList (Join-Path $Repo "dashboard\app.py") -WorkingDirectory $Repo -WindowStyle Hidden

# OpenClaw gateway (foreground; cwd = repo so the `clip.cmd` exec resolves).
Write-Host "Starting OpenClaw gateway (Ctrl+C to stop) ..."
Set-Location $Repo
& openclaw gateway
