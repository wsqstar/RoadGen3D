# RoadGen3D

> **3D Urban Street Scene Generation and Evaluation** — 规则/约束驱动、AI 辅助的 3D 街道场景生成与评价系统

[![Docs](https://img.shields.io/badge/docs-index-blue)](docs/README.md)
[![Framework](https://img.shields.io/badge/docs-framework-blue)](docs/ROADGEN3D_FRAMEWORK.md)
[![Evaluation Engine](https://img.shields.io/badge/eval-road--metrics-green)](src/roadgen3d/eval_engine_ext)
[![License](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

> 文档入口见 [docs/README.md](docs/README.md)，当前进度总控页见 [docs/current-progress.md](docs/current-progress.md)。下午组会可直接使用 [docs/PROJECT_SUMMARY_FOR_MEETING.md](docs/PROJECT_SUMMARY_FOR_MEETING.md)。

---

## 🏗️ 仓库架构

RoadGen3D 是一个**分层架构**的生成式设计系统，由 **4 层**和 **3 个子模块**组成：

```
RoadGen3D/
├── 🌐 Web 交互层 (web/)
│   ├── viewer/             # 当前主界面：Scenario Designs、设计生成、溯源、评价与 Three.js 3D 查看
│   ├── workbench/          # 已归档的旧 React 设计工作台 (默认不启动)
│   └── api/                # FastAPI 后端 (意图理解、场景生成、评估接口)
│
├── 🧠 核心引擎层 (src/roadgen3d/)
│   ├── auto_pipeline/      # 自动生成流水线 (graph_loader, iteration_controller)
│   ├── llm/                # LLM 设计助手 (prompts, design_workflow)
│   ├── knowledge/          # RAG 知识库 (PDF/GraphRAG 检索)
│   ├── services/           # 运行时服务 (design_runtime, branch_runs, branch_benchmarks)
│   └── eval_engine_ext/    # 📦 [Submodule] 独立评估引擎 → road-metrics
│
├── 📊 评估引擎 (road-metrics) ← 独立 Git Submodule
│   ├── extractors/         # Layer 1: 数据提取 (crossing, trees, furniture)
│   ├── base_metrics/       # Layer 2: 基础指标 (adequacy, uniformity, density)
│   ├── composers/          # Layer 3: 评分组合 (walkability, safety, beauty)
│   └── evaluators/         # LLM 增强评估 (safety_eval, beauty_eval)
│
├── 📦 资产与数据 (data/, assets/)
│   ├── real/latents/       # CLIP 特征向量缓存 (154 个资产，600KB)
│   ├── real/meshes/        # 3D 资产 GLB 文件 (本地存储，不提交到 Git)
│   └── knowledge/          # GraphRAG 知识库索引
│
├── 🧪 生成实验与 Benchmark (artifacts/)
│   ├── branch_runs/        # 分支搜索 manifest、trace、layout、保留的 GLB
│   └── branch_benchmarks/  # 持久化 benchmark samples 与汇总索引
│
├── 🗂️ 运营与工具层 (ops/)
│   ├── scripts/              # 运维/研究脚本（实际路径：ops/scripts）
│   ├── configs/              # 运行/实验配置（实际路径：ops/configs）
│   └── examples/             # 示例场景与演示（实际路径：ops/examples）
└── 📖 文档 (docs/)
    ├── ROADGEN3D_FRAMEWORK.md         # 当前框架总览和主流程
    ├── DATA_CONTRACTS.md              # 数据契约
    ├── EVALUATION.md                  # 当前评价契约
    ├── DEPLOYMENT_AND_JOBS.md         # 部署与任务边界
    ├── ACTIVE_ENTRYPOINTS.md          # 活跃入口与兼容边界
    ├── current-progress.md            # 进度与文档主控索引
    ├── PROJECT_LAYOUT.md              # 目录分层导航（新增）
    └── PROJECT_SUMMARY_FOR_MEETING.md  # 组会一页总结
```

### 🔑 核心设计理念

| 层级 | 职责 | 关键技术 |
|:---|:---|:---|
| **Web 层** | 用户交互、3D 可视化 | TypeScript DOM, Three.js, Vite, G6/Chart.js |
| **API 层** | 业务逻辑编排、任务队列 | FastAPI, Pydantic |
| **引擎层** | 场景生成、约束求解、布局优化 | Python, NumPy, CLIP, PuLP |
| **评估层** | 多维度质量评估 (独立子模块) | road-metrics (Submodule) |

### 🎯 核心算法特性

#### 1. 资产缩放与布局优化
- **强制同比例缩放**: 所有资产保持原始长宽比，禁止拉伸变形
- **动态间距算法**: 根据资产实际尺寸智能计算间距
  - **均匀分布求解器**: 树/路灯等间距排列 (Uniform Spacing)
  - **紧凑装箱求解器**: 长椅/垃圾桶等紧凑聚集 (Compact Packing)

#### 2. 多矩形碰撞检测 (Multi-Box Decomposition)
- **资产预处理**: 将复杂 3D 网格分解为多个紧密贴合的边界框
- **两阶段检测**:
  - 粗检测: 外包 AABB 快速排除不相关资产
  - 精检测: 子框对精确碰撞检测
- **内存安全**: LRU 缓存管理，自动淘汰，防 OOM 设计
- **效果**: L 形长椅、带顶棚公交站等可以"咬合"排列，空间利用率大幅提升

### 🔄 核心工作流

```
OSM / Reference Annotation / Scenario catalog / optional LLM prompt
  → A: Skeleton Design / 骨架功能设计
  → B: Street Furniture Profile / 街道家具主题
  → 场景生成 (road skeleton + layout + buildings + furniture + assets)
  → 质量评估 (road-metrics)
  → Viewer 展示、对比、报告和后续优化
```

当前 Viewer 的 Scenario Designs 批量生成从场景目录出发，通过 `/api/scenario-designs/runs` 转成 `template_patch` 和 `compose_config_patch`，并以 `skip_llm` 模式复用 scene job 生成内核。自然语言 LLM/RAG draft、Branch/Pareto 和 benchmark 路径仍然存在，但不是 Scenario Designs 面板的主生成线路。

#### A/B Semantic Design Layers

RoadGen3D now separates street semantics into two explicit layers:

- **A: Skeleton Design / 骨架功能设计** decides road skeleton, cross-section, surface annotation, functional zones, bus / walking / vehicle priority, and similar spatial-function choices. It can come from OSM/POI inference, Viewer Reference Plan Annotation, or LLM annotation.
- **B: Street Furniture Profile / 街道家具主题** decides furniture density, asset mix, building/furniture generation preferences, material and rendering style. It can come from the Viewer street furniture design goal, LLM inference, or a fallback recommendation from A.
- Resolution priority is fixed as **manual annotation > LLM > OSM/POI automatic inference**. `scene_layout.json.summary.semantic_design_layers` records the final A/B profiles, source, confidence, reasons, resolution order, and the `profile_pair`.
- Evaluation still uses the same road-metrics dimensions (`Walkability`, `Safety`, `PlaceQuality`), while the scenario rubric can override Pass / Review / Fail thresholds by `skeleton_design_profile + street_furniture_profile`.

### 🧭 Pareto Trace 与 Benchmark Explorer

Viewer 的 Design 面板现在包含一套面向生成实验的分析工作流：

- **Branch Run**: 从一个 prompt / preset 出发，生成多个候选节点并记录每个节点的 RAG evidence、参数三元组、config patch、优化指令、rejected edits、评分和父子 delta。
- **100 Sample Trace**: 通过 `target_samples=100` 持续扩展 frontier，直到达到目标样本数、触发安全上限、触发早停，或没有可扩展节点。
- **Pareto Search**: 将 `walkability / safety / beauty` 作为三目标搜索空间，使用传统参数采样与 Pareto frontier 选择候选；LLM 不再承担每个节点的参数推导，LLM 主要保留在视觉/截图评价等尚未替代的环节。
- **Artifact Retention**: 批量生成时默认只保留评分前若干个 GLB，例如 top 10；新候选会临时渲染、评分，然后如果没有进入保留集合就删除 GLB。`scene_layout.json` 会保留，用于后续分析或按需重建 GLB。
- **Persistent Benchmark Explorer**: 读取 `artifacts/branch_benchmarks/samples.jsonl`，支持按 preset / batch / run 过滤历史样本，比较不同 preset 的评分集群。
- **Correlation Analysis**: 连接 `输入参数 / preset / patch` → `scene_layout.json` 落地参数 → `walkability / safety / beauty / overall`，提供相关热力图、参数散点、类别效应和 feature importance。

> 当前的“导致”定义为可追溯解释和相关性分析：展示某个结果使用了哪些知识、参数、patch、约束，以及相对父节点的评分变化；不声明严格统计因果。

### 📦 独立子模块

本项目使用 Git Submodule 管理独立组件：

| 子模块 | 路径 | 仓库 | 说明 |
|:---|:---|:---|:---|
| **road-metrics** | `src/roadgen3d/eval_engine_ext` | [wsqstar/road-metrics](https://github.com/wsqstar/road-metrics) | 分层评估引擎 (可独立安装) |
| **viewer** | `web/viewer` | [GIStudio/Viewer](https://github.com/GIStudio/Viewer) | 3D 场景渲染器 |

```bash
# 克隆时初始化子模块
git clone --recurse-submodules https://github.com/GIStudio/RoadGen3D.git
```

---

## 🚀 快速开始

### 环境准备

```bash
# 1. 克隆仓库 (包含子模块)
git clone --recurse-submodules https://github.com/GIStudio/RoadGen3D.git
cd RoadGen3D

# 2. 安装 Python 依赖 (使用 uv)
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync

# 3. 安装前端依赖
make viewer-install

# 4. 下载 CLIP 模型 (离线)
huggingface-cli download openai/clip-vit-base-patch32 \
  --local-dir models/clip-vit-base-patch32
```

> **本项目使用 [uv](https://github.com/astral-sh/uv) 管理 Python 依赖和运行环境。**
>
> 所有 Python 命令通过 `uv run python` 或 `uv run pytest` 执行，无需手动创建虚拟环境或安装依赖。

### 启动服务

**启动完整开发环境** (API + Viewer):

```bash
make dev
```

这将启动两个服务：
- **API** — `http://127.0.0.1:8010` (FastAPI 后端)
- **Viewer** — 默认 `http://127.0.0.1:4173`；如果端口被其他服务占用，会自动选择后续空闲端口。

或单独启动：`make api`, `make viewer-web`。

`make workbench-api` 仅作为历史兼容 alias 保留，实际启动同一个 FastAPI 入口。

旧版 `web/workbench` 已归档，默认不再启动。需要查看历史 UI 时显式执行：

```bash
ENABLE_ARCHIVED_WORKBENCH=1 make workbench-web
```

兼容性说明：

- 新目录已归位到 `ops/` 与 `legacy/`，根目录保留兼容入口：
  - `scripts` -> `ops/scripts`
  - `configs` -> `ops/configs`
  - `examples` -> `ops/examples`
  - `evaluation` -> `legacy/evaluation`
  - `ui` -> `legacy/ui_api_legacy`
  - `.archive` -> `legacy/_archive`
  - `web/workbench` -> `legacy/web_workbench`

### 测试 Pipeline

```bash
# 默认：随机选择模板，禁用 LLM
make test-pipeline

# 指定模板
make test-pipeline GRAPH_TEMPLATE=hkust_gz_gate_all

# 启用 LLM 动态生成
make test-pipeline USE_LLM=1

# 批量测试：并行生成 6 个模板
make test-batch

# 查看测试报告
make test-report
```

## Quick Start

### Prerequisites

- Python 3.11+ (tested on macOS arm64)
- Git (with submodule support)
- Node.js (for web viewer)

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
make viewer-install

# Download CLIP model (offline)
huggingface-cli download openai/clip-vit-base-patch32 \
  --local-dir models/clip-vit-base-patch32
```

### Run

**Start the full development environment** (API + Viewer):

```bash
make dev
```

This launches two services:
- **API** — `http://127.0.0.1:8010`
- **Viewer** — defaults to `http://127.0.0.1:4173`; if that port belongs to another service, Make picks the next free port.

Or start individual services via `make api` and `make viewer-web`.

The legacy `web/workbench` app is archived and hidden from default startup. To inspect it explicitly:

```bash
ENABLE_ARCHIVED_WORKBENCH=1 make workbench-web
```

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

> 📖 **想了解完整的系统架构和工作流？** 请查看 [当前框架总览](docs/ROADGEN3D_FRAMEWORK.md) 和 [文档入口](docs/README.md)，其中说明了 Viewer、Test Pipeline、归档 Workbench 之间的关系，以及"生成-评估-优化"闭环的当前实现。

启动服务：

```bash
make dev
```

访问地址：
- **Viewer** — 查看 `make dev` 输出的 Viewer URL，默认是 `http://127.0.0.1:4173`

### 场景模板

Viewer 的 Design 面板提供六种预设模板：
- **步行友好** — 行人优先，安全舒适
- **商业活力** — 商业活跃，人流密集
- **公交优先** — 公交导向，换乘便利
- **公园景观** — 绿化为主，休闲舒适
- **安静居住** — 住宅区安静，绿树成荫
- **平衡街道** — 各类使用者平衡

### 100/600 组 Benchmark 与相关性分析

在 Viewer 的 Design 面板中打开 **Persistent Benchmark Explorer**：

1. 选择 preset 或自定义 prompt，启动 **100 Sample Trace**。
2. 对单个 preset，后端会创建一个 branch run，最多保存 100 个已评分样本。
3. 对全部六个 preset，后端会创建 batch run，目标规模是 `6 * 100`；如果多轮没有改进，会按 `early_stop_patience` 早停。
4. 每个样本都会持久化为 benchmark sample，之后无需 GLB 也可以参与统计分析。
5. 切到 **Correlation Analysis** tab，查看参数-评分热力图、参数散点、feature importance、preset 类别效应，以及 3D Pareto scatter 的参数着色。

主要输出位置：

| 路径 | 内容 |
|------|------|
| `artifacts/branch_runs/<run_id>/manifest.json` | 单次 branch run 的节点、评分、trace、scatter points、artifact 保留信息 |
| `artifacts/branch_runs/<run_id>/<node_id>/.../scene_layout.json` | 可恢复的场景布局清单 |
| `artifacts/branch_runs/<run_id>/<node_id>/.../scene.glb` | 仅对 top-k 保留节点长期保存 |
| `artifacts/branch_benchmarks/samples.jsonl` | 跨 run / batch 持久化样本表 |
| `artifacts/branch_benchmarks/summary.json` | 按 preset 聚合的 benchmark 摘要 |

API 示例：

```bash
# 单 preset 的 100 组 Pareto Trace
curl -X POST http://127.0.0.1:8010/api/design/branch-runs \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "行人优先、树荫充足、商业活跃的完整街道",
    "preset_id": "pedestrian_friendly",
    "search_mode": "pareto",
    "target_samples": 100,
    "persist_to_benchmark": true,
    "retain_topk_artifacts": 10,
    "score_with_rendered_views": true
  }'

# 六个 preset 各跑最多 100 组
curl -X POST http://127.0.0.1:8010/api/design/benchmark-batches \
  -H "Content-Type: application/json" \
  -d '{
    "target_samples": 100,
    "early_stop_patience": 20,
    "retain_topk_artifacts": 10,
    "score_with_rendered_views": true
  }'

# 读取持久化样本
curl "http://127.0.0.1:8010/api/design/benchmark-samples?limit=10000"

# 读取相关性分析 payload
curl "http://127.0.0.1:8010/api/design/benchmark-analysis?limit=10000"
```

GLB 恢复策略：

- 如果 `scene.glb` 仍存在，Viewer 会直接加载对应 GLB。
- 如果只保留了 `scene_layout.json`，Viewer 可调用 `/api/design/rebuild-layout-glb` 重新组装 GLB。
- 如果布局清单也缺失，则只能重新生成新结果；这不等价于无损恢复当时的样本。

### 自动化测试 Pipeline

持续测试整个工作流，确保系统稳定运行：

```bash
# 完整 Pipeline：启动 API → 运行测试 → 生成报告
make test-pipeline

# 后台运行测试（持续监控）
nohup make test-pipeline > artifacts/test_reports/pipeline.log 2>&1 &
echo "PID: $!"

# 或者使用 watch 定期执行
watch -n 300 make test-preset  # 每 5 分钟执行一次

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
│   │   └── cli.py                  # (entry point via ops/scripts/)
│   ├── llm/                # LLM design assistant (optional)
│   │   ├── glm_client.py
│   │   ├── prompts.py
│   │   └── design_workflow.py
│   ├── services/           # API & runtime services
│   │   ├── generation_core.py      # Scene generation logic
│   │   ├── generation_api.py       # FastAPI routes
│   │   ├── design_runtime.py       # LLM design runtime
│   │   ├── design_types.py         # Data types
│   │   ├── scene_jobs.py           # Async job queue
│   │   ├── branch_runs.py          # Branch / Pareto generation, trace payloads, artifact retention
│   │   └── branch_benchmarks.py    # Persistent benchmark store, feature extraction, correlations
│   └── ...
├── ops/
│   ├── scripts/               # CLI tools (rag_*, asset_*, street_*, layout_*, osm_*, program_*)
│   ├── configs/               # 配置（入口/实验配置、demo 配置）
│   └── examples/              # 示例脚本与演示入口
├── web/
│   ├── api/                # FastAPI backend service (port 8010)
│   ├── viewer/             # Active design + Three.js scene viewer (port 4173, submodule)
│   └── workbench/          # Archived legacy React workbench (opt-in only)
├── data/                   # Asset manifests, materials, training data
├── knowledge/              # Complete Streets design guide + RAG index
├── models/                 # Pre-trained CLIP model
├── artifacts/              # Generated outputs (scenes, meshes, eval reports, branch benchmark samples)
├── tests/                  # Test suites
└── tools/
    └── download3dAssets/   # UrbanVerse asset batch downloader (submodule)
```

## System Architecture

### Current Viewer Generation Pipeline

```
Scenario Designs catalog
    │
    ▼
┌────────────────┐    ┌────────────────┐    ┌────────────────┐    ┌──────────────┐
│ template_patch │───▶│ graph-template │───▶│ compose_street │───▶│ scene_layout │
│ config_patch   │    │ scene bridge   │    │ _scene()       │    │ + scene.glb  │
└────────────────┘    └────────────────┘    └────────────────┘    └──────────────┘
```

This is the current Viewer Scenario Designs path. It submits `/api/scenario-designs/runs`, constructs a `DesignDraft` from catalog data, sets `preset_id=skip_llm`, and then reuses the `/api/scene/jobs` generation core.

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

### Branch / Pareto Benchmark Pipeline

```
preset / prompt / patch
        │
        ▼
┌──────────────────┐
│ BranchRunService │──▶ candidate nodes + parent_id + influence_rows
└────────┬─────────┘
         ▼
┌──────────────────┐
│ compose scene    │──▶ scene_layout.json + temporary / retained scene.glb
└────────┬─────────┘
         ▼
┌──────────────────┐
│ score scene      │──▶ walkability / safety / beauty / overall
└────────┬─────────┘
         ▼
┌──────────────────┐
│ Pareto frontier  │──▶ top-k artifacts retained, weak branches early-stopped
└────────┬─────────┘
         ▼
┌──────────────────┐
│ BenchmarkStore   │──▶ samples.jsonl + correlation analysis payload
└──────────────────┘
```

For v1 benchmark search, the generation parameter space is solved with deterministic / traditional search over preset patches. RAG evidence, scene parameter triples, LLM config patches, optimization directives, and rejected edits are preserved as trace rows so the Viewer can explain why a point appears where it does in the 3D score space. Visual scoring from rendered views may still call the LLM evaluator when enabled.

### Structured Rule / Constraint Generation

The core generator uses explicit intermediate representations:

1. **StreetProgram** — Declarative street description: road type, cross-section, functional zones, street furniture requirements, control points, design goals
2. **ConstraintSet** — Hard/soft design rules (not hardcoded penalties)
3. **LayoutSolver** — Placement optimization with collision detection, outputs `slot_plans / edits / conflicts / rule_evaluations`

These structures support explainability and rule/constraint-driven generation. The repository also contains optional LLM/RAG and learned-generator hooks, but the current Viewer Scenario Designs batch path is not a strict neuro-symbolic model.

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

#### OSM Multiblock Semantic Mode

OSM has two generation modes with different design intent:

- `layout_mode=osm` keeps the original single-road auto-discovery flow. It selects one POI-rich road from the AOI and generates a focused street scene.
- `layout_mode=osm_multiblock` keeps the AOI as a connected multi-road context. It uses OSM road geometry, POIs, buildings, and landuse/amenity polygons to assign street-level semantic profiles before generation.

`osm_multiblock` adds the A-layer semantic skeleton profile on top of the physical segment graph:

- **Semantic blocks** come first from real OSM landuse/amenity/building polygons, with fallback to road-buffer/grid blocks when OSM polygons are sparse.
- **Street segments** carry `semantic_profile_id`, `skeleton_design_profile`, reasons, and confidence. `semantic_profile_id` remains for compatibility; `skeleton_design_profile` is the explicit A-layer field.
- **Street furniture theme stays separate.** For example, `child_friendly_school` can change road-section and safety choices, while B-layer `street_furniture_profile=pedestrian_friendly` controls furniture density, asset mix, and style.
- **Main roads and solver segments are different units.** Main roads remain the user-facing OSM ways; solver segments are internal resampled slices used for placement and annotation.
- **Short roads can be rendered with default style.** In the HKUST(GZ) demo, roads shorter than 20 m are kept in the graph but do not contribute to semantic profile counts or trigger extra facilities.

Current semantic profiles include `child_friendly_school`, `walkable_commercial`, `vehicle_access_commercial`, `transit_priority`, `green_walkable`, and `quiet_residential`.

After semantic classification, OSM multiblock generation also runs `socioeconomic_fit_v1`:

1. infer the surrounding socioeconomic proxy from OSM landuse/amenity/POI context;
2. compare current road supply, including sidewalk width, lane count, highway class, and nearby OSM facilities;
3. mark under-provisioned segments and roads;
4. when `osm_context_fit_mode=auto_design`, apply the dominant recommended design patch before 3D generation.

Example design directions:

- school/campus context with weak crossing or safety facilities -> `child_safety_upgrade`
- commercial context with active POI and weak pedestrian supply -> `commercial_walkability_upgrade`
- commercial context with sparse local POI / parking-access need -> `vehicle_access_upgrade`
- transit context with weak stop/crossing support -> `transit_access_upgrade`

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

- **Scenario Designs + graph template is the current Viewer demo path.** OSM, MetaUrban, prompt/preset and `template` modes remain available for other workflows and experiments.
- **StreetProgram → ConstraintSet → LayoutSolver** is the explicit intermediate backbone inside the core generator. Current Viewer Scenario Designs generation starts from catalog/template patches, not from per-sample LLM parameter derivation.
- **POI is a hard generation input**, not just visualization. Asset-backed POI bind to anchored slots; missing categories cause explicit failure, not silent degradation.
- **Sidewalk widths are POI-driven** in OSM mode, not fixed. Cross-section synthesis adjusts widths based on POI pressure.
- **Learned backends** (program generator, layout policy) are enhancement layers. The system always falls back to heuristic/rule defaults when checkpoints are unavailable.

## CLI Usage

### Generate a Street Scene

```bash
uv run python ops/scripts/street_compose.py \
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
uv run python ops/scripts/osm_fetch.py --bbox 116.39 39.90 116.40 39.91

# Generate with real OSM geometry + POI constraints
uv run python ops/scripts/street_compose.py \
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
uv run python ops/scripts/osm_eval_compliance.py \
  --scene-dir artifacts/m4/eval_scenes/rule
```

| Flag | Default | Description |
|------|---------|-------------|
| `--layout-mode` | `template` | `template` (straight road) or `osm` (real geometry) |
| `--constraint-mode` | `soft` | `off` or `soft` (POI penalty scoring) |
| `--aoi-bbox` | None | `MIN_LON MIN_LAT MAX_LON MAX_LAT` (required for osm mode) |
| `--poi-rule-set` | `entrance_fire_bus_stop_v1` | Rule set name |

#### HKUST(GZ) 350 m OSM Semantic Demo

The checked-in demo configuration is:

- Config: `ops/configs/osm_demos/hkust_gz_350m.json`
- Semantic preview artifact: `assets/osm_demos/hkust_gz_350m_semantic_preview.json`
- Raw Overpass cache: `artifacts/m5/osm_cache/` (ignored by Git)
- Demo mode: `layout_mode=osm_multiblock`
- AOI bbox: `[113.474180, 22.887261, 113.477592, 22.890421]`
- AOI size: about `350.1 m x 350.0 m`, about `122,500 m²` / `12.25 ha` / `0.123 km²`

Current preview interpretation:

- OSM roads in AOI: `12`
- Selected main roads: `5`
- Selected main-road length: about `545 m`
- Internal solver segments: `18`, generated by whole-road polyline resampling with `segment_length_m=35`
- Short default-style roads: `1` road, about `11 m`, retained for rendering but excluded from semantic profile counts
- Segment semantic profile counts: `child_friendly_school=12`, `quiet_residential=4`, `transit_priority=1`
- Context-fit automation: `osm_context_fit_mode=auto_design`; current preview recommends `child_safety_upgrade` for under-provisioned campus segments

HKUST(GZ) semantic constraints:

- Campus service roads and education landuse are treated as `child_friendly_school`.
- Only the off-campus road `笃学路` is eligible for bus-stop or transit overlay.
- Current OSM data has `0` real bus stops/platforms in this AOI. The demo may add at most `1` `demo_inferred` bus stop on `笃学路`, and the preview records provenance separately as `osm`, `demo_inferred`, and `total`.

Regenerate the lightweight semantic preview:

```bash
UV_CACHE_DIR=.uv-cache \
MPLCONFIGDIR=/private/tmp/roadgen3d-mpl-cache \
XDG_CACHE_HOME=/private/tmp/roadgen3d-xdg-cache \
uv run python ops/scripts/osm_semantic_preview.py \
  --config ops/configs/osm_demos/hkust_gz_350m.json
```

### Auto Scene Pipeline

Automatically generate, evaluate, and iteratively improve a street scene from a Viewer-exported graph JSON or a built-in Graph Template:

```bash
# Using built-in Graph Template (HKUST-GZ Gate)
uv run python ops/scripts/auto_scene_pipeline.py \
  --graph-json assets/graph_templates/hkust_gz_gate/annotation.json \
  --max-iterations 1 \
  --local-files-only \
  --device cpu \
  --query "modern clean urban street" \
  --manifest data/real/real_assets_manifest.jsonl
```

> **Graph Template 特征速查**
> | Template | 中央绿化 | 说明 |
> |----------|----------|------|
> | `hkust_gz_gate` | 无 | 标准校门四车道，无中央分隔带 |
> | `hkust_gz_detailed` | median（中分带） | 详细剖面，带中央绿化隔离带 |
> | `hkust_gz_gate_all` | grass_belt + median | 最完整剖面，兼具中央绿化带与中分带 |

```bash
# Using Viewer-exported graph JSON
uv run python ops/scripts/auto_scene_pipeline.py \
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
uv run python ops/scripts/run_auto_eval.py \
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
uv run python ops/scripts/layout_collect_data.py \
  --manifest data/real/real_assets_manifest.jsonl \
  --artifacts artifacts/real \
  --out artifacts/m4/policy_train.jsonl \
  --model-dir models/clip-vit-base-patch32 \
  --local-files-only

# Train layout policy (MLP: 32 → 64 → 32 → 1)
uv run python ops/scripts/layout_train.py \
  --data artifacts/m4/policy_train.jsonl \
  --out-dir artifacts/m4 \
  --device cpu

# Use learned policy
uv run python ops/scripts/street_compose.py \
  --placement-policy learned \
  --policy-ckpt artifacts/m4/layout_policy.pt \
  --policy-temperature 0.12 \
  ...

# Evaluate engineering metrics
uv run python ops/scripts/layout_eval.py \
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
uv run python ops/scripts/asset_clean_manifest.py \
  --manifest data/real/real_assets_manifest.jsonl --write
```

The cleaner computes `mesh_face_count`, assigns `quality_tier`, flags `scene_eligible`, and writes `quality_notes`.

## Web API

The canonical API entry point is `web/api/main.py`. Scene generation runs as async jobs:

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/design/draft` | Generate a design draft with LLM + RAG |
| POST | `/api/design/generate` | Direct scene generation |
| POST | `/api/design/branch-runs` | Start a branch / Pareto generation run; supports `target_samples`, `search_mode`, artifact retention, benchmark persistence |
| GET | `/api/design/branch-runs` | List recent branch runs |
| GET | `/api/design/branch-runs/{run_id}` | Read a branch run manifest payload, including nodes, scatter points, influence rows, and artifact status |
| GET | `/api/design/benchmark-samples` | Query persistent benchmark samples by `preset_id`, `batch_id`, or `run_id` |
| GET | `/api/design/benchmark-analysis` | Query extracted features, Spearman correlations, Kruskal effects, and feature importance |
| POST | `/api/design/benchmark-batches` | Run benchmark generation across multiple presets, defaulting to all six presets |
| GET | `/api/design/benchmark-batches/{batch_id}` | Read batch status and per-preset run progress |
| POST | `/api/design/rebuild-layout-glb` | Rebuild a missing `scene.glb` from a retained `scene_layout.json` |
| POST | `/api/design/evaluate/unified` | Unified scene evaluation endpoint |
| POST | `/api/scene/jobs` | Submit a generation job |
| GET | `/api/scene/jobs` | List all jobs |
| GET | `/api/scene/jobs/{job_id}` | Get job status / result |
| GET | `/api/scenario-designs` | List curated Viewer scenario designs |
| POST | `/api/scenario-designs/runs` | Submit Scenario Designs batch generation; catalog entries are converted to template/config patches and generated with `skip_llm` |
| GET | `/api/scenario-designs/runs/{run_id}` | Poll Scenario Designs batch status |
| GET | `/api/scenario-designs/runs/{run_id}/report` | Read the generated Scenario Designs report |
| POST | `/api/osm/semantic-preview` | Preview OSM multiblock semantic blocks, road/segment profile counts, short-road policy, and bus-stop provenance without running 3D generation |
| GET | `/api/scenes/recent` | List recent scenes |
| GET | `/api/knowledge/sources` | List available knowledge sources |
| POST | `/api/knowledge/search` | Manual knowledge search |
| GET | `/api/graph-templates` | Street graph templates |
| GET | `/api/reference-plans` | MetaUrban reference plans |

Swagger UI: `http://127.0.0.1:8010/docs`

## 评估可视化

Viewer 提供交互式评估可视化功能：

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
- **3D Pareto Scatter** — X/Y/Z 对应 `walkability / safety / beauty`，每个点代表一个已评分场景；支持 hover、click 选中、按 preset 或参数着色
- **溯源矩阵** — 按 `Knowledge / RAG`、`Parameter Triples`、`LLM Changes & Constraints` 展示当前点的激活知识、参数和约束
- **Correlation Analysis** — 热力图、参数散点、feature importance、Kruskal-Wallis 类别效应，用于分析输入参数、落地场景参数和评分之间的关系

### 3D 预览

点击任意方案卡片上的 **"3D 预览"** 按钮，在 Viewer 中查看该方案的 3D 渲染效果。Viewer 支持：
- 轨道控制 (旋转、缩放、平移)
- 场景布局路径透传
- 最近场景历史记录
- 缺失 GLB 时从 `scene_layout.json` 按需重建

### Benchmark Explorer

Benchmark Explorer 是当前分析历史生成结果的主入口：

- **Overview**: 查看所有持久化样本、preset 聚类、Pareto frontier、artifact 是否可恢复。
- **Correlation Analysis**: 在同一批 filtered samples 上计算并展示相关性和特征重要性。
- **Preset Filter**: 对不同 preset 的样本集群进行过滤和对比。
- **Color by Parameter**: 在 3D Pareto scatter 上用 `tree_count`、`sidewalk_width_m`、`road_width_m`、`density`、`building_density` 等参数重新着色。

分析使用三层数据，避免把输入和落地结果混在一起：

1. **输入参数**: `preset_id`、prompt/template、`config_patch`、RAG/triple active 状态、directives、rejected edits。
2. **场景落地参数**: 从 `scene_layout.json.summary / config / placements / building_placements` 抽取道路宽度、人行道宽度、树/长椅/灯/建筑数量、密度、dropped/overlap/compliance/rule satisfaction 等。
3. **最终结果**: `walkability / safety / beauty / overall`、Pareto rank、parent-child delta。

## UI 设计主题

Web 界面采用统一的 **HKUST 主题风格**，贯穿所有页面：

### 配色方案

| 用途 | 颜色 | 说明 |
|:---|:---|:---|
| 主色 | `#00539F` | HKUST Blue，用于按钮、标题、导航等核心元素 |
| 强调色 | `#FFD100` | HKUST Yellow，用于徽章、重要提示、hover 效果 |
| 背景 | 透明科技白 | 半透明白色 + backdrop-filter 模糊效果 |
| 阴影 | 柔和阴影 | CSS 变量 `--shadow-md`，营造层次感 |

### 主题特性

- **透明面板**: 所有卡片和面板使用 `rgba(255, 255, 255, 0.85)` 半透明背景
- **毛玻璃效果**: `backdrop-filter: blur(12px)` 创建科技感
- **柔和阴影**: 多层阴影叠加，边缘有蓝色光晕
- **CSS 变量**: 统一使用 `--color-primary`、`--color-accent` 等变量，便于主题切换

### 统一组件

Viewer 子模块提供共享 UI 组件 (`ui.ts`):
- `setupMenuToggle()` — 响应式菜单切换
- `setupNavigation()` — 统一的页面导航

## Testing

Current focused checks for the Viewer + benchmark workflow:

```bash
# Backend branch runs, benchmark store, API compatibility
uv run pytest tests/test_branch_runs.py tests/test_design_api.py -q

# Viewer TypeScript API/types/UI wiring
cd web/viewer
npm run typecheck
```

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
./ops/scripts/test_llm_api.sh

# Test with specific model
./ops/scripts/test_llm_api.sh gpt-4

# List available models
./ops/scripts/test_llm_api.sh --list
```

## Make Targets

```bash
make help                 # Show all available targets
make dev                  # Start API + viewer
make api                  # Start FastAPI backend (port 8010)
make workbench-api        # Deprecated alias for make api
make workbench-web        # Archived legacy workbench; requires ENABLE_ARCHIVED_WORKBENCH=1
make viewer-web           # Start 3D viewer (port 4173)
make knowledge-build      # Build RAG knowledge base from design guide PDF
make collect              # Collect policy training data
make train                # Train layout policy
make eval                 # Run engineering evaluation
```

## Roadmap

### Completed ✓

- **归档旧 Workbench UI** — 当前交互式流程迁入 Viewer，`web/workbench` 默认不再启动
- **评估可视化** — 雷达图、柱状图、综合评分展示
- **方案对比** — 多方案并行生成与评分排序
- **Viewer URL 透传** — 正确传递场景布局路径到 3D 预览器
- **Pareto / 100 Sample Trace** — 支持单 preset 最多 100 个已评分样本、三目标 3D scatter、top-k artifact retention 和早停
- **Persistent Benchmark Store** — `samples.jsonl` 持久化历史样本，可跨 run / batch 复用
- **Correlation Analysis 面板** — 后端抽取 input / scene / derived 特征并计算 Spearman、preset residual、delta、Kruskal-Wallis、feature importance
- **GLB 按需重建** — 对保留 `scene_layout.json` 但缺失 `scene.glb` 的样本可重新组装 GLB

### Near-term

- Stabilize OSM + POI + width synthesis as the default generation path
- Strengthen constraint-type POI influence on layout (crossing, traffic_signals, subway_entrance, parking_entrance)
- Improve cross-section synthesis readability in UI summaries
- Add lighter async caching for full benchmark-analysis payloads when historical samples grow beyond the current 100/600 scale

### Mid-term

- Expand POI taxonomy to more complete street furniture system
- Make segment-level graph participate in layout (not just global bands)
- Deepen learned program generator integration as a strong backend

### Long-term

- Generalize beyond the curated graph-template cross-junction demo path
- Evolve from "asset placement" to a full "street design system" with editable cross-section presets
- Standardize research loop with versioned training data, fixed evaluation protocols, and result dashboards

## Current Limitations

- No cross-modal training (OpenShape/ULIP) — retrieval is CLIP text-only
- `shapee` direct latent decoding requires matching latent dimensions; production use recommends `mesh_ref`
- Current Viewer Scenario Designs generation is catalog/template-patch driven and sets `skip_llm`; the natural-language LLM/RAG draft path is a separate route.
- Course delivery path supports `graph_template` cross junctions; open-ended arbitrary street networks are still out of scope
- `StreetProgram` uses heuristic generator (`heuristic_v1`) — not yet replaced by a learned program generator
- Layout solver uses `banded` heuristic — not MILP or diffusion-based
- Benchmark conclusions are correlations and trace explanations, not strict causal estimates
- Visual scoring from rendered screenshots can still depend on the configured LLM evaluator; this is intentional until a non-LLM visual evaluator is available
- GLB is losslessly recoverable only when the corresponding `scene_layout.json` and referenced assets still exist

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
