# RoadGen3D Current Progress

> Status: current integration map
> Last updated: 2026-05-15
> Scope: Summarize the current implementation, architecture, documentation map, active gaps, and dropped/archived surfaces. This page is a routing layer over the existing Markdown files, not a replacement for the detailed source documents.

## Tag Syntax

| Tag | Meaning |
| --- | --- |
| `[now]` | Current implementation, current source of truth, or active user-facing path. |
| `[next]` | Immediate next work that should be done before expanding scope. |
| `[todo]` | Known gap, missing contract, validation, cleanup, or documentation fix. |
| `[plan]` | Designed or intended direction that still needs implementation, calibration, or productization. |
| `【drop】` | Historical, archived, legacy, third-party, or explicitly not part of the current mainline. Keep for reference only. |

## Current Reading Order

If documents disagree, use this order:

1. `[now]` [ROADGEN3D_FRAMEWORK.md](ROADGEN3D_FRAMEWORK.md) for current product framing and main flow.
2. `[now]` [ACTIVE_ENTRYPOINTS.md](ACTIVE_ENTRYPOINTS.md) for active entrypoints and submodule boundaries.
3. `[now]` [DATA_CONTRACTS.md](DATA_CONTRACTS.md) for schema, request, job, and `scene_layout.json` contracts.
4. `[now]` [EVALUATION.md](EVALUATION.md) for scoring APIs, evaluation fields, and road-metrics boundaries.
5. `[now]` [DEPLOYMENT_AND_JOBS.md](DEPLOYMENT_AND_JOBS.md) for local services, job service limits, and artifact boundaries.
6. `[now]` [README.md](../readme.md) for quick start and high-level repo orientation.
7. `[plan]` [features/README.md](features/README.md) for feature-specific plans and still-useful design notes.
8. `【drop】` [archive/README.md](archive/README.md)、`legacy/web_workbench`（兼容入口 `web/workbench`）及第三方/vendor 文档仅作历史参考。

## One-Line Project State

`[now]` RoadGen3D is a rule/constraint-driven, AI-assisted 3D street-scene generation and evaluation framework. Its current stable demo path is Scenario Designs in Viewer: structured scenario catalog entries become `template_patch` and `compose_config_patch`, then flow through graph-template context, `compose_street_scene()`, `scene_layout.json` / GLB artifacts, road-metrics evaluation, and Viewer analysis.

`[now]` The strongest project claim is not "black-box prompt-to-3D"; it is "structured design intent -> explicit intermediate representation -> controllable 3D scene -> evaluable and comparable result."

## Active Main Flow

```text
[now] data/scenario_designs/*.json
  -> [now] web/viewer Scenario Designs panel
  -> [now] web/api/main.py /api/scenario-designs/runs
  -> [now] ScenarioDesignService
  -> [now] DesignDraft with preset_id=skip_llm
  -> [now] graph_template SceneContext + template_patch
  -> [now] SceneJobService
  -> [now] build_graph_template_scene_bridge()
  -> [now] compose_street_scene()
  -> [now] StreetProgram + ConstraintSet + LayoutSolverResult
  -> [now] scene_layout.json + scene.glb / scene.ply
  -> [now] road-metrics EvalEngine
  -> [now] Viewer 3D display, evaluation, compare, reports, history
```

`[now]` Design / Branch / Pareto paths remain active for prompt, preset, benchmark, and exploration workflows, but they are not the same as the Scenario Designs batch path.

`[plan]` OSM, MetaUrban, reference annotation, learned placement, LLM/RAG draft, and auto-pipeline are useful expansion surfaces. They should be described as supported or experimental paths, not as the default current demo.

## Architecture State

| Layer | Status | Main paths | Current role |
| --- | --- | --- | --- |
| UI / Studio | `[now]` | `web/viewer` | Active Viewer, Scenario Designs, Design workspace, 3D scene viewing, asset editor, scene graph, evaluation, compare, benchmark explorer. |
| API | `[now]` | `web/api/main.py` | FastAPI business entrypoint for design, generation jobs, branch runs, evaluation, knowledge, assets, and diff. |
| Workflow services | `[now]` | `src/roadgen3d/services/*` | Runtime option resolution, design draft handling, scene jobs, branch runs, benchmark records, scenario design orchestration. |
| Generation core | `[now]` | `src/roadgen3d/street_layout.py`, `types.py`, `street_program.py`, `design_rules.py`, `layout_solver.py` | Program generation, constraints, layout solving, slot planning, assets, buildings, GLB/JSON output. |
| Data contracts | `[now]` | `data/schemas/scene_layout.schema.json`, `docs/DATA_CONTRACTS.md` | `scene_layout.json` has `schema_version=roadgen3d.scene_layout.v1`; more summary/API schemas still need refinement. |
| Evaluation | `[now]` | `src/roadgen3d/eval_engine_ext/road_metrics` | Active road-metrics engine for walkability, safety, beauty, overall, child-friendly auxiliary scoring, and scenario rubric inputs. |
| Viewer local adapter | `[now][todo]` | `web/viewer/vite.config.ts` | Local development file and manifest APIs. Useful now, but production-equivalent FastAPI/artifact service boundaries still need cleanup. |
| Legacy UI | `【drop】` | `legacy/web_workbench` | Historical Workbench only. Do not add new features there. |

## Submodule and External Boundaries

| Path | Status | Boundary |
| --- | --- | --- |
| `web/viewer` | `[now]` | Active frontend submodule and current user-facing surface. |
| `src/roadgen3d/eval_engine_ext` | `[now]` | Active evaluation engine submodule; `src/roadgen3d/eval_engine` is only a compatibility facade. |
| `tools/download3dAssets` | `[plan]` | Asset tooling support; not a main runtime surface. |
| `vendor/RoadPen` | `【drop】` | Vendor/editor reference; not RoadGen3D source of truth. |
| `vendor/RoadGen` | `【drop】` | Vendor reference; not RoadGen3D source of truth. |
| `metaurban/**` | `【drop】` for project docs | Third-party / external dependency docs unless a RoadGen3D integration task explicitly requires them. |

## Implemented Capability Map

### Generation

- `[now]` Scenario Designs batch generation uses catalog JSON, template patches, config patches, `skip_llm`, `SceneJobService`, and the same core generation kernel.
- `[now]` Generation retains explicit intermediate representations: `DesignDraft`, `SceneContext`, `StreetComposeConfig`, `StreetProgram`, `ConstraintSet`, `LayoutSolverResult`, and `scene_layout.json`.
- `[now]` `SceneContext.layout_mode` supports `graph_template`, `metaurban`, `osm`, and `template`.
- `[now]` Template patch operations can resize, update, remove, add, and replace cross-section strips and functional zones.
- `[now]` A/B semantic design layers are recorded: A = skeleton design, B = street furniture profile, C = runtime environment state.
- `[next]` Keep backend preset definitions as the authority and reduce drift from Viewer-local presets.
- `[todo]` Make historical layout loading and migration explicit for older `scene_layout.json` files.
- `[todo]` Split file-level `outputs` from returned `SceneComposeResult.outputs`.

### Viewer

- `[now]` Viewer is the current product shell for Scenario Designs, Design Workspace, Branch/Pareto trace, 3D inspection, Asset Editor, Scene Graph, evaluation panels, and comparison.
- `[now]` New Viewer work should go into focused modules such as `viewer-*-controller.ts`, `viewer-*.ts`, or helper modules; `src/app.ts` should remain wiring/composition.
- `[now]` Viewer dev middleware reads local layouts, recent artifacts, asset manifests, files, and diff images during local development.
- `[todo]` Production-critical Viewer middleware APIs need FastAPI or artifact-service equivalents.
- `[todo]` Keep panel/sidebar changes scroll-safe and module-scoped; avoid putting business logic directly into `app.ts`.

### Evaluation

- `[now]` The current unified evaluation API is `/api/design/evaluate/unified`, routed through `DesignAssistantService.evaluate_scene_unified()` into road-metrics.
- `[now]` Evaluation consumes real `scene_layout.json` and optional Viewer captured `rendered_views`.
- `[now]` Current score dimensions are `walkability`, `safety`, `beauty`, and `overall`; `child_friendly` is auxiliary and not included in `overall`.
- `[now]` Default profile is `local_segment_v1`; `network_v1` is reserved for larger-scale network cases.
- `[now]` Scenario rubric evaluation is a separate deterministic layer using `data/scenario_designs/hkust_gz_gate_evaluation_rubric.json`.
- `[todo]` Freeze `EvaluateRequestModel` and response schema.
- `[todo]` Document `indicators`, `llm_status`, and `None` / `N/A` / failure semantics as stable enums/field tables.
- `[next]` Build benchmark scenes with fixed seed, asset manifest, evaluation config, rendered views, and golden evaluation outputs.
- `[plan]` Road-engineering-grade evaluation still needs lane-level conflict points, crossing exposure time, signal/control availability, delay, reachability, and simulation-derived surrogate safety metrics.

### Assets and Data

- `[now]` Street furniture, buildings, materials, sky, and graph templates are part of the generation surface.
- `[now]` Building manifest work has registered lightweight Kenney assets and a smaller UrbanVerse subset for selective higher-realism use.
- `[now]` Some assets and model files are intentionally local or externally distributed, documented in [DATA_RECOVERY.md](DATA_RECOVERY.md).
- `[todo]` Reconcile asset-count drift across docs: older inventory docs, recent furniture manifest notes, building survey, and README do not all describe the same inventory snapshot.
- `[todo]` Decide which asset inventory document becomes authoritative for current numbers.
- `[plan]` Keep image-first/generated-artifact strategy aligned with benchmark and artifact-retention policies.

### Jobs, Artifacts, and Deployment

- `[now]` Local development usually runs FastAPI on `127.0.0.1:8010` and Viewer on `127.0.0.1:4173` or the next available port.
- `[now]` `SceneJobService` is a single-process, single-thread, memory-backed local/demo job system.
- `[now]` Primary artifacts are `scene_layout.json`, GLB/PLY outputs, `production_steps`, presentation renders, placement decision logs, and Viewer cached layouts.
- `[todo]` Add durable job records, restart recovery, cancel/retry/timeout, and concurrency control.
- `[todo]` Add artifact registry, cleanup policy, immutable run ids, and generated output manifests.
- `[plan]` Production deployment should move local file APIs behind a proper backend/artifact service with access policy.

## Documentation Integration Map

### Root and Active Docs

| Document | Status | Use it for |
| --- | --- | --- |
| `readme.md` | `[now][todo]` | Quick start and high-level repo architecture. Needs small consistency cleanup around active vs vendor submodules. |
| `todo.md` | `[todo]` | Small legacy to-do list. Fold actionable items into this progress map or issue tracker later. |
| `docs/README.md` | `[now]` | Primary documentation navigation entrypoint. |
| `docs/current-progress.md` | `[now]` | This integrated progress map. |
| `docs/PROJECT_LAYOUT.md` | `[now]` | Directory split guide and active/legacy boundary map. |
| `docs/ACTIVE_ENTRYPOINTS.md` | `[now]` | Current entrypoint and legacy alias boundaries. |
| `docs/ROADGEN3D_FRAMEWORK.md` | `[now]` | Current architecture and main flow source of truth. |
| `docs/DATA_CONTRACTS.md` | `[now]` | Contracts for request models, `scene_layout.json`, semantic layers, and job status. |
| `docs/EVALUATION.md` | `[now]` | Evaluation API, score dimensions, profiles, and known missing evaluation capabilities. |
| `docs/DEPLOYMENT_AND_JOBS.md` | `[now]` | Local service topology, job service boundaries, and artifact roadmap. |
| `docs/PROJECT_SUMMARY_FOR_MEETING.md` | `[now][todo]` | Meeting summary and talk framing. Update stale schema/version wording against `DATA_CONTRACTS.md`. |
| `docs/ROADGEN3D_3D_GENERATION_TALK.md` | `[now]` | 5-minute video or group-meeting narrative. |

### Assets, Data, and Recovery Docs

| Document | Status | Use it for |
| --- | --- | --- |
| `docs/ASSET_INVENTORY.md` | `[now][todo]` | Asset inventory snapshot. Needs reconciliation with newer furniture/building counts. |
| `docs/BUILDING_ASSET_SURVEY_2026-05-09.md` | `[now]` | Current building asset import and render-check record. |
| `docs/DATA_RECOVERY.md` | `[now]` | Missing local data, models, internal assets, and minimum runtime data. |
| `assets/scene/场景方案.md` | `[plan]` | Scenario source material / presentation reference, not an implementation contract. |
| `assets/building/external/README.md` | `[plan]` | External building asset packaging context. |
| `knowledge/graphRAG/README.md` | `[plan]` | RAG data setup/supporting knowledge context. |

### Feature Docs

| Document | Status | Use it for |
| --- | --- | --- |
| `docs/features/README.md` | `[now]` | Feature-topic index. |
| `docs/features/SCENARIO_DESIGN_OPTIONS.md` | `[plan]` | Scenario design interpretation and design option expansion. |
| `docs/features/roadgen3d_scenario_plan.md` | `[plan]` | Scenario implementation plan, assets, and capability gaps. |
| `docs/features/design-test-workflow.md` | `[plan][todo]` | Test/report workflow design; should be reconciled with current benchmark/golden artifact roadmap. |
| `docs/features/ANALYTICAL_DIORAMA_VISUAL_DIRECTION.md` | `[plan]` | Viewer visual direction for analytical diorama mode. |
| `docs/features/junction-editor.md` | `[now][plan]` | Junction editor feature notes. |
| `docs/features/QUICK_START_JUNCTION_EDITOR.md` | `[now]` | Junction editor quick start. |
| `docs/features/cross-junction-ribbon-corner-data-layer.md` | `[plan]` | Cross-junction surface data layer design. |
| `docs/features/scatter-plot-features.md` | `[now][plan]` | Score scatter and correlation-analysis feature notes. |
| `docs/features/SCENE_COMPARE_FEATURE.md` | `[now][plan]` | Scene compare feature design. |
| `docs/features/SCENE_COMPARE_DUAL_LAYOUT.md` | `[now][plan]` | Dual-layout comparison design. |
| `docs/features/SCENE_COMPARE_NEW_LOGIC.md` | `[now][plan]` | New comparison logic. |
| `docs/features/comparison-features.md` | `[now][plan]` | Comparison feature grouping. |

### Evaluation Docs

| Document | Status | Use it for |
| --- | --- | --- |
| `src/roadgen3d/eval_engine_ext/README.md` | `[now]` | road-metrics submodule entrypoint. |
| `src/roadgen3d/eval_engine_ext/road_metrics/README.md` | `[now]` | Standalone road-metrics usage and metrics summary. |
| `src/roadgen3d/eval_engine_ext/road_metrics/LAYERED_ARCHITECTURE.md` | `[now]` | road-metrics extractor/base-metric/composer architecture. |
| `src/roadgen3d/eval_engine_ext/MIGRATION_GUIDE.md` | `[plan]` | Migration context for evaluation engine changes. |
| `src/roadgen3d/eval_engine/README.md` | `【drop】` | Compatibility facade context only. |
| `evaluation/README.md` | `【drop】` for truth | Legacy helper index that points readers back to `docs/EVALUATION.md`. |
| `evaluation/scenario_evaluation_standards.md` | `[now]` | Human-readable seven-scenario evaluation method; automatic source of truth is rubric JSON. |
| `evaluation/docs/evaluation_module_plan.md` | `【drop】` | Historical evaluation planning. |

### Viewer Docs

| Document | Status | Use it for |
| --- | --- | --- |
| `web/viewer/README.md` | `[now]` | Current Viewer entry and feature overview. |
| `web/viewer/ARCHITECTURE.md` | `[now]` | Viewer module ownership and `app.ts` guardrails. |
| `web/viewer/docs/road.md` | `[now][plan]` | Road geometry notes. |
| `web/viewer/docs/multi-lane.md` | `[now][plan]` | Multi-lane band geometry notes. |
| `web/viewer/src/styles/README.md` | `[now]` | Viewer styling organization. |
| `web/viewer/docs/archive/*` | `【drop】` | Historical refactor and G6 plans. |
| `legacy/web_workbench/README.md`, `legacy/web_workbench/tasks.md` | `【drop】` | Archived historical Workbench. |

### Archived and Third-Party Docs

| Group | Status | Rule |
| --- | --- | --- |
| `docs/archive/*` | `【drop】` | Historical architecture/evaluation/workbench documents. Do not use as current facts. |
| `metaurban/**.md` | `【drop】` | Third-party/external docs unless directly debugging MetaUrban integration. |
| `vendor/**.md` | `【drop】` | Vendor docs, not RoadGen3D product docs. |
| `tools/download3dAssets/README.md` | `[plan]` | Asset-download tooling context only. |

## Known Drift and Cleanup Queue

- `[todo]` Update [PROJECT_SUMMARY_FOR_MEETING.md](PROJECT_SUMMARY_FOR_MEETING.md): it still says `scene_layout.json` lacks `schema_version` / JSON Schema, while [DATA_CONTRACTS.md](DATA_CONTRACTS.md) documents `schema_version=roadgen3d.scene_layout.v1` and `data/schemas/scene_layout.schema.json`.
- `[todo]` Reconcile root README language about "3 submodules" with `.gitmodules`: active product submodules are Viewer and road-metrics, while tools/vendor submodules are supporting or external.
- `[todo]` Reconcile asset counts: older inventory, building survey, street furniture manifest notes, and README numbers are from different snapshots.
- `[todo]` Decide whether `todo.md` should remain a separate root file or be folded into this progress map.
- `[now]` `docs/current-progress.md` is linked from [docs/README.md](README.md) as the current progress and documentation integration map.
- `[todo]` Clarify whether feature docs should stay as design notes or be split into implemented feature docs vs future design docs.
- `[todo]` Keep OSM wording precise: OSM/context support exists, but current stable demo path is Scenario Designs over graph templates.

## Current Boundary Statements

- `[now]` We can claim: structured 3D street-scene generation, Viewer-based design workflow, explicit intermediate representations, `scene_layout.json` contract, road-metrics evaluation, scenario rubric, branch/benchmark analysis.
- `[now]` We should say carefully: AI-assisted and optionally LLM/RAG-enhanced, not pure LLM generation.
- `[now]` We should not overclaim: complete road-engineering design, traffic simulation, lane movement/control modeling, fully trained neuro-symbolic generator, or production-grade multi-user job infrastructure.
- `【drop】` We should not route new work through Workbench, archived evaluation plans, old architecture docs, or vendor docs unless the task is explicitly historical.

## Recommended Next Milestones

1. `[next]` Optionally link this file from root [README.md](../readme.md).
2. `[next]` Refresh meeting and promotion docs so they match current schema/evaluation contracts.
3. `[next]` Normalize the active source-of-truth set: framework, active entrypoints, data contracts, evaluation, deployment/jobs, current-progress.
4. `[todo]` Add API schema docs for scene job and unified evaluation responses.
5. `[todo]` Build a benchmark matrix with fixed scenarios, seeds, asset manifests, rendered views, expected evaluation profiles, and golden artifacts.
6. `[todo]` Convert Viewer local middleware production dependencies into FastAPI/artifact-service boundaries.
7. `[plan]` Extend road-engineering depth: lane connectivity, turn movements, signal/control, crossing exposure, reachability, delay, and simulation-based safety.
8. `[plan]` If "neuro-symbolic" becomes a major claim, close the loop with learned program generator data, checkpoints, ablations, and Viewer-mainline integration.
