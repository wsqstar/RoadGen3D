# RoadGen3D 道路生成框架审查

> 生成日期：2026-05-03  
> 目的：梳理当前 RoadGen3D 的设计文档、实现文档与真实代码流程，判断它是否已经构成一个完整的道路生成框架，并列出缺口、冗余和下一步文档治理建议。

## 1. 结论摘要

当前 RoadGen3D 已经具备一个较完整的“研究型道路/街道场景生成框架”雏形：

- 有交互入口：`web/viewer` 已成为主界面，覆盖设计输入、生成、溯源、生长树、评价、历史分析、对比和 3D 查看。
- 有统一业务后端：`web/api/main.py` 通过 `DesignAssistantService`、`SceneJobService`、`BranchRunService` 编排 draft、生成、评价和优化。
- 有显式中间表示：`DesignDraft`、`SceneContext`、`StreetComposeConfig`、`StreetProgram`、`ConstraintSet`、`PlacementContext`、`scene_layout.json`。
- 有多模式生成路径：graph template、MetaUrban、OSM/reference annotation，以及模板/兼容路径。
- 有评价闭环：road-metrics 负责 walkability、safety、beauty 评分，Viewer 和 branch run 可以把评价结果反馈到改进流程。
- 有可视化与调试工具：Three.js viewer、floating lane overlay、scene graph、junction editor、asset editor、diff/compare/history。

但如果把目标定义为“完整道路生成框架”，目前仍更像“街道空间与资产布置生成框架”，还没有完全覆盖道路工程、交通运行、标准合规、数据治理和生产部署需要的全部层次。

最需要补齐的不是再加一个功能，而是建立清晰的 source of truth：

1. 一个权威的框架总文档。
2. 一个稳定的 `scene_layout.json` / `StreetProgram` schema 文档。
3. 一个 API 契约文档。
4. 一个评价指标与测试协议文档。
5. 一个 legacy/实验文档归档规则。

## 2. 当前主流程

### 2.1 产品主流程：Viewer 到生成结果

```text
用户 prompt / preset / graph template
  ↓
web/viewer
  - viewer-design-controller.ts
  - viewer-design.ts
  - viewer-branch-workspace.ts
  ↓ HTTP
web/api/main.py
  - /api/design/draft
  - /api/scene/jobs
  - /api/design/branch-runs
  - /api/design/evaluate/unified
  ↓
DesignAssistantService
  - LLM/RAG draft
  - scene job orchestration
  - road-metrics evaluation
  ↓
SceneJobService
  - single-process background queue
  - progress operations
  - generation trace
  ↓
generate_scene_from_draft()
  - normalize generation options
  - sanitize SceneContext
  - optional LLM parameter derivation
  - route by layout_mode
  ↓
compose_street_scene()
  - asset/material/sky backend loading
  - StreetProgram inference
  - ConstraintSet loading
  - LayoutSolver / placement planning
  - CLIP/manifest asset selection
  - collision-aware placement
  - GLB/PLY + scene_layout.json export
  ↓
viewer cache + recent layouts + evaluation
  ↓
Three.js viewer / evaluation panel / history / comparison
```

### 2.2 生成分支

`generate_scene_from_draft()` 根据 `SceneContext.layout_mode` 分流：

| 模式 | 入口 | 作用 |
| --- | --- | --- |
| `graph_template` | `build_graph_template_scene_bridge()` | 从内置/标注图模板生成道路图、POI 与放置上下文 |
| `metaurban` | `build_metaurban_scene_bridge()` | 对接 MetaUrban 风格道路块与参考计划 |
| `osm` | `resolve_scene_context()` + OSM/road discovery | 从真实道路、POI、建筑入口等外部上下文生成场景 |
| template/fallback | `compose_street_scene()` | 兼容直线街段和调试路径 |

### 2.3 自动迭代/研究流程

`src/roadgen3d/auto_pipeline/iteration_controller.py` 保留了独立的研究闭环：

```text
graph_ctx + optional base map
  ↓
LLM initial config_patch
  ↓
generate_scene_from_graph_context()
  ↓
topdown preview
  ↓
evaluate_scene_unified() / evaluate_scene_with_history()
  ↓
suggestions + config_patch
  ↓
repeat until score improves or early-stop
```

这条线很适合论文实验和自动化 benchmark，但需要和产品主流程明确区分。

## 3. 现有文档地图

### 3.1 应作为主线保留的文档

| 文档 | 当前价值 | 建议 |
| --- | --- | --- |
| `readme.md` | 对外入口，包含安装、架构、CLI、API、OSM、auto pipeline | 保留，但压缩重复 quick start，把“当前主流程”链接到一个权威文档 |
| `docs/CURRENT_PROJECT_ARCHITECTURE_ANALYSIS.md` | 最接近真实代码的架构审查 | 更新日期和 stale 部分，作为架构审计记录 |
| `web/viewer/ARCHITECTURE.md` | Viewer 代码组织守则，简洁有效 | 保留，继续作为前端改动 guard |
| `docs/cross-junction-ribbon-corner-data-layer.md` | 对 junction surface 数据层解释清楚 | 保留为 feature design note |
| `src/roadgen3d/eval_engine_ext/road_metrics/LAYERED_ARCHITECTURE.md` | 评价引擎分层原则清晰 | 保留，但主仓文档应只引用，不重复展开 |

### 3.2 需要合并或改名的文档

| 文档 | 问题 | 建议 |
| --- | --- | --- |
| `docs/ARCHITECTURE.md` | 仍包含 Workbench `useGeneration.ts` 的一键优化实现，和“Workbench 已归档”状态冲突 | 改成当前主架构文档，移除 Workbench 作为主流程的叙述 |
| `docs/evaluation-system.md`、`docs/scoring_formula_specification.md`、`docs/EVALUATION_REPORT.md`、`docs/evaluation_module_plan.md` | 评分公式、计划、实现说明高度重叠 | 合并成 `docs/EVALUATION.md`，区分“当前实现”和“规划扩展” |
| `docs/workbench_web_vs_test_pipeline.md` | 对比对象是已归档 Workbench，仍有历史价值但不是当前主流程 | 移入 `docs/archive/` 或标注 legacy |
| `docs/SCENE_COMPARE_*`、`docs/comparison-features.md`、`docs/scene-compare-events.ts` | 多份 compare 设计记录散落 | 合并成一份 `docs/features/scene-compare.md` |
| `docs/roadgen3d_scenario_plan.md` | 更像早期需求/资产计划 | 保留为 planning note，需标注哪些已经实现、哪些仍缺失 |

### 3.3 当前文档中的明显漂移

- `docs/ARCHITECTURE.md` 同时说 Viewer 是主界面，又给出 Workbench `useGeneration.ts` 的主流程代码，容易误导。
- `docs/CURRENT_PROJECT_ARCHITECTURE_ANALYSIS.md` 还写 `make dev` 会启动 Workbench，但 README 已说明 Workbench 默认归档。
- `web/viewer/vite.config.ts` 仍提供本地文件 API 和一套 `PRESETS`，这与后端 `src/roadgen3d/presets.py`、Viewer `VIEWER_DESIGN_PRESETS` 存在配置漂移。
- Viewer 设计预设目前只列了 4 个，而后端正式 `SCENE_PRESETS` 是 6 个。
- 评价文档多处重复三维评分公式，但没有单一版本说明“当前 API 返回字段”和“子模块内部字段”的映射。

## 4. 是否符合完整道路生成框架

### 4.1 已经具备的框架能力

| 框架层 | 当前状态 | 说明 |
| --- | --- | --- |
| 需求输入 | 已具备 | prompt、preset、graph template、reference annotation、OSM context |
| 知识增强 | 已具备 | PDF RAG、GraphRAG、scenario-parameter triples |
| 参数化配置 | 已具备 | `DesignDraft`、`compose_config_patch`、`StreetComposeConfig` |
| 道路/街道语义 | 部分完整 | lanes、sidewalk、strip/band、furnishing、frontage、junction surfaces |
| 约束系统 | 已具备雏形 | `ConstraintSet`、design rule profiles、hard/soft rules |
| 生成求解 | 已具备 | `StreetProgram`、`LayoutSolver`、rule/learned placement fallback |
| 资产系统 | 已具备 | manifest backend、curated assets、CLIP retrieval、material/sky backend |
| 场景输出 | 已具备 | `scene_layout.json`、GLB/PLY、viewer cache、topdown preview |
| 评价系统 | 已具备 | road-metrics 三维评分、LLM visual eval、history comparison |
| 闭环优化 | 部分具备 | branch run、auto pipeline、suggestion/config patch |
| 可视化工具 | 已具备 | Viewer、scene graph、floating lanes、junction editor、asset editor |

### 4.2 仍不像“完整道路生成框架”的地方

当前更强的是“街道空间设计 + 城市家具/场景生成”，较弱的是“道路工程 + 交通运行 + 标准合规”。

主要缺口：

1. **道路网络与交通拓扑**
   - 有 road segment graph 和 junction surface，但还缺少稳定的 lane-level connectivity、turn movement、signal phase、conflict point 模型。
   - 目前更偏几何铺装和放置上下文，尚未成为完整交通网络生成器。

2. **标准合规体系**
   - `design_rules.py` 已有规则 profile，但 NACTO/ADA/AASHTO/本地规范没有形成可版本化的标准库。
   - 缺少“规则来源、适用场景、硬/软约束、测试样例”的统一表。

3. **动态仿真与运行评价**
   - 缺少车辆、行人、自行车、公交运行模拟。
   - 安全评价目前偏结构化指标和视觉/LLM，不等价于冲突仿真、延误、容量、LOS、可达性时空分析。

4. **真实城市数据闭环**
   - OSM/POI 已接入，但 GTFS、信号灯、车速、事故、地形坡度、排水、路权边界、地块/建筑功能等还未成为标准输入。
   - 缺少 GIS 坐标系、导入/导出和可复现数据版本管理规范。

5. **Schema 与契约**
   - `scene_layout.json` 是事实标准，但缺少版本号、JSON Schema、字段稳定性承诺和迁移策略。
   - `StreetProgram`、`PlacementContext`、`EvaluationResult` 的文档仍分散在代码和测试中。

6. **生产化任务系统**
   - `SceneJobService` 是单进程、单线程、内存队列，适合本地 demo，不适合多人/服务化。
   - 缺少持久化 job store、artifact registry、重试/取消、并发 worker、权限和资源隔离。

7. **实验复现和质量门槛**
   - 已有大量测试，但缺少统一的 scenario benchmark matrix。
   - 需要固定种子、固定资产版本、固定评价配置、golden scene snapshots 和跨版本指标趋势。

8. **人机协同编辑闭环**
   - Viewer 能看、能比较、能移动资产、能标注，但“手工编辑 → schema patch → 重新求解 → 评价”的闭环还不够正式。

## 5. 多了什么或边界过宽

这些能力本身有价值，但不应被写成道路生成框架的核心主线：

1. **Legacy Workbench**
   - 源码可保留，但文档里不应再作为主交互流程。

2. **Viewer Vite middleware**
   - `/api/layout`、`/api/file`、asset manifest save/delete、diff image 等本地文件 API 很方便，但属于 dev tool/service adapter。
   - 如果写入系统架构，需要明确它不是生产后端。

3. **重复预设系统**
   - `src/roadgen3d/presets.py`
   - `web/viewer/src/viewer-types.ts`
   - `web/viewer/vite.config.ts`
   - 旧测试/文档中的 preset 常量
   - 这些应该收敛到一个权威来源，通过 API 或生成脚本同步。

4. **重复评价引擎叙事**
   - 主仓的 `eval_engine`、子模块 `eval_engine_ext/road_metrics`、多份 docs 同时讲评价。
   - 建议把 road-metrics 作为唯一当前实现，其他文档标注 legacy/migration。

5. **Feature 设计文档过散**
   - scene compare、junction editor、scenario plan、diff、scatter plot 都有独立文档。
   - 建议保留，但放入 `docs/features/`，并在主架构文档只列索引。

6. **Viewer 功能过宽**
   - 3D viewing、设计生成、评价、history、asset editor、annotation、junction editor、compare 都在一个子应用里。
   - 这可以继续作为 RoadGen3D Studio，但要在文档上把“核心生成框架”和“工具生态”分开。

## 6. 建议的文档重组

建议把文档分成 5 层：

```text
docs/
  ROADGEN3D_FRAMEWORK.md        # 唯一主架构和流程 source of truth
  DATA_CONTRACTS.md             # scene_layout / StreetProgram / API payload schema
  EVALUATION.md                 # 当前评价实现、公式、API 字段映射、配置
  DEPLOYMENT_AND_JOBS.md        # API、viewer middleware、job queue、artifact 管理
  features/
    viewer.md
    branch-runs.md
    junction-editor.md
    scene-compare.md
    asset-editor.md
  archive/
    workbench_web_vs_test_pipeline.md
    old_scene_compare_notes.md
```

主文档只回答 4 个问题：

1. 输入是什么？
2. 中间表示是什么？
3. 生成和评价如何执行？
4. 输出如何被查看、复现和比较？

## 7. 建议补齐的工程任务

优先级按“让框架真正闭合”的重要性排序。

### P0：定义契约与权威来源

- 为 `scene_layout.json` 增加 `schema_version` 和 JSON Schema。
- 为 `StreetComposeConfig` / `DesignDraft` / `SceneContext` / `EvaluationResult` 写契约表。
- 将 presets 收敛到 `src/roadgen3d/presets.py`，Viewer 通过 API 拉取。
- 给文档加 `Status: current / planning / legacy` 和 `Last verified`。

### P1：补道路框架核心

- lane-level connectivity 和 movement model。
- junction turning movement、crosswalk relation、signal/control model。
- 标准规则库：规则来源、参数、适用场景、测试夹具。
- OSM/Reference annotation 到 road graph 的稳定 schema。

### P2：补评价和仿真

- 固定 benchmark 场景集和 golden outputs。
- 评价配置版本化。
- 引入基础交通运行指标：冲突点、延误、可达性、可视域、过街暴露时间。
- 明确 LLM visual eval 与结构化指标的可用性、失败降级和置信度。

### P3：补生产化能力

- 持久化 job store。
- 多 worker / cancel / retry。
- Artifact registry。
- 资产版本、LOD、渲染性能和质量检查。
- Viewer 本地 middleware 与 FastAPI 的边界治理。

## 8. 推荐的表述定位

当前最准确的定位建议是：

> RoadGen3D is a neuro-symbolic street-scene generation and evaluation framework. It turns design intent, graph templates, OSM/reference context, and rule profiles into editable 3D street scenes with explainable placement, structured evaluation, and iterative design feedback.

中文可以写成：

> RoadGen3D 是一个神经符号街道场景生成与评价框架。它将设计意图、图模板、OSM/参考图上下文和规则 profile 转换为可解释、可评价、可迭代改进的 3D 街道场景。

暂时不建议直接宣称为“完整道路工程生成框架”，除非后续补齐 lane movement、标准合规、交通仿真和 GIS 数据契约。
