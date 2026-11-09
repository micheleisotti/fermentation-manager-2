#Requires -RunAsAdministrator
<#
  Installs Fermentation Manager 2 as a Windows service that starts at boot with nobody logged in.
  Runs under the virtual account "NT SERVICE\fm2" (dedicated, no password, minimal rights).
  Run from an elevated PowerShell:  C:\FermentationManager\deploy\windows\install-service.ps1
#>
$ErrorActionPreference = "Stop"
$Root    = "C:\FermentationManager"
$Data    = "C:\ProgramData\FermentationManager"
$Here    = $PSScriptRoot
$WinSW   = Join-Path $Here "fm2-service.exe"
$Account = "NT SERVICE\fm2"

# Pre-flight checks
if (-not (Test-Path "$Root\.venv\Scripts\fm2-server.exe")) { throw "fm2-server.exe not found: run 'pip install -e .' in $Root first." }
if (-not (Test-Path "$Root\frontend\dist\index.html"))   { throw "frontend\dist missing: run 'npm.cmd run build' in $Root\frontend first." }
if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) { throw "Port 8000 is in use: stop the fm2-server running in PowerShell (Ctrl+C) first." }
if (Get-Service fm2 -ErrorAction SilentlyContinue) { throw "Service 'fm2' already exists. Use uninstall-service.ps1 first to reinstall." }

# Data and log folders
New-Item -ItemType Directory -Force "$Data\data", "$Data\logs" | Out-Null

# WinSW wrapper (single exe, downloaded once)
if (-not (Test-Path $WinSW)) {
    Write-Host "Downloading WinSW v2.12.0..."
    Invoke-WebRequest "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe" -OutFile $WinSW
}

# Install, then switch to the dedicated virtual account
& $WinSW install
sc.exe config fm2 obj= "$Account" | Out-Null

# Permissions: data writable, code and Python readable, nothing else
icacls $Data /grant "${Account}:(OI)(CI)M" | Out-Null
icacls $Root /grant "${Account}:(OI)(CI)RX" | Out-Null
$PyBase = & "$Root\.venv\Scripts\python.exe" -c "import sys; print(sys.base_prefix)"
icacls $PyBase /grant "${Account}:(OI)(CI)RX" | Out-Null

# Start and check
& $WinSW start
Start-Sleep -Seconds 8
try {
    $r = Invoke-WebRequest "http://localhost:8000/docs" -UseBasicParsing -TimeoutSec 10
    Write-Host "OK: service running, http://localhost:8000 answers ($($r.StatusCode))." -ForegroundColor Green
} catch {
    Write-Warning "Service installed but http://localhost:8000 does not answer yet. Check the logs in $Data\logs."
}
Get-Service fm2 | Format-Table Status, Name, DisplayName, StartType
