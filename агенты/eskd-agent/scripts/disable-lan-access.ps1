# Удалить проброс портов ESKD Agent (запуск от администратора).
param(
    [int[]]$Ports = @(8000, 3000, 8080, 8765)
)

$ErrorActionPreference = "Stop"

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$p = New-Object Security.Principal.WindowsPrincipal($id)
if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Запустите PowerShell от имени администратора."
    exit 1
}

foreach ($port in $Ports) {
    netsh interface portproxy delete v4tov4 listenport=$port listenaddress=0.0.0.0 2>$null | Out-Null
    netsh advfirewall firewall delete rule name="ESKD Agent TCP $port" 2>$null | Out-Null
    Write-Host "Removed :$port"
}

netsh interface portproxy show v4tov4
