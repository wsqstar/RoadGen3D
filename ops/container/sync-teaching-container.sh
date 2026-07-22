#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SYNC_HOST="${ROADGEN_SYNC_HOST:-10.123.4.23}"
SYNC_PORT="${ROADGEN_SYNC_PORT:-50031}"
SYNC_USER="${ROADGEN_SYNC_USER:-root}"
SYNC_KEY="${ROADGEN_SYNC_SSH_KEY:?Set ROADGEN_SYNC_SSH_KEY to the local private-key path}"
REMOTE_ROOT="${ROADGEN_SYNC_REMOTE_ROOT:-/workspace/RoadGen3D}"
TEMP_ROOT="$(mktemp -d /tmp/roadgen3d-sync.XXXXXX)"
SSH_COMMAND="ssh -i $SYNC_KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p $SYNC_PORT"
TARGET="$SYNC_USER@$SYNC_HOST"

cleanup() {
  rm -rf "$TEMP_ROOT"
}
trap cleanup EXIT

cd "$ROOT"
git -c core.quotePath=false ls-files \
  | grep -Ev '^(web/viewer$|metaurban$|vendor/|models/|artifacts/|\.archive$|\.archive/|\.claude/|\.qwen/|\.qoder/|\.playwright-mcp/|\.github/|ops/docker/|docker-compose|Dockerfile|shap_e_model_cache/|src/roadgen3d/artifacts/|src/roadgen3d/eval_engine_ext$|tools/download3dAssets$)' \
  > "$TEMP_ROOT/root-files.txt"

rsync -a --partial --stats --files-from="$TEMP_ROOT/root-files.txt" \
  -e "$SSH_COMMAND" "$ROOT/" "$TARGET:$REMOTE_ROOT/"

for data_dir in assets data; do
  rsync -a --partial --stats \
    --exclude-from="$ROOT/ops/container/rsync-useful-data-excludes.txt" \
    -e "$SSH_COMMAND" "$ROOT/$data_dir/" "$TARGET:$REMOTE_ROOT/$data_dir/"
done

git -C "$ROOT/web/viewer" -c core.quotePath=false ls-files > "$TEMP_ROOT/viewer-files.txt"
rsync -a --partial --stats --files-from="$TEMP_ROOT/viewer-files.txt" \
  -e "$SSH_COMMAND" "$ROOT/web/viewer/" "$TARGET:$REMOTE_ROOT/web/viewer/"
rsync -a --partial --stats -e "$SSH_COMMAND" \
  "$ROOT/web/viewer/dist/" "$TARGET:$REMOTE_ROOT/web/viewer/dist/"

git -C "$ROOT/src/roadgen3d/eval_engine_ext" -c core.quotePath=false ls-files \
  > "$TEMP_ROOT/road-metrics-files.txt"
rsync -a --partial --stats --files-from="$TEMP_ROOT/road-metrics-files.txt" \
  -e "$SSH_COMMAND" \
  "$ROOT/src/roadgen3d/eval_engine_ext/" \
  "$TARGET:$REMOTE_ROOT/src/roadgen3d/eval_engine_ext/"

# Deployment artifacts are intentionally synchronized explicitly because they
# may be uncommitted while the deployment is being prepared. No user state is
# stored below the code checkout.
rsync -a --partial --stats -e "$SSH_COMMAND" \
  "$ROOT/ops/container/" "$TARGET:$REMOTE_ROOT/ops/container/"
rsync -a --partial --stats -e "$SSH_COMMAND" \
  "$ROOT/ops/requirements-teaching-server.txt" "$TARGET:$REMOTE_ROOT/ops/"
rsync -a --partial --stats -e "$SSH_COMMAND" \
  "$ROOT/ops/scripts/check_teaching_server_profile.py" \
  "$TARGET:$REMOTE_ROOT/ops/scripts/"
rsync -a --partial --stats -e "$SSH_COMMAND" \
  "$ROOT/tests/test_teaching_server_profile.py" \
  "$TARGET:$REMOTE_ROOT/tests/"
rsync -a --partial --stats -e "$SSH_COMMAND" \
  "$ROOT/docs/TEACHING_CONTAINER_INTERNAL.md" \
  "$ROOT/docs/TEACHING_SERVER_BARE_METAL.md" \
  "$TARGET:$REMOTE_ROOT/docs/"

printf 'Code synchronized to %s:%s without --delete.\n' "$TARGET" "$REMOTE_ROOT"
printf 'User state remains outside the checkout at /workspace/roadgen3d-data.\n'
