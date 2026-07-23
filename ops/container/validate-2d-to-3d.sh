#!/usr/bin/env bash
set -uo pipefail

ROOT=/workspace/RoadGen3D
STATE_ROOT=/workspace/roadgen3d-data
OSM_RUNS=20
WORKERS=5
SEED=20260723
TIMEOUT=900

while (($#)); do
  case "$1" in
    --osm-runs) OSM_RUNS="$2"; shift 2 ;;
    --workers) WORKERS="$2"; shift 2 ;;
    --seed) SEED="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

if ((OSM_RUNS < 1 || WORKERS < 1 || WORKERS > 5)); then
  echo "--osm-runs must be positive and --workers must be between 1 and 5" >&2
  exit 2
fi

cd "$ROOT"
test -d .git
test -x .venv/bin/python
test -r "$STATE_ROOT/roadgen3d.env"

REVISION="$(git rev-parse --short=12 HEAD)"
RUN_ID="2d-to-3d-$(date -u +%Y%m%dT%H%M%SZ)-$REVISION"
RUN_DIR="$STATE_ROOT/validation/$RUN_ID"
mkdir -p "$RUN_DIR/service-logs" "$RUN_DIR/batch-report"
SUMMARY="$RUN_DIR/summary.tsv"
SERVICE_OFFSETS="$RUN_DIR/service-log-offsets.tsv"
printf 'step\texit_code\tlog\n' >"$SUMMARY"
printf 'path\tstart_byte\n' >"$SERVICE_OFFSETS"

for log in \
  "$STATE_ROOT/logs/api.log" \
  "$STATE_ROOT/logs/api-error.log" \
  "$STATE_ROOT/logs/supervisord.log" \
  "$STATE_ROOT"/logs/worker-*.log \
  "$STATE_ROOT"/logs/worker-*-error.log
do
  test -f "$log" || continue
  printf '%s\t%s\n' "$log" "$(( $(stat -c %s "$log") + 1 ))" >>"$SERVICE_OFFSETS"
done

run_step() {
  local name="$1"
  shift
  local log="$RUN_DIR/$name.log"
  printf '\n===== %s =====\n' "$name" | tee "$log"
  printf 'command:' | tee -a "$log"
  printf ' %q' "$@" | tee -a "$log"
  printf '\n' | tee -a "$log"
  "$@" > >(tee -a "$log") 2> >(tee -a "$log" >&2)
  local status=$?
  printf '%s\t%s\t%s\n' "$name" "$status" "$log" >>"$SUMMARY"
  return 0
}

{
  echo "run_id=$RUN_ID"
  echo "started_utc=$(date -u +%FT%TZ)"
  echo "root_revision=$(git rev-parse HEAD)"
  echo "viewer_revision=$(git -C web/viewer rev-parse HEAD)"
  echo "branch=$(git branch --show-current)"
  echo "python=$(.venv/bin/python --version 2>&1)"
  echo "node=$(node --version)"
  echo "npm=$(npm --version)"
  echo "cpu_count=$(nproc)"
  free -h
  df -h "$ROOT" "$STATE_ROOT"
  git status --short --branch
  git -C web/viewer status --short --branch
} >"$RUN_DIR/environment.log" 2>&1

run_step 01-api-health curl --fail --silent --show-error http://127.0.0.1:8010/api/health
run_step 02-viewer-migration-contract npm --prefix web/viewer run test:2d-to-3d-migration
run_step 03-viewer-professional-pipeline npm --prefix web/viewer run test:professional-pipeline
run_step 04-viewer-starter-contract npm --prefix web/viewer run test:starter-scene
run_step 05-viewer-typecheck npm --prefix web/viewer run typecheck
run_step 06-viewer-build npm --prefix web/viewer run build
run_step 07-backend-flow-tests \
  .venv/bin/python -m pytest -q \
  tests/test_design_api.py::test_design_api_endpoints_return_expected_shapes \
  tests/test_scene_jobs.py::test_scene_job_service_runs_sync_generation \
  tests/test_scene_jobs.py::test_scene_job_service_exposes_running_progress \
  tests/test_design_runtime.py::test_generate_scene_from_draft_wraps_existing_scene_pipeline \
  tests/test_design_runtime.py::test_generate_scene_from_draft_applies_osm_scene_context \
  tests/test_street_compose.py::test_backend_random_osm_snapshot_generates_pedestrian_priority_3d
run_step 08-random-osm-batch \
  .venv/bin/python ops/scripts/test_batch.py \
  --mode osm \
  --osm-runs "$OSM_RUNS" \
  --workers "$WORKERS" \
  --seed "$SEED" \
  --timeout "$TIMEOUT" \
  --output "$RUN_DIR/batch-report"

while IFS=$'\t' read -r log start_byte; do
  test "$log" != "path" || continue
  test -f "$log" || continue
  tail -c "+$start_byte" "$log" >"$RUN_DIR/service-logs/$(basename "$log")"
done <"$SERVICE_OFFSETS"

FAILED_STEPS="$RUN_DIR/failed-steps.txt"
awk -F '\t' 'NR > 1 && $2 != 0 {print $1 "\t" $2 "\t" $3}' "$SUMMARY" >"$FAILED_STEPS"
{
  echo "finished_utc=$(date -u +%FT%TZ)"
  echo "failed_step_count=$(wc -l <"$FAILED_STEPS" | tr -d ' ')"
  echo "run_dir=$RUN_DIR"
} >"$RUN_DIR/result.env"

echo "Validation artifacts: $RUN_DIR"
if test -s "$FAILED_STEPS"; then
  echo "Failed validation steps:" >&2
  cat "$FAILED_STEPS" >&2
  exit 1
fi
echo "All 2D -> 3D validation steps passed."
