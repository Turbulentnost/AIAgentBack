# Pilot и полный прогон итеративного обучения BGE по эталону 1С (Jalko)
# Запуск из корня репозитория на сервере 192.168.1.157

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "=== Pilot: dry-run, limit 50 ==="
docker compose run --rm api python scripts/train_bge_iterative_1c_oracle.py --limit 50 --dry-run --since 2026-07-20
if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 2) { exit $LASTEXITCODE }

Write-Host "=== Full train: fresh index + reextract, target 90% ==="
docker compose run --rm api python scripts/train_bge_iterative_1c_oracle.py --fresh-index --reextract --since 2026-07-20 --target 0.90 --max-iterations 20
if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 2) { exit $LASTEXITCODE }

Write-Host "=== Holdout eval (out-of-sample) ==="
docker compose run --rm api python scripts/eval_bge_routing_holdout.py --limit 100

Write-Host "=== Done. Reports: data/stats/bge_1c_oracle_train.json ==="
