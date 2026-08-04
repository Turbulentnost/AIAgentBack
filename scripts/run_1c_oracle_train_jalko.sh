#!/usr/bin/env bash
# Pilot и полный прогон итеративного обучения BGE по эталону 1С (Jalko)
set -euo pipefail

echo "=== Pilot: dry-run, limit 50 ==="
docker compose run --rm api python scripts/train_bge_iterative_1c_oracle.py --limit 50 --dry-run --since 2026-07-20 || true

echo "=== Full train: fresh index + reextract, target 90% ==="
docker compose run --rm api python scripts/train_bge_iterative_1c_oracle.py --fresh-index --reextract --since 2026-07-20 --target 0.90 --max-iterations 20 || true

echo "=== Holdout eval (out-of-sample) ==="
docker compose run --rm api python scripts/eval_bge_routing_holdout.py --limit 100 || true

echo "=== Done. Reports: data/stats/bge_1c_oracle_train.json ==="
