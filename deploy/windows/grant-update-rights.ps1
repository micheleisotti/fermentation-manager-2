# Run ONCE in an administrator PowerShell. Lets one Windows account start/stop/restart ONLY the
# "fm2" service (no other admin rights), so update.ps1 can run from a normal shell — e.g. by Claude
# through the desktop app while the screen is locked.
#
#   powershell -ExecutionPolicy Bypass -File C:\FermentationManager\deploy\windows\grant-update-rights.ps1 -User isott
param([Parameter(Mandatory = $true)][string]$User)

$ErrorActionPreference = "Stop"
$sid = (New-Object System.Security.Principal.NTAccount($User)).Translate([System.Security.Principal.SecurityIdentifier]).Value
$sd = (sc.exe sdshow fm2 | Where-Object { $_ -match "D:" }).Trim()
if (-not $sd) { throw "Service fm2 not found" }
if ($sd -match [regex]::Escape($sid)) { Write-Host "$User already has rights on fm2: nothing to do."; exit 0 }
# RP start, WP stop, DT pause, LO interrogate, CR user-defined control, LC query status, RC read
$ace = "(A;;RPWPDTLOCRLCRC;;;$sid)"
if ($sd -match "S:") { $new = $sd -replace "S:", "${ace}S:" } else { $new = "$sd$ace" }
sc.exe sdset fm2 $new | Out-Null
if ($LASTEXITCODE) { throw "sc sdset failed" }
Write-Host "$User can now start/stop/restart the fm2 service (and nothing else)."
