# Creates a "Fermentation Manager" shortcut on the Desktop and in the Start menu of ALL Windows users
# (Public Desktop), opening http://localhost:8000 in an app window (Edge or Chrome, no address bar).
# The software itself runs as the "fm2" service: the shortcut only opens the interface.
#
# Run once in an administrator PowerShell:
#   powershell -ExecutionPolicy Bypass -File C:\FermentationManager\deploy\windows\create-shortcut.ps1
param([string]$Url = "http://localhost:8000")

$ErrorActionPreference = "Stop"
$icon = Join-Path $PSScriptRoot "fm2.ico"
$browsers = @(
  "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
  "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
)
$browser = $browsers | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

$targets = @(
  (Join-Path $env:PUBLIC "Desktop\Fermentation Manager.lnk"),
  (Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs\Fermentation Manager.lnk")
)
$shell = New-Object -ComObject WScript.Shell
foreach ($path in $targets) {
  $lnk = $shell.CreateShortcut($path)
  if ($browser) {
    $lnk.TargetPath = $browser
    $lnk.Arguments = "--app=$Url"
  } else {
    $lnk.TargetPath = "explorer.exe"     # no Edge/Chrome found: default browser
    $lnk.Arguments = $Url
  }
  $lnk.IconLocation = "$icon,0"
  $lnk.Description = "Fermentation Manager 2 (opens $Url)"
  $lnk.Save()
  Write-Host "Created $path"
}
if ($browser) { Write-Host "Opens in an app window of $browser" } else { Write-Host "Edge/Chrome not found: opens in the default browser" }
