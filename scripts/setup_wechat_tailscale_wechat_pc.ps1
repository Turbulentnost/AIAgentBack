#Requires -RunAsAdministrator
#Requires -Version 5.1
<#
.SYNOPSIS
  WeChat PC: allow inbound TCP 8790 (CONNECT.md step 1A).

.USAGE
  powershell -ExecutionPolicy Bypass -File setup_wechat_tailscale_wechat_pc.ps1
#>
$ErrorActionPreference = "Stop"
$ruleName = "WeChat Utility WS 8790"

Write-Host "Firewall: inbound TCP 8790" -ForegroundColor Cyan
$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Rule already exists: $ruleName"
} else {
    New-NetFirewallRule `
        -DisplayName $ruleName `
        -Direction Inbound `
        -Protocol TCP `
        -LocalPort 8790 `
        -Action Allow `
        -Profile Any | Out-Null
    Write-Host "Created: $ruleName" -ForegroundColor Green
}

$TailscaleExe = "${env:ProgramFiles}\Tailscale\tailscale.exe"
if (Test-Path -LiteralPath $TailscaleExe) {
    $ip = (& $TailscaleExe ip -4 2>$null | Select-Object -First 1).Trim()
    Write-Host "This machine Tailscale IP: $ip"
    Write-Host "Set on dev PC: WECHAT_WS_URL=ws://${ip}:8790"
} else {
    Write-Host "Install Tailscale and login with same account as dev PC."
}

try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8790/health" -TimeoutSec 5
    Write-Host "Local health OK:" -ForegroundColor Green
    $health | ConvertTo-Json -Compress
} catch {
    Write-Host "Utility not on 127.0.0.1:8790 - start WeChat + bot." -ForegroundColor Yellow
}
