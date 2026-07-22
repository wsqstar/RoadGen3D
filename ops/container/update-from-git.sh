#!/usr/bin/env bash
set -euo pipefail

ROOT=/workspace/RoadGen3D
STATE_ROOT=/workspace/roadgen3d-data
DEPLOY_BRANCH="${ROADGEN_DEPLOY_BRANCH:-codex/user-data-zip-export}"

cd "$ROOT"
test -d .git
test -r "$STATE_ROOT/roadgen3d.env"
test -x .venv/bin/python
command -v node >/dev/null
command -v npm >/dev/null

if test -n "$(git status --porcelain --untracked-files=no)"; then
  echo "Refusing to update: tracked deployment files have local changes." >&2
  git status --short --untracked-files=no >&2
  exit 1
fi

"$ROOT/ops/container/backup-teaching.sh"
git fetch origin "$DEPLOY_BRANCH"
git merge --ff-only FETCH_HEAD
git submodule sync -- web/viewer src/roadgen3d/eval_engine_ext
git submodule update --init --depth 1 web/viewer src/roadgen3d/eval_engine_ext

if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
  .venv/bin/python -m ensurepip --upgrade
fi
.venv/bin/python -m pip install --disable-pip-version-check \
  -r ops/requirements-teaching-server.txt

(
  cd web/viewer
  npm ci
  npm run build
)

set -a
# shellcheck disable=SC1091
source "$STATE_ROOT/roadgen3d.env"
set +a
.venv/bin/python -m alembic upgrade head

"$ROOT/ops/container/start-teaching.sh"
curl --fail --silent --show-error http://127.0.0.1:8010/api/health >/dev/null
curl --fail --silent --show-error http://127.0.0.1:4173/ >/dev/null

printf 'Updated %s to %s and verified Viewer/API health.\n' \
  "$DEPLOY_BRANCH" "$(git rev-parse --short HEAD)"
