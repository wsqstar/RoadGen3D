# Archived Legacy Workbench

`web/workbench` is retained as a legacy React implementation for reference only.
The active design, generation, trace, branch-run, evaluation, and scene-viewing
workflow now lives in `web/viewer`, backed by `web/api`.

Default development commands no longer start this app:

- `make dev` starts the API and `web/viewer`.
- `make ui-web` aliases to `viewer-web`.
- `make ui-install` aliases to `viewer-install`.

To inspect the old UI during migration archaeology, opt in explicitly:

```bash
ENABLE_ARCHIVED_WORKBENCH=1 make workbench-install
ENABLE_ARCHIVED_WORKBENCH=1 make workbench-web
```

Do not add new product features here. Port any remaining useful workflow or UI
ideas into `web/viewer` instead.
