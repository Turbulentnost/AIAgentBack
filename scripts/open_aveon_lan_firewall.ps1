#Requires -Version 5.1
<#
.SYNOPSIS
  Opens inbound TCP 5173 (Vite) and 5454 (FastAPI) for Aveon LAN testing.
  Requires Administrator (UAC prompt when run via run_aveon_lan_access_admin.cmd).
#>
[CmdletBinding()]
param(
    [int[]]$Ports = @(5173, 5454),
    [string]$RulePrefix = "Aveon AI Platform LAN"
)

$ErrorActionPreference = "Stop"

function Test-IsAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function New-LanFirewallRule {
    param(
        [int]$Port,
        [string]$Label
    )
    $ruleName = "$RulePrefix - $Label (TCP $Port)"
    $existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    if ($existing) {
        Enable-NetFirewallRule -DisplayName $ruleName | Out-Null
        Write-Host "[ok] Rule exists, enabled: $ruleName" -ForegroundColor Yellow
        return $true
    }

    New-NetFirewallRule `
        -DisplayName $ruleName `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort $Port `
        -Profile Private, Domain `
        -Description "LAN access for Aveon agent testers (Vite/FastAPI)." | Out-Null
    Write-Host "[ok] Created rule: $ruleName" -ForegroundColor Green
    return $true
}

if (-not (Test-IsAdmin)) {
    Write-Host "[warn] Administrator rights required for firewall rules." -ForegroundColor Yellow
    Write-Host "       Run: run_aveon_lan_access_admin.cmd (UAC prompt)" -ForegroundColor Yellow
    Write-Host "       Or manually in elevated PowerShell:" -ForegroundColor Yellow
    foreach ($port in $Ports) {
        Write-Host "         New-NetFirewallRule -DisplayName 'Aveon TCP $port' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port -Profile Private,Domain" -ForegroundColor Gray
    }
    exit 2
}

Write-Host "Opening Windows Firewall for Aveon LAN testing..." -ForegroundColor Cyan
foreach ($port in $Ports) {
    $label = if ($port -eq 5173) { "Frontend Vite" } elseif ($port -eq 5454) { "Backend API" } else { "Port $port" }
    New-LanFirewallRule -Port $port -Label $label | Out-Null
}

Write-Host ""
Write-Host "Profiles: Private, Domain." -ForegroundColor Gray
exit 0
