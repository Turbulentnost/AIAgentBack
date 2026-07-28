# Проброс портов ESKD Agent: WSL2 -> Windows LAN (запуск от администратора).
param(
    [Parameter(Mandatory = $true)]
    [string]$WslIp,
    [string]$Ports = "8000,8080,8765,3000,5173"
)

[int[]]$PortList = $Ports -split ',' | ForEach-Object { [int]$_.Trim() }

$ErrorActionPreference = "Stop"

function Require-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Error "Запустите PowerShell от имени администратора."
        exit 1
    }
}

function Ensure-FirewallRule([int]$Port) {
    $ruleName = "ESKD Agent TCP $Port"
    netsh advfirewall firewall delete rule name="$ruleName" 2>$null | Out-Null
    netsh advfirewall firewall add rule name="$ruleName" dir=in action=allow protocol=TCP localport=$Port profile=any enable=yes | Out-Null
}

Require-Admin

# portproxy требует iphlpsvc
$iphlp = Get-Service iphlpsvc -ErrorAction SilentlyContinue
if ($iphlp -and $iphlp.Status -ne 'Running') {
    Start-Service iphlpsvc
    Write-Host "Служба iphlpsvc запущена (нужна для portproxy)"
}

Write-Host "WSL IP: $WslIp"
Write-Host "Проброс 0.0.0.0 -> WSL..."

foreach ($port in $PortList) {
    netsh interface portproxy delete v4tov4 listenport=$port listenaddress=0.0.0.0 2>$null | Out-Null
    netsh interface portproxy delete v4tov4 listenport=$port listenaddress=127.0.0.1 2>$null | Out-Null
    netsh interface portproxy add v4tov4 listenport=$port listenaddress=0.0.0.0 connectaddress=$WslIp connectport=$port | Out-Null
    Ensure-FirewallRule -Port $port
    Write-Host "  OK  :$port -> ${WslIp}:$port + firewall"
}

Write-Host ""
Write-Host "=== portproxy ==="
netsh interface portproxy show v4tov4

Write-Host ""
Write-Host "=== Откройте с ДРУГОГО ПК (та же Wi-Fi, не гостевая сеть) ==="
$lanIps = @()
$primary = & "$PSScriptRoot/get-lan-ip.ps1" 2>$null
if ($primary) { $lanIps = @($primary) }
if (-not $lanIps) {
    $lanIps = Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object {
            $_.IPAddress -match '^192\.168\.\d+\.\d+$' -and
            $_.IPAddress -ne '192.168.56.1' -and
            $_.IPAddress -notlike '192.168.137.*'
        } |
        Select-Object -ExpandProperty IPAddress
}

if (-not $lanIps) {
    $lanIps = @('192.168.2.61')
}

$uiPort = if ($PortList -contains 8000) { 8000 } else { $PortList[0] }
foreach ($ip in $lanIps) {
    Write-Host ""
    Write-Host "  UI:  http://${ip}:${uiPort}/"
    Write-Host "  API: http://${ip}:8080/health"
    if ($PortList -contains 5173) {
        Write-Host "  Vite (AIAgentFront dev): http://${ip}:5173/"
    }
}

Write-Host ""
Write-Host "НЕ используйте localhost с другого ПК. НЕ используйте :3000 если portproxy для 3000 не добавлен — предпочтите :8000"
