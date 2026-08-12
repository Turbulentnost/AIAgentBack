#Requires -Version 5.1
<#
.SYNOPSIS
  Настройка LAN-доступа к агенту Авион: firewall + access-info для тестеров.

.USAGE
  powershell -ExecutionPolicy Bypass -File scripts\setup_aveon_lan_access.ps1
#>
[CmdletBinding()]
param(
    [string]$OutputFile = "",
    [switch]$SkipFirewall
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$FrontRoot = Join-Path (Split-Path -Parent $ProjectRoot) "AIAgentFront"
if (-not (Test-Path $FrontRoot)) {
    $FrontRoot = Join-Path $ProjectRoot "..\AIAgentFront"
}

if (-not $OutputFile) {
    $OutputFile = Join-Path $ProjectRoot "access-info-aveon.txt"
}

function Read-DotEnv {
    param([string]$Path)
    $result = @{}
    if (-not (Test-Path -LiteralPath $Path)) { return $result }
    Get-Content -LiteralPath $Path -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) { return }
        $eq = $line.IndexOf("=")
        if ($eq -lt 1) { return }
        $key = $line.Substring(0, $eq).Trim()
        $value = $line.Substring($eq + 1).Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $result[$key] = $value
    }
    return $result
}

function Get-LanIPv4 {
    $candidates = @()
    try {
        $candidates = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object {
                $_.IPAddress -ne "127.0.0.1" -and
                $_.PrefixOrigin -ne "WellKnown" -and
                $_.InterfaceAlias -notmatch "Loopback|vEthernet|WSL|Hyper-V|VirtualBox|VMware|Tailscale|ZeroTier|Radmin VPN"
            } |
            Sort-Object -Property @{
                Expression = {
                    if ($_.IPAddress -match "^192\.168\.") { 0 }
                    elseif ($_.IPAddress -match "^10\.") { 1 }
                    else { 2 }
                }
            }, InterfaceMetric |
            Select-Object -ExpandProperty IPAddress -Unique
    }
    catch { $candidates = @() }
    if (@($candidates).Count -gt 0) { return @($candidates)[0] }
    return "127.0.0.1"
}

function Test-TcpPortListening {
    param([int]$Port)
    return [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

if (-not $SkipFirewall) {
    & (Join-Path $PSScriptRoot "open_aveon_lan_firewall.ps1")
    if ($LASTEXITCODE -eq 2) {
        Write-Host "[warn] Firewall rules not applied (need Administrator). Continuing with access-info..." -ForegroundColor Yellow
    }
    Write-Host ""
}

$lanIp = Get-LanIPv4
$frontendPort = 5173
$apiPort = 5454
$lanBase = "http://${lanIp}:${frontendPort}"
$apiHost = "${lanIp}:${apiPort}"
$generatedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

$templatePath = Join-Path $PSScriptRoot "access-info-aveon.template.txt"
if (-not (Test-Path -LiteralPath $templatePath)) {
    throw "Template not found: $templatePath"
}
$content = Get-Content -LiteralPath $templatePath -Raw -Encoding UTF8
$content = $content.Replace("{{GENERATED_AT}}", $generatedAt)
$content = $content.Replace("{{LAN_IP}}", $lanIp)
$content = $content.Replace("{{LAN_BASE}}", $lanBase)
$content = $content.Replace("{{API_HOST}}", $apiHost)
Set-Content -LiteralPath $OutputFile -Value $content -Encoding UTF8

Write-Host "=== Статус сервисов на хосте ===" -ForegroundColor Cyan
$viteUp = Test-TcpPortListening -Port $frontendPort
$apiUp = Test-TcpPortListening -Port $apiPort
Write-Host ("  Vite  :{0} {1}" -f $frontendPort, $(if ($viteUp) { "LISTEN" } else { "NOT RUNNING" })) `
    -ForegroundColor $(if ($viteUp) { "Green" } else { "Red" })
Write-Host ("  API   :{0} {1}" -f $apiPort, $(if ($apiUp) { "LISTEN" } else { "NOT RUNNING" })) `
    -ForegroundColor $(if ($apiUp) { "Green" } else { "Red" })

if ($apiUp) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:${apiPort}/api/v1/health" -TimeoutSec 5
        Write-Host "  Health: $($health.status)" -ForegroundColor Green
    }
    catch {
        Write-Host "  Health: недоступен ($($_.Exception.Message))" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "[ok] Инструкция для тестеров: $OutputFile" -ForegroundColor Green
Write-Host ""
Write-Host "Отправьте тестерам URL: $lanBase/agents/document-analysis" -ForegroundColor Cyan
Write-Host "(сначала /login, затем переход на агент)" -ForegroundColor Gray
Write-Host ""
Get-Content -LiteralPath $OutputFile -Encoding UTF8
