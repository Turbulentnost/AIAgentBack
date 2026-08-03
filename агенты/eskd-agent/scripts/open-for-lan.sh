#!/usr/bin/env bash
# Открыть ESKD Agent для просмотра с других ПК в локальной сети (WSL2 → Windows portproxy).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec bash "$ROOT/scripts/enable-lan-access.sh"
