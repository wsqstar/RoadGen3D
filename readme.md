# RoadGen3D

> **本项目使用 [uv](https://github.com/astral-sh/uv) 管理 Python 依赖和运行环境。**
>
> 所有 Python 命令通过 `uv run python` 或 `uv run pytest` 执行，无需手动创建虚拟环境或安装依赖。

**Text-to-3D Urban Street Scene Generation**

RoadGen3D is a neuro-symbolic system that transforms text descriptions into detailed 3D urban street scenes. A user describes a design goal (e.g., *"步行安全、全龄友好的完整街道"*), the workbench retrieves design knowledge via RAG, generates a parameterized street layout with design-rule constraints, and exports a 3D scene viewable in the built-in Viewer.

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

# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Python dependencies via uv
uv sync

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

### Workflow

**简化的 3 步设计流程：**

1. **模板选择** — 从预设模板中选择场景类型（校园入口、商业街、住宅区、公园道路）
2. **方案生成** — 系统生成多个不同的 3D 街道布局方案
3. **评估与预览** — 查看各方案的评估得分（步行性、安全性、美观性），选择满意方案进行 3D 预览

```
┌─────────────┐     ┌─────────────┐     ┌─────────────────────┐
│  模板选择    │ ──▶ │  方案生成    │ ──▶ │  评估 & 3D 预览      │
│  Template   │     │  Generate   │     │  Evaluate & View    │
└─────────────┘     └─────────────┘     └─────────────────────┘
```

启动服务：

```bash
make dev
```

访问地址：
- **Workbench** — `http://127.0.0.1:4174` (主界面)
- **Viewer** — `http://127.0.0.1:4173` (3D 预览)

### 场景模板

Workbench 提供六种预设模板：
- **步行友好** — 行人优先，安全舒适
- **商业活力** — 商业活跃，人流密集
- **公交优先** — 公交导向，换乘便利
- **公园景观** — 绿化为主，休闲舒适
- **安静居住** — 住宅区安静，绿树成荫
- **平衡街道** — 各类使用者平衡

### 自动化测试 Pipeline

持续测试整个工作流，确保系统稳定运行：

```bash
# 完整 Pipeline：启动 API → 运行测试 → 生成报告
make test-pipeline

# 后台运行测试（持续监控）
nohup make test-pipeline > artifacts/test_reports/pipeline.log 2>&1 &
echo "PID: $!"

# 或者使用 watch 定期执行
watch -n 300 make test-single  # 每 5 分钟执行一次

# 查看汇总报告
make test-report

# 查看日志
tail -f artifacts/test_reports/pipeline.log
```

**报告输出目录**: `artifacts/test_reports/`

```
artifacts/test_reports/
├── test_2026-04-12_15-30-00.md   # 单次测试报告
├── test_2026-04-12_16-00-00.md
├── SUMMARY.md                       # 汇总报告
└── pipeline.log                     # Pipeline 运行日志
```

**汇总报告内容**:
- 总测试数、通过率、失败率
- 平均耗时、平均评分
- 最近 10 次测试详情
- 所有测试报告链接

**单次测试报告内容**:
- 选择的模板和任务 ID
- 场景生成状态和路径
- LLM 评估分数（步行性、安全性、美观性、综合）
- 详细指标和建议

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
│   ├── api/                # FastAPI backend service (port 8010)
│   ├── workbench/          # Vite + React design workbench (port 4174)
│   └── viewer/             # Three.js 3D scene viewer (port 4173, submodule)
├── data/                   # Asset manifests, materials, training data
├── knowledge/              # Complete Streets design guide + RAG index
├── models/                 # Pre-trained CLIP model
├── artifacts/              # Generated outputs (scenes, meshes, eval reports)
├── tests/                  # Test suites
└── tools/
    └── download3dAssets/   # UrbanVerse asset batch downloader (submodule)
```

## System Architecture

### Generation Pipeline

```
User Text Prompt
    │
    ▼
┌──────────┐    ┌────────────────┐    ┌────────────────┐    ┌──────────────┐
│  CLIP +  │───▶│ StreetProgram  │───▶│  LayoutSolver  │───▶│  Mesh Export  │
│  FAISS   │    │ + Constraints  │    │  (collision,   │    │  (GLB / PLY)  │
│ Retrieve │    │                │    │   rules, ...)  │    │               │
└──────────┘    └────────────────┘    └────────────────┘    └──────────────┘
```

### Auto Scene Pipeline (LLM-driven closed loop)

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

### Neuro-Symbolic Street Generation

The default generation pipeline uses explicit intermediate representations:

1. **StreetProgram** — Declarative street description: road type, cross-section, functional zones, street furniture requirements, control points, design goals
2. **ConstraintSet** — Hard/soft design rules (not hardcoded penalties)
3. **LayoutSolver** — Placement optimization with collision detection, outputs `slot_plans / edits / conflicts / rule_evaluations`

Built-in design rule profiles:
- `balanced_complete_street_v1`
- `pedestrian_priority_v1`
- `transit_priority_v1`

### OSM + POI Integration

The system integrates real-world spatial data:

1. **OSM Ingest** — Fetches Overpass data, parses roads/buildings/POI, projects to local metric coordinates
2. **Road Discovery** — Scores candidate roads by POI density, length, and relevance
3. **POI-Driven Cross-Section** — Adjusts sidewalk widths based on nearby POI (transit, entrance, parking, etc.)
4. **Segment Graph** — Discretizes roads into segment/node graph with band/POI context
5. **Placement Context** — Generates road polygons, sidewalk polygons, valid placement zones

Normalized POI types: `entrance`, `bus_stop`, `fire_hydrant`, `crossing`, `traffic_signals`, `parking_entrance`, `subway_entrance`, `post_box`, `waste_basket`, `bollard`

### Text Retrieval

1. Encode query with CLIP `get_text_features`
2. L2 normalize
3. FAISS `IndexFlatIP` inner-product search

### Decoders

| Decoder | Description |
|---------|-------------|
| `placeholder` | Lightweight reproducible decoder; outputs `voxel_prob` + `voxel_bin` |
| `shapee` | Real latent / mesh reference decoding with fallback to placeholder |

### Key Architecture Decisions

- **OSM mode is the primary generation path.** `template` mode is retained for compatibility and debugging.
- **StreetProgram → ConstraintSet → LayoutSolver** is the explicit intermediate backbone. No direct query-to-slot black box.
- **POI is a hard generation input**, not just visualization. Asset-backed POI bind to anchored slots; missing categories cause explicit failure, not silent degradation.
- **Sidewalk widths are POI-driven** in OSM mode, not fixed. Cross-section synthesis adjusts widths based on POI pressure.
- **Learned backends** (program generator, layout policy) are enhancement layers. The system always falls back to heuristic/rule defaults when checkpoints are unavailable.

## CLI Usage

### Generate a Street Scene

```bash
uv run python scripts/m3_01_compose_street.py \
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

### OSM + POI Street Scene

```bash
# Fetch OSM data for an AOI
uv run python scripts/m5_01_fetch_osm.py --bbox 116.39 39.90 116.40 39.91

# Generate with real OSM geometry + POI constraints
uv run python scripts/m3_01_compose_street.py \
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
uv run python scripts/m5_10_eval_compliance.py \
  --scene-dir artifacts/m4/eval_scenes/rule
```

| Flag | Default | Description |
|------|---------|-------------|
| `--layout-mode` | `template` | `template` (straight road) or `osm` (real geometry) |
| `--constraint-mode` | `soft` | `off` or `soft` (POI penalty scoring) |
| `--aoi-bbox` | None | `MIN_LON MIN_LAT MAX_LON MAX_LAT` (required for osm mode) |
| `--poi-rule-set` | `entrance_fire_bus_stop_v1` | Rule set name |

### Auto Scene Pipeline

Automatically generate, evaluate, and iteratively improve a street scene from a Viewer-exported graph JSON or a built-in Graph Template:

```bash
# Using built-in Graph Template (HKUST-GZ Gate)
uv run python scripts/auto_scene_pipeline.py \
  --graph-json assets/graph_templates/hkust_gz_gate/annotation.json \
  --max-iterations 1 \
  --local-files-only \
  --device cpu \
  --query "modern clean urban street" \
  --manifest data/real/real_assets_manifest.jsonl

# Using Viewer-exported graph JSON
uv run python scripts/auto_scene_pipeline.py \
  --graph-json path/to/exported_graph.json \
  --base-map path/to/reference.png \
  --output-dir artifacts/auto_pipeline/my_scene \
  --manifest data/real/real_assets_manifest.jsonl \
  --model-dir models/clip-vit-base-patch32 \
  --max-iterations 5 \
  --query "modern clean urban street" \
  --local-files-only
```

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
├── final/
│   ├── scene_layout.json    # best result
│   ├── scene.glb
│   └── preview.png
└── iteration_log.json
```

Stop conditions: early stop after 2 consecutive rounds without score improvement, or when `--max-iterations` is reached.

### Multi-Version Auto Evaluation

Run multiple design queries through the full pipeline in one shot:

```bash
uv run python scripts/run_auto_eval.py \
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

### Learnable Layout Policy

```bash
# Collect distilled policy data
uv run python scripts/m4_01_collect_policy_data.py \
  --manifest data/real/real_assets_manifest.jsonl \
  --artifacts artifacts/real \
  --out artifacts/m4/policy_train.jsonl \
  --model-dir models/clip-vit-base-patch32 \
  --local-files-only

# Train layout policy (MLP: 32 → 64 → 32 → 1)
uv run python scripts/m4_02_train_layout_policy.py \
  --data artifacts/m4/policy_train.jsonl \
  --out-dir artifacts/m4 \
  --device cpu

# Use learned policy
uv run python scripts/m3_01_compose_street.py \
  --placement-policy learned \
  --policy-ckpt artifacts/m4/layout_policy.pt \
  --policy-temperature 0.12 \
  ...

# Evaluate engineering metrics
uv run python scripts/m4_10_eval_engineering.py \
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

### Scene-Ready Asset Curation

Refresh manifest metadata after adding or replacing assets:

```bash
uv run python scripts/m3_04_clean_asset_manifest.py \
  --manifest data/real/real_assets_manifest.jsonl --write
```

The cleaner computes `mesh_face_count`, assigns `quality_tier`, flags `scene_eligible`, and writes `quality_notes`.

## Web API

The canonical API entry point is `web/api/main.py`. Scene generation runs as async jobs:

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/design/draft` | Generate a design draft with LLM + RAG |
| POST | `/api/design/generate` | Direct scene generation |
| POST | `/api/scene/jobs` | Submit a generation job |
| GET | `/api/scene/jobs` | List all jobs |
| GET | `/api/scene/jobs/{job_id}` | Get job status / result |
| GET | `/api/scenes/recent` | List recent scenes |
| GET | `/api/knowledge/sources` | List available knowledge sources |
| POST | `/api/knowledge/search` | Manual knowledge search |
| GET | `/api/graph-templates` | Street graph templates |
| GET | `/api/reference-plans` | MetaUrban reference plans |

Swagger UI: `http://127.0.0.1:8010/docs`

## 评估可视化

Workbench 提供交互式评估可视化功能：

### 评估维度

| 维度 | 权重 | 说明 |
|------|------|------|
| 步行性 (Walkability) | 45% | 人行道宽度、无障碍设施、步行舒适度 |
| 安全性 (Safety) | 35% | 交通隔离、照明、安全设施覆盖 |
| 美观性 (Beauty) | 20% | 植物配置、街道家具协调性 |

### 可视化组件

- **雷达图** — 多维度综合评分可视化
- **柱状图** — 各维度得分对比
- **综合评分** — 加权总分 (0-100)

### 3D 预览

点击任意方案卡片上的 **"3D 预览"** 按钮，在 Viewer 中查看该方案的 3D 渲染效果。Viewer 支持：
- 轨道控制 (旋转、缩放、平移)
- 场景布局路径透传
- 最近场景历史记录

## Testing

The test suite in `tests/test_auto_eval.py` validates the full pipeline end-to-end. Tests 1–4 call the real LLM API (auto-skipped if `llm_base_url` and `key` are not set in `.env`), while test 5 uses a mock service for deterministic early-stop verification.

```bash
# Run all tests (real-LLM tests auto-skip without API credentials)
uv run pytest tests/test_auto_eval.py -v

# Force-skip real-LLM tests (only mock + presentation tests)
GLM_SKIP=1 uv run pytest tests/test_auto_eval.py -v
```

| Test | LLM | What it verifies |
|------|-----|-----------------|
| `TestAutoEvalGeneratesMultipleVersions` | Real | Multiple queries produce distinct iteration dirs, final/, and different config patches |
| `TestAutoEvalSavesIterationLogs` | Real | `iteration_log.json` has correct structure (score, evaluation, suggestions, config_patch) |
| `TestAutoEvalRendersPresentationViews` | None | `render_presentation_views()` outputs valid view dicts |
| `TestAutoEvalProducesEvalReport` | Real | `eval_report.json` aggregates all versions with plausible scores in [0, 10] |
| `TestAutoEvalLLMIterationsImproveOrStop` | Mock | Controller stops after ≤3 iterations when scores stagnate |

## Environment Variables

Create a `.env` file in the project root:

```bash
GRAPHRAG_API_KEY=your_graphrag_key
GRAPHRAG_API_BASE=https://api.zetatechs.com/v1/
LLM_MODEL=gpt-4o-mini
```

### Test LLM API

```bash
# Test API connectivity
./scripts/test_llm_api.sh

# Test with specific model
./scripts/test_llm_api.sh gpt-4

# List available models
./scripts/test_llm_api.sh --list
```

## Make Targets

```bash
make help                 # Show all available targets
make dev                  # Start API + workbench + viewer
make workbench-api        # Start FastAPI backend (port 8010)
make workbench-web        # Start Vite workbench (port 4174)
make viewer-web           # Start 3D viewer (port 4173)
make knowledge-build      # Build RAG knowledge base from design guide PDF
make collect              # Collect policy training data
make train                # Train layout policy
make eval                 # Run engineering evaluation
```

## Roadmap

### Completed ✓

- **简化 Workbench UI** — 从 5-tab 架构简化为 3-step 流程 (模板选择 → 方案生成 → 评估预览)
- **评估可视化** — 雷达图、柱状图、综合评分展示
- **方案对比** — 多方案并行生成与评分排序
- **Viewer URL 透传** — 正确传递场景布局路径到 3D 预览器

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

## Current Limitations

- No cross-modal training (OpenShape/ULIP) — retrieval is CLIP text-only
- `shapee` direct latent decoding requires matching latent dimensions; production use recommends `mesh_ref`
- Single-segment straight road template — no complex intersections or curved networks
- `StreetProgram` uses heuristic generator (`heuristic_v1`) — not yet replaced by a learned program generator
- Layout solver uses `banded` heuristic — not MILP or diffusion-based

## GraphRAG Knowledge Base

The knowledge base uses **GraphRAG** (Graph-based Retrieval Augmented Generation) to provide structured design knowledge from the Complete Streets Design Handbook.

### Data Source

| Source | Records | Description |
|--------|---------|-------------|
| Txt corpus | 75 | Merged text chunks from the handbook |
| Community reports | 60 | LLM-generated community summaries at multiple levels |
| Text units | 108 | Source text segments linked to entities |
| Entities | 431 | Design-relevant entities (organizations, locations, events) |
| Relationships | 570 | Entity relationships with weights |

### Quality Characteristics

- **Community reports**: Avg 487 chars summary, 3657 chars full content
- **Entity descriptions**: Avg 399 chars with detailed context
- **Hierarchical structure**: Level 0-3 communities for multi-granularity search

### Knowledge Sources in Design Flow

```python
# Hybrid mode (PDF RAG + GraphRAG combined)
service.search_knowledge(query="sidewalk width pedestrian safety", knowledge_source="hybrid")

# GraphRAG only (uses graph structure for context-aware retrieval)
service.search_knowledge(query="bicycle infrastructure design", knowledge_source="graph_rag")
```

### Updating the Knowledge Base

When new knowledge is added to `knowledge/graphRAG/graphrag_txt/`:

```bash
# Trigger rebuild via API
curl -X POST http://127.0.0.1:8010/api/knowledge/rebuild
```

Or rebuild programmatically:

```python
from roadgen3d.knowledge.graphrag import GraphRagKnowledgeRetriever
retriever = GraphRagKnowledgeRetriever(project_dir="knowledge/graphRAG")
retriever.ensure_runtime_artifacts(force=True)
```

## License

This project is developed by [GIStudio](https://github.com/GIStudio).
