#Requires -RunAsAdministrator
# Stops and removes the Fermentation Manager 2 service. Data in C:\ProgramData\FermentationManager is NOT touched.
$WinSW = Join-Path $PSScriptRoot "fm2-service.exe"
& $WinSW stop
& $WinSW uninstall
