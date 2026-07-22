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

## Production deployment (one server)

This Compose stack is the supported shared-server deployment. It exposes the
multi-user platform at `/api/v1`; the older research API is intentionally not
published by the production frontend because it has local-file and no-login
semantics.

### 1. Prerequisites and network boundary

- A Linux host with Docker Engine plus the Compose plugin, a DNS name, and a
  reverse proxy that terminates HTTPS.
- Open only `80`/`443` to users. The supplied Compose file binds its frontend
  to `127.0.0.1:8080`, so an HTTPS reverse proxy on the same host is required.
  Do not publish PostgreSQL (`5432`), Redis (`6379`), MinIO S3 (`9000`), or the
  MinIO console (`9001`).
- Reserve persistent disk for PostgreSQL, MinIO, generated 3D artifacts, and
  Docker images. The exact requirement depends on mesh sizes and retention;
  monitor it from day one rather than treating the runtime volume as scratch.

### 2. Configure secrets

```bash
git clone <your-private-repository-url> roadgen3d
cd roadgen3d
cp .env.teaching.example .env.teaching
chmod 600 .env.teaching
```

Replace every `change-this` value with a unique, high-entropy value. Set
`ROADGEN_CORS_ORIGINS` to the exact public HTTPS origin, for example
`https://roadgen.example.edu`; do not use `*`. Leave `OPENAI_API_KEY` empty when
LLM-assisted proposals are not required. Keep `.env.teaching` out of Git and
out of support tickets.

### 3. Start and validate

```bash
docker compose --env-file .env.teaching -f docker-compose.teaching.yml config --quiet
docker compose --env-file .env.teaching -f docker-compose.teaching.yml up -d --build
docker compose --env-file .env.teaching -f docker-compose.teaching.yml ps
curl --fail http://127.0.0.1:8080/api/health
```

The platform listens on port `8080` by default. The MinIO console is bound to
`127.0.0.1:9001` for local-only administration; use `ssh -L 9001:127.0.0.1:9001
<server>` if it must be inspected. Do not expose its root credentials through a
general user-facing proxy.

Put the frontend behind the institution's HTTPS reverse proxy. The proxy must
preserve `Host`, `X-Forwarded-For`, and `X-Forwarded-Proto`, and should enforce
request-size and rate limits appropriate to your users. A minimal Nginx virtual
host is:

```nginx
server {
  listen 443 ssl http2;
  server_name roadgen.example.edu;
  # ssl_certificate / ssl_certificate_key managed by your institution or Certbot

  location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    client_max_body_size 100m;
    proxy_read_timeout 1800s;
  }
}
```

After HTTPS is live, repeat the health check through the public origin and
verify that `https://roadgen.example.edu/api/v1/auth/bootstrap-status` responds
but a legacy endpoint such as `/api/design/draft` returns `404`.

### 4. Bootstrap and operate accounts

1. Open the production site and create the first administrator with
   `ROADGEN_BOOTSTRAP_TOKEN`. The operation succeeds only while no user exists.
2. Store that bootstrap token as a consumed deployment secret; it does not need
   to be shared with teachers or students afterward.
3. Sign in as the administrator, create a registration invite for each private
   workspace user, or create a course and share its course invitation with the
   relevant students.
4. Test isolation with two accounts: account A must receive `403` when reading
   or modifying A's private project from account B. Test a job submission and
   artifact download for both accounts.

The platform is then available at the HTTPS origin. A browser guest identity is
also supported for the public bulletin-board mode; all of its projects and
artifacts are public. For a server intended only for private work, do not direct
users to that mode and treat an application-level guest-disable switch as a
future hardening item.

Services:

- `frontend`: static Viewer plus reverse proxy to the API
- `api`: FastAPI and Alembic migration entrypoint
- `worker`: persistent RQ worker for OSM, generation, evaluation, and export
- `postgres`: users, tenancy, revisions, evaluation provenance, jobs, and audit log
- `redis`: durable queued-work coordination
- `minio`: project-scoped GeoJSON, layouts, GLB files, screenshots, and bundles

Back up the `postgres_data` and `minio_data` volumes together. Redis append-only persistence is enabled, while PostgreSQL remains the job system of record; interrupted `queued` or `running` jobs are recovered when the worker starts.

### Capacity, backups, and updates

- One RQ worker is configured by default. `ROADGEN_MAX_ACTIVE_JOBS_PER_USER=3`
  enforces a per-user queue quota, not a global CPU/GPU quota. Start with one
  worker, observe memory/GPU/LLM throughput, then add workers only after setting
  a host-wide concurrency policy. Generation and evaluation can be long-running.
- Back up PostgreSQL and MinIO together; restoring only one breaks artifact
  references. Perform a restore drill before calling the service production
  ready. Redis can be rebuilt from the PostgreSQL job records.
- Before an upgrade, record `docker compose ... ps`, back up the two durable
  stores, then run `docker compose ... up -d --build`. Alembic runs in the API
  and worker entrypoints; review migration changes in the release before an
  irreversible database upgrade.
- Daily checks: `docker compose ... ps`, recent worker logs, disk capacity,
  failed-job count, and the external HTTPS health endpoint. Keep the host OS,
  Docker images, and Python/Node dependencies on a regular patch schedule.

## Production boundaries

- Production course APIs return IDs and controlled artifact downloads, never absolute server paths.
- Students can access only projects they own. Teachers can access projects in courses they teach; admins can administer all courses.
- The public Compose frontend proxies only `/api/v1` plus `/api/health`; it
  rejects the legacy `/api/*` research routes. Never publish the FastAPI port
  directly as a workaround.
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
