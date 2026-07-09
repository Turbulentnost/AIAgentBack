#Requires -Version 5.1
<#
.SYNOPSIS
  Collects UI access URL, login/password and writes access-info.txt.

.USAGE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\write_access_info.ps1
  or: run_access_info.cmd
#>
[CmdletBinding()]
param(
    [string]$OutputFile = "",
    [int]$Port = 0
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputFile) {
    $OutputFile = Join-Path $ProjectRoot "access-info.txt"
}

$Defaults = @{
    Email    = "temp.nd@local.dev"
    Password = "NdTemp2026!"
    Port     = 5173
    ApiHost  = "192.168.1.157:5454"
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
                $_.InterfaceAlias -notmatch "Loopback|vEthernet|WSL|Hyper-V|VirtualBox|VMware|Tailscale|ZeroTier"
            } |
            Sort-Object -Property @{
                Expression = {
                    if ($_.IPAddress -match "^192\.168\.") { 0 }
                    elseif ($_.IPAddress -match "^10\.") { 1 }
                    elseif ($_.IPAddress -match "^172\.(1[6-9]|2[0-9]|3[0-1])\.") { 2 }
                    else { 3 }
                }
            }, InterfaceMetric |
            Select-Object -ExpandProperty IPAddress -Unique
    }
    catch {
        $candidates = @()
    }

    if (@($candidates).Count -gt 0) { return @($candidates)[0] }

    try {
        $udp = New-Object System.Net.Sockets.UdpClient
        $udp.Connect("8.8.8.8", 80)
        $ip = ($udp.Client.LocalEndPoint).Address.ToString()
        $udp.Close()
        if ($ip -and $ip -ne "127.0.0.1") { return $ip }
    }
    catch { }

    return "127.0.0.1"
}

function Get-PublicIPv4 {
    $providers = @(
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
        "https://icanhazip.com"
    )
    foreach ($url in $providers) {
        try {
            $ip = (Invoke-RestMethod -Uri $url -TimeoutSec 8 -ErrorAction Stop).ToString().Trim()
            if ($ip -match "^\d{1,3}(\.\d{1,3}){3}$") { return $ip }
        }
        catch { }
    }
    return $null
}

function Build-BaseUrl {
    param([string]$HostOrIp, [int]$PortNum)
    return "http://${HostOrIp}:${PortNum}"
}

$envVars = Read-DotEnv -Path (Join-Path $ProjectRoot ".env")
$email = if ($envVars["PLATFORM_LOGIN_EMAIL"]) { $envVars["PLATFORM_LOGIN_EMAIL"] } else { $Defaults.Email }
$password = if ($envVars["PLATFORM_LOGIN_PASSWORD"]) { $envVars["PLATFORM_LOGIN_PASSWORD"] } else { $Defaults.Password }
$frontendPort = if ($Port -gt 0) { $Port } elseif ($envVars["FRONTEND_PORT"]) { [int]$envVars["FRONTEND_PORT"] } else { $Defaults.Port }
$apiHost = if ($envVars["PLATFORM_API_HOST"]) { $envVars["PLATFORM_API_HOST"] } else { $Defaults.ApiHost }

$lanIp = Get-LanIPv4
$publicIp = Get-PublicIPv4
$generatedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

$lanBase = Build-BaseUrl -HostOrIp $lanIp -PortNum $frontendPort
$publicBase = if ($publicIp) { Build-BaseUrl -HostOrIp $publicIp -PortNum $frontendPort } else { $null }

$publicIpLine = if ($publicIp) {
    "Vneshnij (publichnyj) IP:              $publicIp"
} else {
    "Vneshnij (publichnyj) IP:              ne udalos opredelit"
}

$publicSection = ""
if ($publicBase) {
    $publicSection = @"
--- URL dlya vneshnego dostupa ($publicIp) ---
Stranica vhoda:                 $publicBase/login
Katalog agentov (posle vhoda):  $publicBase/agents
Vhodyashchaya korrespondenciya: $publicBase/agents/incoming-mail

Dlya dostupa iz interneta nuzhno:
  1) Probros porta $frontendPort na etot PK (router/NAT)
  2) Pravilo v brandmauere Windows dlya TCP $frontendPort
  3) Zapushchennyj frontend: run_frontend.cmd

"@
}

$templatePath = Join-Path $PSScriptRoot "access-info.template.txt"
if (Test-Path -LiteralPath $templatePath) {
    $publicSectionRu = ""
    if ($publicBase) {
        $publicTemplatePath = Join-Path $PSScriptRoot "access-info.public.template.txt"
        if (Test-Path -LiteralPath $publicTemplatePath) {
            $publicSectionRu = Get-Content -LiteralPath $publicTemplatePath -Raw -Encoding UTF8
            $publicSectionRu = $publicSectionRu.Replace("{{PUBLIC_IP}}", $publicIp)
            $publicSectionRu = $publicSectionRu.Replace("{{PUBLIC_BASE}}", $publicBase)
            $publicSectionRu = $publicSectionRu.Replace("{{FRONTEND_PORT}}", [string]$frontendPort)
        }
    }

    $publicIpTemplate = if ($publicIp) {
        Join-Path $PSScriptRoot "access-info.public-ip.ok.txt"
    } else {
        Join-Path $PSScriptRoot "access-info.public-ip.fail.txt"
    }
    $publicIpLineRu = (Get-Content -LiteralPath $publicIpTemplate -Raw -Encoding UTF8).Replace("{{PUBLIC_IP}}", $(if ($publicIp) { $publicIp } else { "" }))

    $content = Get-Content -LiteralPath $templatePath -Raw -Encoding UTF8
    $content = $content.Replace("{{GENERATED_AT}}", $generatedAt)
    $content = $content.Replace("{{LAN_IP}}", $lanIp)
    $content = $content.Replace("{{PUBLIC_IP_LINE}}", $publicIpLineRu)
    $content = $content.Replace("{{FRONTEND_PORT}}", [string]$frontendPort)
    $content = $content.Replace("{{API_HOST}}", $apiHost)
    $content = $content.Replace("{{EMAIL}}", $email)
    $content = $content.Replace("{{PASSWORD}}", $password)
    $content = $content.Replace("{{LAN_BASE}}", $lanBase)
    $content = $content.Replace("{{PUBLIC_SECTION}}", $publicSectionRu)
}
else {
    $lines = @(
        "============================================================"
        "  Dostup k UI: Vhodyashchaya korrespondenciya (agent_nd_front)"
        "  Generated: $generatedAt"
        "============================================================"
        ""
        "VAZHNO: avtomaticheskij vhod po ssylke NE podderzhivaetsya."
        ""
        "--- Set ---"
        "LAN IP: $lanIp"
        $publicIpLine
        "Port: $frontendPort"
        "API: http://$apiHost"
        ""
        "Email: $email"
        "Password: $password"
        ""
        "Login: $lanBase/login"
        "Agents: $lanBase/agents"
        "Incoming mail: $lanBase/agents/incoming-mail"
        ""
    )
    if ($publicSection) { $lines += $publicSection.Split([Environment]::NewLine) }
    $lines += "============================================================"
    $content = ($lines -join [Environment]::NewLine) + [Environment]::NewLine
}

Set-Content -LiteralPath $OutputFile -Value $content -Encoding UTF8

Write-Host "[ok] Written: $OutputFile" -ForegroundColor Green
Write-Host ""
Get-Content -LiteralPath $OutputFile -Encoding UTF8
