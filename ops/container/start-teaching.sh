#!/usr/bin/env bash
set -euo pipefail

ROOT=/workspace/RoadGen3D
STATE_ROOT=/workspace/roadgen3d-data
SUPERVISOR_CONFIG="$ROOT/ops/container/teaching-supervisord.conf"

install -d -m 0700 \
  "$STATE_ROOT/artifacts" \
  "$STATE_ROOT/backups" \
  "$STATE_ROOT/logs" \
  "$STATE_ROOT/matplotlib-cache" \
  "$STATE_ROOT/osm-cache" \
  "$STATE_ROOT/run"

test -r "$STATE_ROOT/roadgen3d.env"
test -x "$ROOT/.venv/bin/supervisord"
test -f "$ROOT/web/viewer/dist/index.html"

pg_ctlcluster 14 main start 2>/dev/null || pg_isready --quiet
redis-cli ping >/dev/null 2>&1 || redis-server /etc/redis/redis.conf --daemonize yes
/usr/sbin/nginx -t -c "$ROOT/ops/container/teaching-nginx.conf"

if "$ROOT/.venv/bin/supervisorctl" -c "$SUPERVISOR_CONFIG" status >/dev/null 2>&1; then
  "$ROOT/.venv/bin/supervisorctl" -c "$SUPERVISOR_CONFIG" reread
  "$ROOT/.venv/bin/supervisorctl" -c "$SUPERVISOR_CONFIG" update
  "$ROOT/.venv/bin/supervisorctl" -c "$SUPERVISOR_CONFIG" restart all
else
  "$ROOT/.venv/bin/supervisord" -c "$SUPERVISOR_CONFIG"
fi

"$ROOT/.venv/bin/supervisorctl" -c "$SUPERVISOR_CONFIG" status
