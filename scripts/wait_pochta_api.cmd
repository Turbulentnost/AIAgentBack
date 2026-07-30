@echo off
setlocal EnableExtensions
set "POCHTA_API_HEALTH=%~1"
if not defined POCHTA_API_HEALTH set "POCHTA_API_HEALTH=http://127.0.0.1:8080/health"
set /a WAIT_SEC=%~2
if not defined WAIT_SEC set WAIT_SEC=120
echo Waiting for agent-pochta API: %POCHTA_API_HEALTH% (up to %WAIT_SEC%s)...
powershell -NoProfile -Command ^
  "$u=$env:POCHTA_API_HEALTH; $max=[int]$env:WAIT_SEC; for($i=0;$i -lt $max;$i+=2){ try { $r=Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 2; if($r.StatusCode -eq 200){ Write-Host 'API is ready.'; exit 0 } } catch {} Start-Sleep -Seconds 2 }; Write-Host '[warn] API not reachable yet ? Vite will show proxy ECONNREFUSED until docker/API is up.'; exit 1"
exit /b %ERRORLEVEL%
