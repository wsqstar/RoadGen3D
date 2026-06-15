# RoadGen3D Active Entrypoints

> Status: current  
> Last verified: 2026-05-12

RoadGen3D root is the backend business and generation orchestration repository.
The active frontend and evaluation engine are independent submodules with stable
integration contracts.

目录层说明：

- `ops/`：日常脚本/配置/示例的收敛目录（当前可见目录：`scripts/`/`configs/`/`examples/`）。
- `legacy/`：归档与停产目录（当前可见历史入口：`evaluation`、`ui`、`web/workbench`、`.archive`）。

| Surface | Active entrypoint | Responsibility | Ownership boundary |
| --- | --- | --- | --- |
| API service | `web/api/main.py` via `make api` | FastAPI routes for design, generation, scene jobs, evaluation, knowledge, assets, and artifacts | Root repo owns orchestration and schemas |
| Viewer | `web/viewer` via `make viewer-web` | React/AntD + Three.js/G6 viewer, annotation, asset editor, junction editor, evaluation display | `GIStudio/Viewer` submodule owns frontend implementation |
| Evaluation engine | `src/roadgen3d/eval_engine_ext` | road-metrics scoring engine and LLM/visual evaluators | `road-metrics` submodule owns metric internals |
| Generation core | `src/roadgen3d/services`, `src/roadgen3d/llm`, `src/roadgen3d/*.py` | Prompt/draft orchestration, graph-template composition, scene jobs, GLB/layout artifacts | Root repo owns product workflow |
| Legacy workbench | `legacy/web_workbench`（兼容入口 `web/workbench`） | Archived historical UI only | Not an active product entrypoint |

## Current Flow

`web/viewer` calls `web/api/main.py`, which orchestrates design services,
scene jobs, graph-template generation, artifact capture, and road-metrics
evaluation. The root repo remains the integration point for contracts,
artifacts, tests, and local developer commands.

## Compatibility Aliases

- `make workbench-api` remains as a deprecated alias for `make api`.
- `make ui-api`, `make ui-web`, and `make ui-install` remain compatibility
  aliases for older scripts.
- `src/roadgen3d/eval_engine` remains an import compatibility facade for
  `src/roadgen3d/eval_engine_ext/road_metrics`.
- `web/workbench`（兼容入口）和 `legacy/web_workbench` 指向同一历史目录，启动需
  `ENABLE_ARCHIVED_WORKBENCH=1`；该路径仅用于历史查看，不再新增功能。
- `evaluation`、`ui`、`.archive` 为历史目录入口的兼容保留，推荐优先从
  `legacy/` 路径访问。
