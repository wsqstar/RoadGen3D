# RoadGen3D 当前框架总览

> Status: current  
> Last verified: 2026-05-08  
> Scope: 当前代码中的主生成、评价、可视化和迭代流程。历史 Workbench、实验脚本和单项 feature 设计只作为辅助入口。

## 1. 定位

RoadGen3D 当前最准确的定位是：

> 一个规则/约束驱动、AI 辅助的 3D 街道场景生成与评价框架。它将场景设计目录、图模板/参考标注、preset/config patch 和可选 prompt 转换为可解释、可评价、可迭代改进的 3D 街道场景。

它已经覆盖街道空间设计、横断面组织、街道家具/建筑布置、场景输出、评价和 Viewer 展示。代码里有 `StreetProgram`、`ConstraintSet`、`LayoutSolver` 等显式结构，也有 LLM/RAG 和 `learned_v1` program generator 等可选接口；但当前 Viewer 主用的 Scenario Designs 批量生成是 catalog + template patch 驱动，并设置 `preset_id=skip_llm`，不应被描述成已经落地的严格神经符号模型。它尚未完整覆盖道路工程级的 lane movement、信号控制、交通仿真、道路规范库和生产级任务调度。

## 2. 主流程

当前产品主流程以 `web/viewer` 为交互入口，以 `web/api/main.py` 为业务后端，以 `compose_street_scene()` 为生成核心。Viewer 现在有两类主要生成入口：

1. `Scenario Designs`：当前组会/demo 最稳定路径，读取场景目录并批量提交 `/api/scenario-designs/runs`。
2. `Design / Branch`：保留 prompt/preset、branch run、benchmark 和 Pareto trace 路径，提交 `/api/scene/jobs` 或 `/api/design/branch-runs`。

当前 Scenario Designs 批量路径：

```text
data/scenario_designs/*.json
  - scenario_id / query / intent
  - regions / functional_zones / surface_annotations
  - template_patch_operations
  - compose_config_patch
  ↓
Viewer
  - web/viewer/src/viewer-scenario-designs.ts
  ↓
FastAPI
  - web/api/main.py
  - /api/scenario-designs
  - /api/scenario-designs/runs
  ↓
Service layer
  - ScenarioDesignService
  - SceneJobService
  ↓
Generation request
  - DesignDraft generated without LLM re-drafting
  - template_patch + graph_template SceneContext
  - preset_id=skip_llm
  ↓
Core generation
  - build_graph_template_scene_bridge()
  - compose_street_scene()
  - StreetProgram
  - ConstraintSet
  - LayoutSolver
  - asset/material/sky backends
  ↓
Artifacts
  - scene_layout.json
  - scene.glb / scene.ply
  - production_steps
  - presentation renders
  ↓
Evaluation and display
  - road-metrics EvalEngine
  - Viewer 3D render
  - evaluation panel
  - compare/history/branch views
```

Design 面板和 Branch/Pareto 路径仍可使用 `/api/design/draft`、`/api/scene/jobs`、`/api/design/branch-runs`、`/api/design/evaluate/unified`，但它们不应被混同为当前 Scenario Designs 面板的生成线路。

## 3. 分层职责

| 层 | 当前主模块 | 职责 |
| --- | --- | --- |
| UI / Studio | `web/viewer` | 设计输入、生成控制、3D 查看、评价展示、历史与对比、资产/标注工具 |
| API | `web/api/main.py` | 面向前端的业务接口、Pydantic 请求模型、服务对象初始化 |
| Workflow service | `DesignAssistantService` | LLM/RAG draft、scene job 调用、road-metrics 评价、改进建议 |
| Job service | `SceneJobService`、`BranchRunService` | 单进程后台任务、进度事件、生成 trace、生长树搜索 |
| Context resolution | `design_runtime.py`、`scene_context_service.py` | 规范化生成选项、解析 graph/OSM/reference context |
| Core generation | `street_layout.py` | 街道程序生成、约束求解、资产检索、碰撞放置、GLB/JSON 输出 |
| Structured generation IR | `types.py`、`street_program.py`、`design_rules.py`、`layout_solver.py` | `StreetProgram`、`ConstraintSet`、`LayoutSolverResult` 等中间表示 |
| Evaluation | `eval_engine_ext/road_metrics` | walkability、safety、beauty 与综合评分 |
| Viewer local adapter | `web/viewer/vite.config.ts` | 本地开发态文件读取、recent layouts、asset manifest、diff image 等辅助 API |

## 4. 输入类型

| 输入 | 当前支持 | 说明 |
| --- | --- | --- |
| Scenario design catalog | 当前主 demo 路径 | `data/scenario_designs/*.json` 通过 `ScenarioDesignService` 转成 `template_patch` 和 `compose_config_patch` |
| 自然语言 prompt | 支持但不是 Scenario Designs 主路径 | 通过 LLM/RAG 生成 `DesignDraft`；也可在 Viewer Design 面板中直接作为 draft query |
| 后端 preset | 支持 | `src/roadgen3d/presets.py` 是后端正式来源 |
| Viewer preset | 支持但需收敛 | `web/viewer/src/viewer-types.ts` 目前维护了部分 preset，存在漂移风险 |
| Graph template | 支持 | `src/roadgen3d/graph_templates.py` |
| Reference annotation | 支持 | 通过 `/api/reference-annotations/convert` 转换 |
| OSM / city context | 部分支持 | OSM ingest、road discovery、POI context 已有，但数据契约仍需稳定 |

## 5. 生成模式

`SceneContext.layout_mode` 是当前生成分支的关键字段。

| `layout_mode` | 主入口 | 结果 |
| --- | --- | --- |
| `graph_template` | `build_graph_template_scene_bridge()` | 用内置图模板构建道路、POI 与放置上下文 |
| `metaurban` | `build_metaurban_scene_bridge()` | 用 MetaUrban 风格 reference plan 构建场景 |
| `osm` | `resolve_scene_context()` | 从真实道路、POI、建筑/入口等生成放置上下文 |
| `template` | `compose_street_scene()` | 兼容直线街段和调试路径 |

## 6. 显式中间表示

RoadGen3D 的核心价值在于不直接从输入跳到网格，而是经过显式中间表示。当前 Scenario Designs 路径的输入主要来自 catalog 和 template patch；prompt/LLM/RAG 路径是另一条可用入口。

1. `DesignDraft`：由 scenario catalog、LLM/RAG、preset 或 Viewer 输入生成的设计草案。
2. `SceneContext`：运行时布局上下文，不进入 LLM patch。
3. `StreetComposeConfig`：核心生成配置。
4. `StreetProgram`：街道意图、横断面、功能带、家具需求和目标。
5. `ConstraintSet`：规则 profile 和 hard/soft constraints。
6. `LayoutSolverResult`：求解后的 band widths、slot plans、rule evaluations、conflicts。
7. `scene_layout.json`：Viewer、评价、diff 和后续工具共用的事实输出格式。

注意：这些结构支撑了“可解释/可追踪/规则约束驱动”的表述，但当前代码尚不能证明完整“神经符号框架”已经实现。`learned_v1` program generator 和 LLM/RAG 参数推导是可选增强，当前 Scenario Designs 批量生成显式跳过 LLM 重推导。

详细字段见 [DATA_CONTRACTS.md](DATA_CONTRACTS.md)。

## 7. 评价闭环

评价主线由 `DesignAssistantService.evaluate_scene_unified()` 调用 road-metrics `EvalEngine`：

```text
scene_layout.json + optional rendered_views
  ↓
road-metrics EvalEngine
  ↓
walkability / safety / beauty / overall
  ↓
indicators + suggestions + config_patch
  ↓
Viewer panel / branch run ranking / auto iteration
```

详细字段和失败降级见 [EVALUATION.md](EVALUATION.md)。

## 8. 当前边界

这些能力存在，但不应被误认为主框架核心：

- `web/workbench`：已归档 legacy UI，默认不启动。
- `web/viewer/vite.config.ts` 的本地 API：开发工具适配层，不是生产后端。
- `src/roadgen3d/services/generation_api.py`：直接生成 API 草案/辅助路径，当前主业务仍走 `web/api/main.py`。
- `auto_pipeline`：研究/实验闭环，适合 benchmark 和论文实验，不是当前交互式主流程。
- “神经符号”只能作为长期方向或可选组件描述；当前主线更适合表述为规则/约束驱动、AI 辅助。

## 9. 下一步治理顺序

1. 收敛 presets 到后端权威来源。
2. 给 `scene_layout.json` 增加 `schema_version` 和 JSON Schema。
3. 把 Viewer 本地 API 与 FastAPI 生产 API 的边界写清。
4. 建立 benchmark scenario matrix 和 golden artifacts。
5. 若要重新提升“神经符号”表述强度，需要让 learned program generator、训练数据、checkpoint、消融评估和 Viewer 主路径真正闭合。
