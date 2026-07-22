#!/usr/bin/env bash
set -euo pipefail

ROOT=/workspace/RoadGen3D
CONFIG="$ROOT/ops/container/teaching-supervisord.conf"

if "$ROOT/.venv/bin/supervisorctl" -c "$CONFIG" status >/dev/null 2>&1; then
  "$ROOT/.venv/bin/supervisorctl" -c "$CONFIG" shutdown
fi
