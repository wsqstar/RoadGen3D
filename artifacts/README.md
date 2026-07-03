# RoadGen3D artifacts

Generated artifacts are not stored in this Git repository.

The full artifact snapshot was moved to the server on 2026-07-03:

```bash
docker-dev:/workspace/dev/github/gistudio/RoadGen3D/artifacts/
```

Restore a local copy when needed:

```bash
rsync -a --partial docker-dev:/workspace/dev/github/gistudio/RoadGen3D/artifacts/ artifacts/
```

Local runs may create temporary files under `artifacts/`. Those files are ignored
by Git and should stay out of public repository commits.
