# Update Fermentation Manager on the lab PC in one step:
#   git pull (main) -> pip install if dependencies changed (service stopped first) ->
#   npm install if frontend packages changed -> frontend build -> restart the "fm2" service -> health check.
#
#   powershell -ExecutionPolicy Bypass -File C:\FermentationManager\deploy\windows\update.ps1
#
# If an experiment is recording, the script stops and asks (the restart takes a few seconds:
# recording resumes by itself, with a gap of a few readings). Use -Force to skip the question
# (non-interactive use). Needs either an administrator shell or the right to start/stop the fm2
# service, granted once with grant-update-rights.ps1.
param([switch]$Force, [string]$Url = "http://localhost:8000", [string]$Branch = "main")

$ErrorActionPreference = "Stop"
$repo = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $repo
function Step($t) { Write-Host "`n== $t" -ForegroundColor Cyan }

Step "Recording check"
try {
  $live = Invoke-RestMethod "$Url/api/reactors/live" -TimeoutSec 10
  $rec = @($live | Where-Object { $_.experiment })
} catch { $rec = @(); Write-Host "Service not answering ($($_.Exception.Message)): continuing." }
if ($rec.Count -gt 0) {
  $list = ($rec | ForEach-Object { "$($_.experiment.code) on $($_.reactor_id)" }) -join ", "
  Write-Host "Recording now: $list" -ForegroundColor Yellow
  if (-not $Force) {
    if (-not [Environment]::UserInteractive -or [Console]::IsInputRedirected) { throw "Experiments are recording: run again with -Force to update anyway." }
    $a = Read-Host "Restart the service anyway? A few readings will be missing (y/N)"
    if ($a -notin @("y", "Y", "yes", "s", "si")) { Write-Host "Update cancelled."; exit 1 }
  }
}

# Controller addresses live in local.env (data folder), not in the repository (2.2.1).
# If it is missing, it is created once from the addresses in the current service file.
$localEnv = "C:\ProgramData\FermentationManager\data\local.env"
if (-not (Test-Path $localEnv)) {
  $xml = Join-Path $PSScriptRoot "fm2-service.xml"
  $found = @(Select-String -Path $xml -Pattern '<env name="(FM2_R\d_ADDRESS)" value="([^"]+)"' -AllMatches |
             ForEach-Object { $_.Matches } | ForEach-Object { "$($_.Groups[1].Value)=$($_.Groups[2].Value)" })
  if ($found.Count -gt 0) {
    Set-Content -Path $localEnv -Value (@("# Site-specific settings (not in the repository)") + $found) -Encoding UTF8
    Write-Host "Controller addresses moved to $localEnv" -ForegroundColor Yellow
  } else {
    Write-Host "WARNING: $localEnv not found: the controllers have no address (see deploy\windows\local.env.example)." -ForegroundColor Yellow
  }
}

Step "Git pull (main)"
$old = (git rev-parse HEAD).Trim()
git fetch origin
git checkout $Branch
if ($LASTEXITCODE) { throw "git checkout $Branch failed (local changes?)" }
git pull --ff-only origin $Branch
if ($LASTEXITCODE) { throw "git pull failed" }
$new = (git rev-parse HEAD).Trim()
$changed = @()
if ($old -ne $new) { $changed = @(git diff --name-only $old $new) }
Write-Host ("{0} -> {1}, {2} files changed" -f $old.Substring(0, 7), $new.Substring(0, 7), $changed.Count)
$depsChanged = [bool]($changed | Where-Object { $_ -eq "pyproject.toml" })
$npmChanged = [bool]($changed | Where-Object { $_ -in @("frontend/package.json", "frontend/package-lock.json") })
$frontChanged = [bool]($changed | Where-Object { $_ -like "frontend/*" }) -or -not (Test-Path "frontend\dist\index.html")
# also rebuild when the build is older than the last frontend commit (e.g. branch checked out by hand before running this)
if (-not $frontChanged) {
  $lastFront = [int](git log -1 --format=%ct -- frontend)
  $built = [int][double]::Parse((Get-Date (Get-Item "frontend\dist\index.html").LastWriteTimeUtc -UFormat %s))
  if ($built -lt $lastFront) { $frontChanged = $true }
}

if ($depsChanged) {
  Step "Python dependencies changed: stopping the service and installing"
  Stop-Service fm2
  & .\.venv\Scripts\pip.exe install -e .
  if ($LASTEXITCODE) { Start-Service fm2; throw "pip install failed (service restarted with the previous packages)" }
}
if ($npmChanged) {
  Step "Frontend packages changed: npm install"
  Push-Location frontend; npm.cmd install; $rc = $LASTEXITCODE; Pop-Location
  if ($rc) { throw "npm install failed" }
}
if ($frontChanged) {
  Step "Frontend build"
  Push-Location frontend; npm.cmd run build; $rc = $LASTEXITCODE; Pop-Location
  if ($rc) { throw "frontend build failed" }
}

Step "Restarting the fm2 service"
if ((Get-Service fm2).Status -eq "Running") { Restart-Service fm2 } else { Start-Service fm2 }
$ok = $false
for ($i = 0; $i -lt 30; $i++) {
  Start-Sleep -Seconds 2
  try { $sys = Invoke-RestMethod "$Url/api/system" -TimeoutSec 5; $ok = $true; break } catch { }
}
if (-not $ok) { throw "The service did not answer within 60 s: see C:\ProgramData\FermentationManager\logs" }
Write-Host ("`nUpdated and running: version {0}, commit {1}." -f $sys.version, $new.Substring(0, 7)) -ForegroundColor Green
