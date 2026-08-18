#Requires -Version 5.1
<#
.SYNOPSIS
  Tailscale on THIS PC: backend -> WeChat utility :8790 (CONNECT.md step 1A).

.USAGE
  powershell -ExecutionPolicy Bypass -File scripts\setup_wechat_tailscale_this_pc.ps1
  powershell -ExecutionPolicy Bypass -File scripts\setup_wechat_tailscale_this_pc.ps1 -WeChatPcTailscaleIp 100.64.1.2
#>
[CmdletBinding()]
param(
    [string]$WeChatPcTailscaleIp = "",
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $ProjectRoot ".env"
$TailscaleExe = "${env:ProgramFiles}\Tailscale\tailscale.exe"

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

function Ensure-TailscaleInstalled {
    if (Test-Path -LiteralPath $TailscaleExe) { return }
    if ($SkipInstall) {
        throw "Tailscale not installed. Install from https://tailscale.com/download/windows"
    }
    Write-Step "Installing Tailscale..."
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        winget install --id Tailscale.Tailscale -e --accept-source-agreements --accept-package-agreements
    } else {
        $msi = Join-Path $env:TEMP "tailscale-setup-latest-amd64.msi"
        Write-Host "winget missing, downloading MSI..."
        Invoke-WebRequest -Uri "https://pkgs.tailscale.com/stable/tailscale-setup-latest-amd64.msi" -OutFile $msi
        Start-Process msiexec.exe -ArgumentList "/i `"$msi`" /quiet" -Wait
    }
    if (-not (Test-Path -LiteralPath $TailscaleExe)) {
        throw "Tailscale missing after install. Reboot or install manually: https://tailscale.com/download/windows"
    }
}

function Get-ThisPcTailscaleIp {
    $ip = (& $TailscaleExe ip -4 2>$null | Select-Object -First 1).Trim()
    if ($ip -match '^\d+\.\d+\.\d+\.\d+$') { return $ip }
    return ""
}

function Update-WeChatWsUrlInEnv([string]$PeerIp) {
    if (-not (Test-Path -LiteralPath $EnvFile)) {
        Write-Warning ".env not found: $EnvFile"
        return
    }
    $wsUrl = "ws://${PeerIp}:8790"
    $lines = Get-Content -LiteralPath $EnvFile -Encoding UTF8
    $found = $false
    $newLines = foreach ($line in $lines) {
        if ($line -match '^\s*WECHAT_WS_URL=') {
            $found = $true
            "WECHAT_WS_URL=$wsUrl"
        } else {
            $line
        }
    }
    if (-not $found) { $newLines += "WECHAT_WS_URL=$wsUrl" }
    Set-Content -LiteralPath $EnvFile -Value $newLines -Encoding UTF8
    Write-Host "Updated WECHAT_WS_URL=$wsUrl" -ForegroundColor Green
    Write-Host "Restart backend (uvicorn :5454)." -ForegroundColor Yellow
}

function Test-WeChatHealth([string]$HostIp) {
    $url = "http://${HostIp}:8790/health"
    try {
        $resp = Invoke-RestMethod -Uri $url -TimeoutSec 8
        Write-Host "OK $url" -ForegroundColor Green
        $resp | ConvertTo-Json -Compress
        return $true
    } catch {
        Write-Host "FAIL $url - $($_.Exception.Message)" -ForegroundColor Red
        return $false
    }
}

Write-Step "1/4 Tailscale on this PC (dev: backend + frontend)"
Ensure-TailscaleInstalled

Write-Step "2/4 Tailscale login (same account on WeChat PC)"
$status = & $TailscaleExe status 2>&1 | Out-String
if ($status -match "Logged out" -or $status -match "needs login" -or $status -match "Stopped") {
    Write-Host "Tailscale login window will open. Use the SAME account as on WeChat PC."
    Start-Process $TailscaleExe -ArgumentList "login" -Wait
    Start-Sleep -Seconds 2
    $status = & $TailscaleExe status 2>&1 | Out-String
}

$thisIp = Get-ThisPcTailscaleIp
if ($thisIp) {
    Write-Host "This PC Tailscale IP: $thisIp"
} else {
    Write-Host "This PC Tailscale IP: (run tailscale status after login)"
}
Write-Host $status

Write-Step "3/4 On WeChat PC (manual)"
Write-Host "  1) Install Tailscale, same account"
Write-Host "  2) Run setup_wechat_tailscale_wechat_pc.ps1 as Admin (firewall 8790)"
Write-Host "  3) Check http://127.0.0.1:8790/health locally"
Write-Host "  4) Note WeChat PC Tailscale IP (100.x.x.x) from tailscale status"

if ($WeChatPcTailscaleIp) {
    Write-Step "4/4 Set WECHAT_WS_URL and test /health"
    Update-WeChatWsUrlInEnv $WeChatPcTailscaleIp
    Test-WeChatHealth $WeChatPcTailscaleIp | Out-Null
} else {
    Write-Step "4/4 After WeChat PC is ready"
    Write-Host "Rerun with WeChat PC IP:"
    Write-Host "  .\scripts\setup_wechat_tailscale_this_pc.ps1 -WeChatPcTailscaleIp 100.x.x.x"
}

Write-Host ""
Write-Host "Avion agent: VPN does not break Avion; only WECHAT_WS_URL changes for test button."
