# RoadGen3D

**Text-to-3D Urban Street Scene Generation**

RoadGen3D is a neuro-symbolic system that transforms text descriptions into detailed 3D urban street scenes. Given a natural language query like *"modern clean urban street"*, it retrieves relevant assets, plans a street layout with design-rule constraints, and exports a complete 3D scene (GLB/PLY).

## Pipeline Overview

### Core Generation Pipeline

```
Text Prompt
    │
    ▼
┌──────────┐    ┌────────────────┐    ┌────────────────┐    ┌──────────────┐
│  CLIP +  │───▶│ StreetProgram  │───▶│  LayoutSolver  │───▶│  Mesh Export  │
│  FAISS   │    │ + Constraints  │    │  (collision,   │    │  (GLB / PLY)  │
│ Retrieve │    │  (M6)          │    │   rules, ...)  │    │               │
└──────────┘    └────────────────┘    └────────────────┘    └──────────────┘
```

### Auto Scene Pipeline (LLM-driven closed loop)

Accepts a Viewer-exported graph JSON and an optional reference base-map image, then automatically iterates: **generate → render preview → LLM evaluate → improve** until score convergence.

```
graph.json (Viewer export)       base_map.png (optional)
        │                               │
        ▼                               ▼
 ┌──────────────┐               ┌───────────────┐
 │ Graph Parser │               │ LLM Context   │
 │ → overrides  │               │ → config_patch│
 └──────┬───────┘               └───────┬───────┘
        │                               │
        └───────────┬───────────────────┘
                    ▼
          ┌──────────────────┐
          │ compose_street   │──▶  scene_layout.json + scene.glb
          │ _scene()         │
          └────────┬─────────┘
                   ▼
          ┌──────────────────┐
          │ Render top-down  │──▶  preview.png
          └────────┬─────────┘
                   ▼
          ┌──────────────────┐
          │ LLM Evaluate     │──▶  score + suggestions + config_patch
          └────────┬─────────┘
                   │
             Score improved?
              Yes → apply patch → loop
              No  ×2 → early stop
```

## Milestones

| Milestone | Capability | Status |
|-----------|-----------|--------|
| **M1** | Single-asset pipeline: `text → FAISS → latent → voxel → mesh` | Done |
| **M2** | Real data pipeline (Blender-free `mesh_ref` encoding) | Done |
| **M3** | Multi-asset street composition (retrieval + dedup + collision + export) | Done |
| **M4** | Learnable layout policy + engineering evaluation loop | Done |
| **M5** | OpenStreetMap integration with POI-aware generation | In progress |
| **M6** | Neuro-symbolic generation (StreetProgram + ConstraintSet + LayoutSolver) | Done (v1) |
| **Auto** | LLM-driven auto pipeline: graph → generate → evaluate → iterate closed loop | Done (v1) |

## Quick Start

### Prerequisites

- Python 3.11+ (tested on macOS arm64)
- Git (with submodule support)
- Node.js (for web workbench & viewer)

### Install

```bash
# Clone with submodules
git clone https://github.com/GIStudio/RoadGen3D.git
cd RoadGen3D
git submodule update --init

# Python dependencies
.venv/bin/python -m pip install -r requirements-m1.txt
.venv/bin/python -m pip install -r requirements-m2.txt
.venv/bin/python -m pip install -r requirements-ui.txt

# Frontend dependencies
make workbench-install
make viewer-install

# Download CLIP model (offline)
huggingface-cli download openai/clip-vit-base-patch32 \
  --local-dir models/clip-vit-base-patch32
```

### Run

**Start the full development environment** (API + Workbench + Viewer):

```bash
make dev
```

This launches three services:
- **API** — `http://127.0.0.1:8010`
- **Workbench** — `http://127.0.0.1:4174`
- **Viewer** — `http://127.0.0.1:4173`

Or start individual services via `make workbench-api`, `make workbench-web`, `make viewer-web`.

## CLI Usage

### Generate a Street Scene

```bash
.venv/bin/python scripts/m3_01_compose_street.py \
  --query "modern clean urban street" \
  --manifest data/real/real_assets_manifest.jsonl \
  --artifacts artifacts/real \
  --out-dir artifacts/real \
  --length-m 80 \
  --road-width-m 8 \
  --sidewalk-width-m 2.5 \
  --density 1.0 \
  --seed 42 \
  --design-rule-profile balanced_complete_street_v1 \
  --model-dir models/clip-vit-base-patch32 \
  --local-files-only \
  --export-format both
```

Output: `artifacts/real/scene.glb`, `artifacts/real/scene_layout.json`

### Auto Scene Pipeline

Automatically generate, evaluate, and iteratively improve a street scene from a Viewer-exported graph JSON:

```bash
.venv/bin/python scripts/auto_scene_pipeline.py \
  --graph-json path/to/exported_graph.json \
  --base-map path/to/reference.png \
  --output-dir artifacts/auto_pipeline/my_scene \
  --manifest data/real/real_assets_manifest.jsonl \
  --model-dir models/clip-vit-base-patch32 \
  --max-iterations 5 \
  --query "modern clean urban street" \
  --local-files-only
```

| Parameter | Description | Default |
|-----------|-------------|---------|
| `--graph-json` | Viewer-exported graph JSON (required) | — |
| `--base-map` | Reference base-map PNG (optional) | — |
| `--output-dir` | Output root directory | `artifacts/auto_pipeline` |
| `--manifest` | Asset manifest JSONL path | `data/real/real_assets_manifest.jsonl` |
| `--model-dir` | CLIP model directory | `models/clip-vit-base-patch32` |
| `--max-iterations` | Maximum generate-evaluate-improve iterations | `5` |
| `--query` | Text description guiding design | `"modern clean urban street"` |
| `--local-files-only` | Offline mode (no model downloads) | `False` |

Output structure:

```
artifacts/auto_pipeline/my_scene/
├── iter_00/
│   ├── scene_layout.json
│   ├── scene.glb
│   ├── preview.png
│   ├── evaluation.json
│   └── config_patch.json
├── iter_01/
│   └── ...
├── final/
│   ├── scene_layout.json    # best result
│   ├── scene.glb
│   └── preview.png
└── iteration_log.json
```

Stop conditions: early stop after 2 consecutive rounds without score improvement, or when `--max-iterations` is reached.

### Multi-Version Auto Evaluation

Run multiple design queries through the full pipeline in one shot. Each query goes through the LLM-driven generate → evaluate → iterate loop, renders presentation views for the best result, and produces a consolidated evaluation report.

```bash
.venv/bin/python scripts/run_auto_eval.py \
  --output-dir artifacts/auto_eval_$(date +%Y%m%d_%H%M%S) \
  --max-iterations 3 \
  --queries "modern transit boulevard" \
            "pedestrian-friendly green street" \
            "commercial shopping district street" \
  --manifest data/real/real_assets_manifest.jsonl \
  --model-dir models/clip-vit-base-patch32 \
  --local-files-only \
  --device cpu
```

| Parameter | Description | Default |
|-----------|-------------|---------|
| `--output-dir` | Root output directory | `artifacts/auto_eval_<timestamp>` |
| `--max-iterations` | Max iterations per query | `3` |
| `--queries` | Design queries (space-separated) | 3 built-in queries |
| `--template-id` | Graph template ID | `hkust_gz_gate` |
| `--manifest` | Asset manifest JSONL path | `data/real/real_assets_manifest.jsonl` |
| `--model-dir` | CLIP model directory | `models/clip-vit-base-patch32` |
| `--local-files-only` | Offline mode | `False` |
| `--device` | Torch device | `cpu` |

Output structure:

```
artifacts/auto_eval_<timestamp>/
├── version_00_modern_transit_boulevard/
│   ├── iter_00/ ... iter_02/
│   ├── final/
│   │   ├── scene_layout.json
│   │   ├── scene.glb
│   │   ├── preview.png
│   │   └── presentation_views/
│   │       ├── final_plan_axonometric.png
│   │       ├── final_oblique_45_axonometric.png
│   │       ├── hero_left.png
│   │       ├── hero_right.png
│   │       └── overview_top_design.png
│   └── iteration_log.json
├── version_01_pedestrian_friendly_green_street/
├── version_02_commercial_shopping_district_street/
└── eval_report.json
```

### Single Asset Pipeline

```bash
.venv/bin/python scripts/m1_06_run_pipeline.py \
  --query "a wooden park bench" \
  --topk 1 \
  --data-dir data/m1 \
  --artifacts artifacts/m1 \
  --model-dir models/clip-vit-base-patch32 \
  --local-files-only \
  --decoder placeholder \
  --export-format both
```

### M1 Step-by-Step Pipeline

```bash
# 1. Environment check
.venv/bin/python scripts/m1_00_check_env.py --out artifacts/m1/env_report.json

# 2. Generate mock data
.venv/bin/python scripts/m1_01_seed_assets.py --out-dir data/m1 --num-assets 8 --seed 42

# 3. Encode asset texts
.venv/bin/python scripts/m1_02_embed_texts.py \
  --assets data/m1/assets.jsonl \
  --out artifacts/m1 \
  --model-dir models/clip-vit-base-patch32 \
  --local-files-only

# 4. Build FAISS index
.venv/bin/python scripts/m1_03_build_faiss.py \
  --embeds artifacts/m1/asset_text_embeds.npy \
  --asset-ids artifacts/m1/asset_ids.json \
  --out artifacts/m1

# 5. Standalone retrieval verification
.venv/bin/python scripts/m1_04_retrieve.py \
  --query "a wooden park bench" \
  --topk 3 \
  --artifacts artifacts/m1 \
  --model-dir models/clip-vit-base-patch32 \
  --local-files-only

# 6. Full single-asset pipeline
.venv/bin/python scripts/m1_06_run_pipeline.py \
  --query "a wooden park bench" \
  --topk 1 \
  --data-dir data/m1 \
  --artifacts artifacts/m1 \
  --model-dir models/clip-vit-base-patch32 \
  --local-files-only
```

### Learnable Layout Policy (M4)

```bash
# Collect distilled policy data
.venv/bin/python scripts/m4_01_collect_policy_data.py \
  --manifest data/real/real_assets_manifest.jsonl \
  --artifacts artifacts/real \
  --out artifacts/m4/policy_train.jsonl \
  --model-dir models/clip-vit-base-patch32 \
  --local-files-only

# Train layout policy
.venv/bin/python scripts/m4_02_train_layout_policy.py \
  --data artifacts/m4/policy_train.jsonl \
  --out-dir artifacts/m4 \
  --device cpu

# Use learned policy
.venv/bin/python scripts/m3_01_compose_street.py \
  --query "modern clean urban street" \
  --manifest data/real/real_assets_manifest.jsonl \
  --artifacts artifacts/real \
  --out-dir artifacts/real \
  --placement-policy learned \
  --policy-ckpt artifacts/m4/layout_policy.pt \
  --policy-temperature 0.12 \
  --model-dir models/clip-vit-base-patch32 \
  --local-files-only

# Evaluate engineering metrics
.venv/bin/python scripts/m4_10_eval_engineering.py \
  --queries data/eval/queries_m4.txt \
  --manifest data/real/real_assets_manifest.jsonl \
  --artifacts artifacts/real \
  --out-dir artifacts/m4 \
  --placement-policy learned \
  --policy-ckpt artifacts/m4/layout_policy.pt \
  --compare-rule \
  --model-dir models/clip-vit-base-patch32 \
  --local-files-only
```

Key metrics: `diversity_ratio`, `dropped_slot_rate`, `overlap_rate`, `retrieval_top3_category_hit`, `latency_ms`

Reports: `artifacts/m4/eval_report.json`, `artifacts/m4/eval_per_scene.csv`

### OSM + POI Street Scene (M5)

```bash
# Fetch OSM data for an AOI
.venv/bin/python scripts/m5_01_fetch_osm.py --bbox 116.39 39.90 116.40 39.91

# Generate with real OSM geometry + POI constraints
.venv/bin/python scripts/m3_01_compose_street.py \
  --query "urban residential" \
  --layout-mode osm \
  --constraint-mode soft \
  --aoi-bbox 116.39 39.90 116.40 39.91 \
  --manifest data/real/real_assets_manifest.jsonl \
  --artifacts artifacts/real \
  --out-dir artifacts/real \
  --model-dir models/clip-vit-base-patch32 \
  --local-files-only

# Evaluate POI compliance
.venv/bin/python scripts/m5_10_eval_compliance.py \
  --scene-dir artifacts/m4/eval_scenes/rule
```

| Flag | Default | Description |
|------|---------|-------------|
| `--layout-mode` | `template` | `template` (straight road) or `osm` (real geometry) |
| `--constraint-mode` | `soft` | `off` or `soft` (POI penalty scoring) |
| `--aoi-bbox` | None | `MIN_LON MIN_LAT MAX_LON MAX_LAT` (required for osm mode) |
| `--poi-rule-set` | `entrance_fire_bus_stop_v1` | Rule set name |

### Scene-Ready Asset Curation

Refresh manifest metadata after adding or replacing assets:

```bash
.venv/bin/python scripts/m3_04_clean_asset_manifest.py \
  --manifest data/real/real_assets_manifest.jsonl --write
```

The cleaner computes `mesh_face_count`, assigns `quality_tier`, flags `scene_eligible`, and writes `quality_notes`.

## Project Structure

```
RoadGen3D/
├── src/roadgen3d/          # Core Python library
│   ├── street_program.py   # StreetProgram declarative representation
│   ├── constraint_set.py   # Design rule constraints
│   ├── layout_solver.py    # Layout optimization with collision detection
│   ├── layout_policy.py    # Learnable MLP for asset selection
│   ├── auto_pipeline/      # LLM-driven auto scene generation loop
│   │   ├── graph_loader.py         # Parse Viewer graph JSON → scene overrides
│   │   ├── scene_renderer.py       # Matplotlib top-down preview rendering
│   │   ├── iteration_controller.py # Generate → evaluate → improve loop
│   │   └── cli.py                  # (entry point via scripts/)
│   ├── llm/                # LLM design assistant (optional)
│   │   ├── glm_client.py
│   │   ├── prompts.py
│   │   └── design_workflow.py
│   ├── services/           # API & runtime services
│   │   ├── generation_core.py      # Scene generation logic
│   │   ├── generation_api.py       # FastAPI routes
│   │   ├── design_runtime.py       # LLM design runtime
│   │   ├── design_types.py         # Data types
│   │   └── scene_jobs.py           # Async job queue
│   └── ...
├── scripts/                # CLI tools (m1_*, m2_*, m3_*, m4_*, m5_*)
│   ├── auto_scene_pipeline.py      # Auto pipeline CLI entry point
│   └── run_auto_eval.py            # Multi-version auto evaluation
├── web/
│   ├── api/                # FastAPI backend service
│   ├── workbench/          # Vite + React generation workbench
│   └── viewer/             # Three.js 3D scene viewer (submodule)
├── data/                   # Asset manifests, materials, training data
├── knowledge/              # Complete Streets design guide + RAG index
├── models/                 # Pre-trained CLIP model
├── artifacts/              # Generated outputs (scenes, meshes, eval reports)
├── tests/                  # Test suites
└── tools/
    └── download3dAssets/   # UrbanVerse asset batch downloader (submodule)
```

## System Architecture

### Text Retrieval

1. Encode query with CLIP `get_text_features`
2. L2 normalize
3. FAISS `IndexFlatIP` inner-product search

```
q = normalize(clip_text(query))
result = argmax_z (q^T z)
```

### Decoders

| Decoder | Description |
|---------|-------------|
| `placeholder` | Lightweight reproducible decoder; outputs `voxel_prob` + `voxel_bin` |
| `shapee` | Real latent / mesh reference decoding with fallback to placeholder |

### Mesh Export

- Default: `marching_cubes`
- Fallback: `cubes`
- Output formats: GLB (display) + PLY (debug)

### Neuro-Symbolic Street Generation (M6)

The default generation pipeline uses explicit intermediate representations:

1. **StreetProgram** — Declarative street description: road type, cross-section, functional zones, street furniture requirements, control points, design goals
2. **ConstraintSet** — Hard/soft design rules (not hardcoded penalties)
3. **LayoutSolver** — Placement optimization with collision detection, outputs `slot_plans / edits / conflicts / rule_evaluations`

Built-in design rule profiles:
- `balanced_complete_street_v1`
- `pedestrian_priority_v1`
- `transit_priority_v1`

### OSM + POI Integration (M5)

The system integrates real-world spatial data:

1. **OSM Ingest** — Fetches Overpass data, parses roads/buildings/POI, projects to local metric coordinates
2. **Road Discovery** — Scores candidate roads by POI density, length, and relevance
3. **POI-Driven Cross-Section** — Adjusts sidewalk widths based on nearby POI (transit, entrance, parking, etc.)
4. **Segment Graph** — Discretizes roads into segment/node graph with band/POI context
5. **Placement Context** — Generates road polygons, sidewalk polygons, valid placement zones

Normalized POI types: `entrance`, `bus_stop`, `fire_hydrant`, `crossing`, `traffic_signals`, `parking_entrance`, `subway_entrance`, `post_box`, `waste_basket`, `bollard`

### Auto Scene Pipeline

An LLM-driven closed-loop system that accepts a Viewer-exported road network graph and iteratively produces optimal street scenes:

1. **Graph Parser** (`graph_loader.py`) — Reads a `ConvertedGraphPayload` JSON, calls the existing `parse_reference_annotation()` + `build_reference_annotation_scene_bridge()` pipeline, and extracts a `GraphSceneContext` with road segment graph, projected features, placement context, and a summary dict for the LLM.
2. **LLM Initial Design** (`design_workflow.py`) — Sends the graph summary + optional base-map image to the LLM, which proposes initial `compose_config_patch` parameters.
3. **Scene Generation** (`design_runtime.py`) — Calls `compose_street_scene()` with `layout_mode="graph_template"` and the parsed graph overrides.
4. **Preview Rendering** (`scene_renderer.py`) — Renders a matplotlib top-down schematic from `scene_layout.json`, with category-colored markers, road/sidewalk regions, bounding boxes, scale bar, and legend.
5. **LLM Evaluation** — Reuses `evaluate_scene()` to score the scene and suggest parameter adjustments.
6. **Iteration Controller** (`iteration_controller.py`) — Loops steps 3–5, applying LLM-suggested config patches. Stops early after 2 consecutive rounds without score improvement.

### Key Architecture Decisions

- **OSM mode is the primary generation path.** `template` mode is retained for compatibility and debugging.
- **StreetProgram → ConstraintSet → LayoutSolver** is the explicit intermediate backbone. No direct query-to-slot black box.
- **POI is a hard generation input**, not just visualization. Asset-backed POI bind to anchored slots; missing categories cause explicit failure, not silent degradation.
- **Sidewalk widths are POI-driven** in OSM mode, not fixed. Cross-section synthesis adjusts widths based on POI pressure.
- **Learned backends** (program generator, layout policy) are enhancement layers. The system always falls back to heuristic/rule defaults when checkpoints are unavailable.

## Testing

### Automated Pipeline Tests

The test suite in `tests/test_auto_eval.py` validates the full pipeline end-to-end. Tests 1–4 call the real LLM API (auto-skipped if `glm_base_url` and `key` are not set in `.env`), while test 5 uses a mock service for deterministic early-stop verification.

```bash
# Run all tests (real-LLM tests auto-skip without API credentials)
.venv/bin/python -m pytest tests/test_auto_eval.py -v

# Force-skip real-LLM tests (only mock + presentation tests)
GLM_SKIP=1 .venv/bin/python -m pytest tests/test_auto_eval.py -v
```

| Test | LLM | What it verifies |
|------|-----|-----------------|
| `TestAutoEvalGeneratesMultipleVersions` | Real | Multiple queries produce distinct iteration dirs, final/, and different config patches |
| `TestAutoEvalSavesIterationLogs` | Real | `iteration_log.json` has correct structure (score, evaluation, suggestions, config_patch) |
| `TestAutoEvalRendersPresentationViews` | None | `render_presentation_views()` outputs valid view dicts |
| `TestAutoEvalProducesEvalReport` | Real | `eval_report.json` aggregates all versions with plausible scores in [0, 10] |
| `TestAutoEvalLLMIterationsImproveOrStop` | Mock | Controller stops after ≤3 iterations when scores stagnate |

## Web API

The canonical API entry point is `web/api/main.py`. Scene generation runs as async jobs:

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/scene/jobs` | Submit a generation job |
| GET | `/api/scene/jobs` | List all jobs |
| GET | `/api/scene/jobs/{job_id}` | Get job status / result |
| GET | `/api/scenes/recent` | List recent scenes |
| POST | `/api/design/generate` | Legacy synchronous endpoint |

Swagger UI: `http://127.0.0.1:8010/docs`

## Environment Variables

Create a `.env` file in the project root:

```bash
key=your_api_key
glm_base_url=https://open.bigmodel.cn/api/coding/paas/v4
GRAPHRAG_API_KEY=your_graphrag_key
GRAPHRAG_API_BASE=https://api.example.com/v1/
```

## Make Targets

```bash
make help                 # Show all available targets
make dev                  # Start API + workbench + viewer
make workbench-api        # Start FastAPI backend (port 8010)
make workbench-web        # Start Vite workbench (port 4174)
make viewer-web           # Start 3D viewer (port 4173)
make knowledge-build      # Build RAG knowledge base from design guide PDF
make collect              # Collect M4 policy training data
make train                # Train layout policy
make eval                 # Run engineering evaluation
```

## Roadmap

### Near-term

- Stabilize OSM + POI + width synthesis as the default generation path
- Strengthen constraint-type POI influence on layout (crossing, traffic_signals, subway_entrance, parking_entrance)
- Improve cross-section synthesis readability in UI summaries

### Mid-term

- Expand POI taxonomy to more complete street furniture system
- Make segment-level graph participate in layout (not just global bands)
- Deepen learned program generator integration as a strong backend

### Long-term

- Support small street networks (multi-road, junctions)
- Evolve from "asset placement" to a full "street design system" with editable cross-section presets
- Standardize research loop with versioned training data, fixed evaluation protocols, and result dashboards

### Not prioritized

- Full building geometry generation
- Large-scale city-level road network modeling
- Removing all heuristic/rule fallbacks
- Complex multi-agent traffic simulation

## Current Limitations

- No cross-modal training (OpenShape/ULIP) — retrieval is CLIP text-only
- `shapee` direct latent decoding requires matching latent dimensions; production use recommends `mesh_ref`
- M3 is single-segment straight road template — no complex intersections or curved networks
- `StreetProgram` uses heuristic generator (`heuristic_v1`) — not yet replaced by a learned program generator
- Layout solver uses `banded` heuristic — not MILP or diffusion-based

## License

This project is developed by [GIStudio](https://github.com/GIStudio).
