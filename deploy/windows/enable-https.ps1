# HTTPS for the installable phone app, through Tailscale ("tailscale serve"): the PC gets a
# certificate for its private Tailscale name, e.g. https://lab-pc.tail1234.ts.net,
# reachable only by devices in your tailnet. Nothing is exposed to the public internet.
#
# Before running, once, in the Tailscale admin console (https://login.tailscale.com/admin/dns):
#   - MagicDNS: enabled
#   - HTTPS Certificates: enabled
# Then, in an administrator PowerShell on the lab PC:
#   powershell -ExecutionPolicy Bypass -File C:\FermentationManager\deploy\windows\enable-https.ps1
# To turn it off:  & "C:\Program Files\Tailscale\tailscale.exe" serve reset
param([int]$Port = 8000)

$ErrorActionPreference = "Stop"
$ts = "$env:ProgramFiles\Tailscale\tailscale.exe"
if (-not (Test-Path $ts)) { throw "Tailscale not found at $ts" }

$st = & $ts status --json | ConvertFrom-Json
$dns = $st.Self.DNSName.TrimEnd(".")
if (-not $dns) { throw "No Tailscale DNS name: enable MagicDNS in the admin console." }
if (-not $st.CertDomains -or $st.CertDomains.Count -eq 0) {
  throw "HTTPS certificates are not enabled for this tailnet: enable them at https://login.tailscale.com/admin/dns, then run this again."
}

Write-Host "Configuring https://$dns -> http://127.0.0.1:$Port ..."
& $ts serve --bg $Port
if ($LASTEXITCODE) { throw "tailscale serve failed" }
& $ts serve status

Write-Host "`nTesting (the first request also obtains the certificate, it can take ~30 s)..."
try {
  $r = Invoke-RestMethod "https://$dns/api/system" -TimeoutSec 60
  Write-Host "OK: Fermentation Manager $($r.version) answers at https://$dns" -ForegroundColor Green
} catch {
  Write-Host "Not answering yet ($($_.Exception.Message)). Try https://$dns from the phone in a minute." -ForegroundColor Yellow
}
Write-Host "`nOn the phone (Tailscale on): open https://$dns , sign in, then"
Write-Host "  Android/Chrome: menu > Install app (or Add to home screen)"
Write-Host "  iPhone/Safari:  Share > Add to Home Screen"
