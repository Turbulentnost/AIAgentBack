# Полный LAN-доступ: portproxy + отключение Windows Firewall (запуск от администратора).
param(
    [Parameter(Mandatory = $true)]
    [string]$WslIp,
    [string]$Ports = "8000,8080,8765,3000,5173"
)

$ErrorActionPreference = "Stop"

function Require-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Error "Нужны права администратора."
        exit 1
    }
}

Require-Admin

[int[]]$PortList = $Ports -split ',' | ForEach-Object { [int]$_.Trim() }

Write-Host "=== Отключение Windows Firewall (все профили) ==="
Set-NetFirewallProfile -Profile Domain, Public, Private -Enabled False
netsh advfirewall set allprofiles state off | Out-Null
Get-NetFirewallProfile | Select-Object Name, Enabled | Format-Table

$iphlp = Get-Service iphlpsvc -ErrorAction SilentlyContinue
if ($iphlp -and $iphlp.Status -ne 'Running') {
    Start-Service iphlpsvc
}

Write-Host ""
Write-Host "=== Portproxy WSL -> LAN (0.0.0.0) ==="
Write-Host "WSL IP: $WslIp"

foreach ($port in $PortList) {
    netsh interface portproxy delete v4tov4 listenport=$port listenaddress=0.0.0.0 2>$null | Out-Null
    netsh interface portproxy add v4tov4 listenport=$port listenaddress=0.0.0.0 connectaddress=$WslIp connectport=$port | Out-Null
    Write-Host "  OK  :$port -> ${WslIp}:$port"
}

netsh interface portproxy show v4tov4

Write-Host ""
Write-Host "=== LAN URL ==="
$lanIp = & "$PSScriptRoot/get-lan-ip.ps1" 2>$null
if (-not $lanIp) {
    $lanIp = (Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object {
            $_.IPAddress -match '^192\.168\.' -and
            $_.IPAddress -ne '192.168.56.1' -and
            $_.IPAddress -notlike '192.168.137.*' -and
            $_.InterfaceAlias -notmatch 'Radmin|VirtualBox|VMware'
        } | Select-Object -First 1 -ExpandProperty IPAddress)
}
if (-not $lanIp) { $lanIp = "192.168.2.120" }

Write-Host "  http://${lanIp}:8000/"
Write-Host ""
Write-Host "Firewall OFF. С другого ПК в той же Wi-Fi откройте URL выше."
