# Включить mirrored networking — порты WSL видны в LAN без portproxy (Win11 22H2+).
# Запуск от администратора не обязателен (пишет в профиль пользователя).
$ErrorActionPreference = "Stop"

$wslconfig = Join-Path $env:USERPROFILE ".wslconfig"
$content = @"
[wsl2]
networkingMode=mirrored
firewall=true
autoProxy=true
"@

Set-Content -Path $wslconfig -Value $content -Encoding UTF8
Write-Host "Записано: $wslconfig"
Write-Host ""
Write-Host "Содержимое:"
Get-Content $wslconfig
Write-Host ""
Write-Host "Дальше в PowerShell или cmd:"
Write-Host "  wsl --shutdown"
Write-Host "  (снова откройте WSL и запустите ./start.sh)"
Write-Host ""
Write-Host "После mirrored LAN URL: http://192.168.2.61:8000/ без portproxy"
