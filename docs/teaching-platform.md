# RoadGen3D course studio

The course studio turns the research Viewer into a project-scoped teaching workflow:

`sign in → create project → select AOI → import OSM/GeoJSON → review 2D → generate 3D → evaluate → compare → export`

Open `/#course-studio` in development. The production frontend image makes this the default route; the original Viewer and research tools remain available as expert routes.

## Local development

Install the additional backend packages into the existing environment:

```bash
uv pip install --python .venv/bin/python -r ops/requirements-api.txt
```

Start the API and Viewer in separate terminals:

```bash
ROADGEN_ALLOW_DEV_BOOTSTRAP=1 PYTHONPATH=src .venv/bin/uvicorn web.api.main:app --reload --port 8000
cd web/viewer && npm run dev
```

The Vite server proxies `/api/v1` to `http://127.0.0.1:8000`. Set `ROADGEN_API_ORIGIN` to use another API origin.

## Course deployment

```bash
cp .env.teaching.example .env.teaching
# Replace every change-this value and set the public HTTPS origin.
docker compose --env-file .env.teaching -f docker-compose.teaching.yml up -d --build
```

The platform is then available on port `8080`. Put the frontend behind the institution's HTTPS reverse proxy. The first teacher/admin account uses the one-time `ROADGEN_BOOTSTRAP_TOKEN`; subsequent students register with a course code and teacher invitation.

Services:

- `frontend`: static Viewer plus reverse proxy to the API
- `api`: FastAPI and Alembic migration entrypoint
- `worker`: persistent RQ worker for OSM, generation, evaluation, and export
- `postgres`: users, tenancy, revisions, evaluation provenance, jobs, and audit log
- `redis`: durable queued-work coordination
- `minio`: project-scoped GeoJSON, layouts, GLB files, screenshots, and bundles

Back up the `postgres_data` and `minio_data` volumes together. Redis append-only persistence is enabled, while PostgreSQL remains the job system of record; interrupted `queued` or `running` jobs are recovered when the worker starts.

## Production boundaries

- Production course APIs return IDs and controlled artifact downloads, never absolute server paths.
- Students can access only projects they own. Teachers can access projects in courses they teach; admins can administer all courses.
- OSM/background buildings are locked in 3D. Geographic edits return to the 2D reference workflow.
- Every scene edit creates an immutable revision. AI edits use an `ai_edit` child branch instead of overwriting student work.
- Evaluation weights must be non-negative and are normalized by the API. Missing visual metrics remain unavailable rather than being imputed into the total.
- The comparison endpoint reports traceable deltas and explicitly does not claim causal effects.

## Verification

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_teaching_platform.py tests/test_scene_sources_and_edits.py
cd web/viewer && npm run typecheck && npm run build && npm run test:i18n && npm run test:plan-export
POSTGRES_PASSWORD=test MINIO_ROOT_USER=test MINIO_ROOT_PASSWORD=test-password ROADGEN_BOOTSTRAP_TOKEN=test-token \
  docker compose -f docker-compose.teaching.yml config --quiet
```
