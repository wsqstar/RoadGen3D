#!/usr/bin/env bash
set -euo pipefail

STATE_ROOT=/workspace/roadgen3d-data
BACKUP_ROOT="$STATE_ROOT/backups"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"

umask 077
install -d -m 0700 "$BACKUP_ROOT"
set -a
source "$STATE_ROOT/roadgen3d.env"
set +a

POSTGRES_URL="${ROADGEN_DATABASE_URL/postgresql+psycopg/postgresql}"
pg_dump --format=custom --file="$BACKUP_ROOT/roadgen3d-$TIMESTAMP.dump" "$POSTGRES_URL"
tar -C "$STATE_ROOT" -czf "$BACKUP_ROOT/artifacts-$TIMESTAMP.tar.gz" artifacts

printf 'database_backup=%s\n' "$BACKUP_ROOT/roadgen3d-$TIMESTAMP.dump"
printf 'artifact_backup=%s\n' "$BACKUP_ROOT/artifacts-$TIMESTAMP.tar.gz"
