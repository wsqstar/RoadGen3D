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

### Generate a Street Scene (CLI)

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

### Auto Scene Pipeline (CLI)

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

### Single Asset Pipeline (CLI)

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
│   └── ...
├── scripts/                # CLI tools (m1_*, m2_*, m3_*, m4_*, m5_*)
│   └── auto_scene_pipeline.py      # Auto pipeline CLI entry point
├── web/
│   ├── api/                # FastAPI backend service
│   ├── workbench/          # Vite + React generation workbench
│   └── viewer/             # Three.js 3D scene viewer (submodule)
├── data/                   # Asset manifests, materials, training data
├── knowledge/              # Complete Streets design guide + RAG index
├── models/                 # Pre-trained CLIP model
├── artifacts/              # Generated outputs (scenes, meshes, eval reports)
├── docs/                   # Architecture decisions, roadmap, system review
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

### Auto Scene Pipeline

An LLM-driven closed-loop system that accepts a Viewer-exported road network graph and iteratively produces optimal street scenes:

1. **Graph Parser** (`graph_loader.py`) — Reads a `ConvertedGraphPayload` JSON, calls the existing `parse_reference_annotation()` + `build_reference_annotation_scene_bridge()` pipeline, and extracts a `GraphSceneContext` with road segment graph, projected features, placement context, and a summary dict for the LLM.
2. **LLM Initial Design** (`design_workflow.py`) — Sends the graph summary + optional base-map image to the LLM, which proposes initial `compose_config_patch` parameters.
3. **Scene Generation** (`design_runtime.py`) — Calls `compose_street_scene()` with `layout_mode="graph_template"` and the parsed graph overrides.
4. **Preview Rendering** (`scene_renderer.py`) — Renders a matplotlib top-down schematic from `scene_layout.json`, with category-colored markers, road/sidewalk regions, bounding boxes, scale bar, and legend.
5. **LLM Evaluation** — Reuses `evaluate_scene()` to score the scene and suggest parameter adjustments.
6. **Iteration Controller** (`iteration_controller.py`) — Loops steps 3–5, applying LLM-suggested config patches. Stops early after 2 consecutive rounds without score improvement.

### Learnable Layout Policy (M4)

Trains an MLP (`32 → 64 → 32 → 1`) to learn per-slot asset selection from distilled supervision data:

```bash
make collect                                          # Collect training data
make train                                            # Train layout policy
.venv/bin/python scripts/m3_01_compose_street.py \
  --placement-policy learned \
  --policy-ckpt artifacts/m4/layout_policy.pt \
  ...                                                 # Use learned policy
```

### Evaluation (M4)

```bash
make eval
```

Key metrics: `diversity_ratio`, `dropped_slot_rate`, `overlap_rate`, `retrieval_top3_category_hit`, `latency_ms`

Reports: `artifacts/m4/eval_report.json`, `artifacts/m4/eval_per_scene.csv`

## Web API

The canonical API entry point is `web/api/main.py`. Scene generation runs as async jobs:

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/scene/jobs` | Submit a generation job |
| GET | `/api/scene/jobs` | List all jobs |
| GET | `/api/scene/jobs/{job_id}` | Get job status / result |
| GET | `/api/scenes/recent` | List recent scenes |
| POST | `/api/design/generate` | Legacy synchronous endpoint |

See `API_GUIDE.md` for full documentation.

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

## Current Limitations

- No cross-modal training (OpenShape/ULIP) — retrieval is CLIP text-only
- `shapee` direct latent decoding requires matching latent dimensions; production use recommends `mesh_ref`
- M3 is single-segment straight road template — no complex intersections or curved networks
- `StreetProgram` uses heuristic generator (`heuristic_v1`) — not yet replaced by a learned program generator
- Layout solver uses `banded` heuristic — not MILP or diffusion-based

## Documentation

| Document | Description |
|----------|-------------|
| `README_M1.md` | Single-asset pipeline runbook |
| `API_GUIDE.md` | REST API usage guide |
| `docs/current_system_review.md` | System overview |
| `docs/architecture_decisions.md` | Architecture decision records |
| `docs/roadmap.md` | Development roadmap |
| `docs/manual_download.md` | Manual model download instructions |
| `docs/shapee_setup.md` | Shape-E environment setup |
| `docs/m6_neurosymbolic_street_generation.md` | Neuro-symbolic system design |
| `docs/m4_learning_and_evaluation.md` | Learning & evaluation system |

## License

This project is developed by [GIStudio](https://github.com/GIStudio).
