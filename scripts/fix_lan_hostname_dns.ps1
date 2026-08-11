#Requires -Version 5.1
<#
.SYNOPSIS
  Stops Hyper-V/WSL virtual adapters from polluting AD DNS for this PC hostname.

  Symptom: http://ok6:5174 times out while http://192.168.2.91:5174 works, because
  ok6.turbo-don.ru resolves to 172.30.48.1 / 172.28.96.1 (vEthernet) in addition to LAN IP.

.USAGE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\fix_lan_hostname_dns.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipRegisterDns
)

$ErrorActionPreference = "Stop"

$VirtualPatterns = @(
    "vEthernet",
    "WSL",
    "Hyper-V",
    "Default Switch",
    "Loopback",
    "Tailscale",
    "ZeroTier",
    "VirtualBox",
    "VMware"
)

function Test-VirtualAdapter {
    param([string]$Name)
    foreach ($pattern in $VirtualPatterns) {
        if ($Name -like "*$pattern*") { return $true }
    }
    return $false
}

Write-Host "=== Fix LAN hostname DNS (computer: $env:COMPUTERNAME) ===" -ForegroundColor Cyan

$adapters = Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq "Up" -or $_.Status -eq "Disconnected" }
if (-not $adapters) {
    Write-Warning "No network adapters found."
    exit 1
}

foreach ($adapter in $adapters) {
    $isVirtual = Test-VirtualAdapter -Name $adapter.Name
    $dnsClient = Get-DnsClient -InterfaceIndex $adapter.ifIndex -ErrorAction SilentlyContinue
    if (-not $dnsClient) { continue }

    if ($isVirtual -or $adapter.Name -match "Loopback") {
        Set-DnsClient -InterfaceIndex $adapter.ifIndex -RegisterThisConnectionsAddress $false
        Write-Host "[off] DNS registration disabled: $($adapter.Name)" -ForegroundColor Yellow
        continue
    }

    Set-DnsClient -InterfaceIndex $adapter.ifIndex -RegisterThisConnectionsAddress $true
    Write-Host "[on]  DNS registration enabled:  $($adapter.Name)" -ForegroundColor Green
}

if (-not $SkipRegisterDns) {
    Write-Host ""
    Write-Host "Re-registering DNS (ipconfig /registerdns)..." -ForegroundColor Cyan
    & ipconfig /registerdns | Out-Null
}

Write-Host ""
Write-Host "Current A records for $env:COMPUTERNAME :" -ForegroundColor Cyan
try {
    Resolve-DnsName "$env:COMPUTERNAME" -Type A -ErrorAction Stop |
        Select-Object Name, IPAddress |
        Format-Table -AutoSize
}
catch {
    Write-Warning "Could not resolve short name $env:COMPUTERNAME (DNS suffix may be required)."
}

try {
    Resolve-DnsName "$env:COMPUTERNAME.turbo-don.ru" -Type A -ErrorAction Stop |
        Select-Object Name, IPAddress |
        Format-Table -AutoSize
}
catch {
    Write-Warning "Could not resolve FQDN."
}

Write-Host ""
Write-Host "If stale 172.30.* / 172.28.* records remain, wait ~20 min (TTL) or ask IT to delete them in AD DNS." -ForegroundColor Yellow
Write-Host "Until then use: http://$( (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -match '^192\.168\.' } | Select-Object -First 1 -ExpandProperty IPAddress) ):5174/agents/incoming-mail" -ForegroundColor Yellow
