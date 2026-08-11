#Requires -Version 5.1
<#
.SYNOPSIS
  Adds ok6 -> LAN IPv4 mapping to hosts file on THIS PC (client-side workaround).

  Use when http://ok6:5174 fails but http://192.168.2.91:5174 works because AD DNS
  returns Hyper-V adapter IPs (172.30.* / 172.28.*) for ok6.turbo-don.ru.

.USAGE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\fix_lan_hosts_entry.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\fix_lan_hosts_entry.ps1 -LanIp 192.168.2.91 -Hostname ok6
#>
[CmdletBinding()]
param(
    [string]$LanIp = "",
    [string]$Hostname = ""
)

$ErrorActionPreference = "Stop"

function Get-PrimaryLanIPv4 {
    try {
        $ip = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object {
                $_.IPAddress -match "^192\.168\." -and
                $_.PrefixOrigin -ne "WellKnown" -and
                $_.InterfaceAlias -notmatch "vEthernet|WSL|Hyper-V|Loopback|VirtualBox|VMware"
            } |
            Select-Object -First 1 -ExpandProperty IPAddress
        if ($ip) { return $ip }
    }
    catch { }
    return $null
}

if (-not $LanIp) {
    $LanIp = Get-PrimaryLanIPv4
    if (-not $LanIp) { throw "Could not detect LAN IPv4. Pass -LanIp explicitly." }
}
if (-not $Hostname) {
    $Hostname = ($env:COMPUTERNAME).ToLowerInvariant()
}

$hostsPath = Join-Path $env:SystemRoot "System32\drivers\etc\hosts"
$marker = "# agent-pochta LAN hostname fix"
$entry = "$LanIp`t$Hostname`t$Hostname.turbo-don.ru`t$marker"

$lines = Get-Content -LiteralPath $hostsPath -Encoding UTF8 -ErrorAction Stop
$filtered = @($lines | Where-Object { $_ -notmatch [regex]::Escape($marker) })
if ($filtered -notcontains $entry) {
    if ($filtered.Count -gt 0 -and $filtered[-1].Trim() -ne "") {
        $filtered += ""
    }
    $filtered += $entry
    Set-Content -LiteralPath $hostsPath -Value $filtered -Encoding UTF8
    Write-Host "[ok] Added to hosts: $entry" -ForegroundColor Green
}
else {
    Write-Host "[ok] Entry already present in hosts." -ForegroundColor Green
}

Write-Host ""
Write-Host "Test: http://${Hostname}:5174/agents/incoming-mail" -ForegroundColor Cyan
Write-Host "Run this script on every PC where short hostname access is needed." -ForegroundColor Yellow
Write-Host "Permanent fix: ask IT to remove 172.30.* / 172.28.* A records for $Hostname in AD DNS." -ForegroundColor Yellow
