# Сборка backend-образов с перебором зеркал PyPI (если pypi.org недоступен или таймаутит).
#
#   .\scripts\docker_build_backend.ps1
#   .\scripts\docker_build_backend.ps1 -Mirror aliyun
#
param(
    [ValidateSet("auto", "pypi", "aliyun", "tsinghua", "yandex")]
    [string]$Mirror = "auto"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

$mirrors = @{
    pypi     = "https://pypi.org/simple"
    aliyun   = "https://mirrors.aliyun.com/pypi/simple/"
    tsinghua = "https://pypi.tuna.tsinghua.edu.cn/simple"
    yandex   = "https://mirror.yandex.ru/pypi/simple/"
}

function Test-Mirror([string]$Url) {
    $probe = if ($Url.EndsWith("/")) { "${Url}pip/" } else { "${Url}/pip/" }
    try {
        $null = Invoke-WebRequest -Uri $probe -Method Head -TimeoutSec 12 -UseBasicParsing
        return $true
    } catch {
        return $false
    }
}

$order = switch ($Mirror) {
    "pypi"     { @("pypi") }
    "aliyun"   { @("aliyun", "pypi", "tsinghua") }
    "tsinghua" { @("tsinghua", "aliyun", "pypi") }
    "yandex"   { @("yandex", "aliyun", "pypi", "tsinghua") }
    default    { @("aliyun", "tsinghua", "pypi", "yandex") }
}

$indexUrl = $null
foreach ($key in $order) {
    $url = $mirrors[$key]
    Write-Host "Проверка зеркала $key : $url" -ForegroundColor Cyan
    if (Test-Mirror $url) {
        $indexUrl = $url
        Write-Host "  OK — используем как PIP_INDEX_URL" -ForegroundColor Green
        break
    }
    Write-Host "  недоступно" -ForegroundColor DarkYellow
}

if (-not $indexUrl) {
    Write-Error "Ни одно зеркало PyPI не ответило. Проверьте интернет/VPN или задайте PIP_INDEX_URL в .env"
}

$env:PIP_INDEX_URL = $indexUrl
$env:PIP_EXTRA_INDEX_URL = "https://pypi.org/simple"
$env:PIP_DEFAULT_TIMEOUT = "120"

Write-Host "`nСборка: PIP_INDEX_URL=$indexUrl" -ForegroundColor Green
docker compose build api celery-worker celery-erp-worker celery-imap-worker migrate rag-init
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`nПерезапуск сервисов..." -ForegroundColor Green
docker compose up -d api celery-worker celery-erp-worker celery-imap-worker
exit $LASTEXITCODE
