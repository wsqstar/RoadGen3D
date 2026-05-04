# RoadGen3D 数据契约

> Status: current draft  
> Last verified: 2026-05-03  
> Scope: 记录当前代码已经稳定依赖的数据结构。本文不是最终 JSON Schema，但用于指导后续 schema/versioning 工作。

## 1. 契约原则

RoadGen3D 当前已有多个事实契约，但还没有统一 schema 文件。后续改动应遵循：

- `scene_layout.json` 是跨模块最重要的输出契约。
- `DesignDraft`、`SceneContext`、`StreetComposeConfig` 是生成请求契约。
- `StreetProgram`、`ConstraintSet`、`LayoutSolverResult` 是核心中间表示契约。
- `SceneJobStatusResponse` 是 Viewer 生成进度契约。
- API 响应字段可以扩展，但不能无提示删除或重命名已被 Viewer 和 tests 使用的字段。

## 2. API 请求契约

### 2.1 `DesignDraft`

代码来源：`src/roadgen3d/services/design_types.py`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `normalized_scene_query` | string | 规范化后的设计需求 |
| `compose_config_patch` | object | 允许覆盖的生成配置 |
| `citations_by_field` | object | field -> citation ids，用于溯源 |
| `design_summary` | string | 给 UI 和 trace 展示的简短摘要 |
| `risk_notes` | string[] | 生成前风险提示 |
| `parameter_sources_by_field` | object | field -> source，用于解释参数来源 |

`compose_config_patch` 只接受 `ALLOWED_COMPOSE_CONFIG_PATCH_FIELDS` 中的字段，入口会通过 `sanitize_compose_config_patch()` 清洗。

### 2.2 `compose_config_patch` 允许字段

当前允许字段包括：

```text
query
design_rule_profile
target_street_type
objective_profile
city_context
style_preset
beauty_mode
render_preset
topdown_render_mode
scene_texture_mode
asset_curation_mode
asset_scale_mode
curated_street_assets_profile
program_generator
layout_solver
length_m
road_width_m
sidewalk_width_m
lane_count
density
building_density
building_max_per_100m
ped_demand_level
bike_demand_level
transit_demand_level
vehicle_demand_level
allow_solver_fallback
```

这意味着 Viewer 或 LLM 不应该直接把任意字段塞入 patch；运行时上下文要放进 `SceneContext`。

### 2.3 `SceneContext`

代码来源：`src/roadgen3d/services/design_types.py`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `layout_mode` | `"template" | "osm" | "metaurban" | "graph_template"` | 选择生成分支 |
| `aoi_bbox` | `[min_lon, min_lat, max_lon, max_lat] | null` | OSM/城市上下文范围 |
| `city_name_en` | string \| null | 城市名 |
| `reference_plan_id` | string \| null | MetaUrban reference plan，仅 `metaurban` 生效 |
| `graph_template_id` | string \| null | Graph template id，仅 `graph_template` 生效 |

注意：`SceneContext` 是运行时上下文，不应混入 `compose_config_patch`。

### 2.4 `generation_options`

代码来源：`SceneGenerationOptions`

常用字段：

| 字段 | 说明 |
| --- | --- |
| `manifest_path` | legacy asset manifest |
| `object_manifest_v2_path` | v2 object manifest |
| `ground_material_manifest_path` | ground material manifest |
| `sky_manifest_path` | sky manifest |
| `out_dir` | 输出目录 |
| `export_format` | `glb`、`ply` 或两者 |
| `placement_policy` | `rule` 或 `learned` |
| `policy_ckpt` | learned placement checkpoint |
| `program_ckpt` | learned program checkpoint |
| `random_seed` | API 层支持的随机种子，当前在 `generate_scene_from_draft()` 中处理 |

## 3. 核心生成配置

### 3.1 `StreetComposeConfig`

代码来源：`src/roadgen3d/types.py`

这是 `compose_street_scene()` 的主配置。关键字段分组如下：

| 分组 | 字段 |
| --- | --- |
| 基础几何 | `length_m`、`road_width_m`、`sidewalk_width_m`、`lane_count`、`density` |
| 运行模式 | `layout_mode`、`road_selection`、`aoi_bbox`、`osm_cache_dir` |
| 规则与求解 | `design_rule_profile`、`layout_solver`、`constraint_mode`、`allow_solver_fallback` |
| 目标与需求 | `objective_profile`、`ped_demand_level`、`bike_demand_level`、`transit_demand_level`、`vehicle_demand_level` |
| 表现与资产 | `style_preset`、`beauty_mode`、`render_preset`、`scene_texture_mode`、`asset_curation_mode`、`asset_scale_mode` |
| 建筑/地块 | `building_density`、`building_max_per_100m`、`zoning_granularity`、`streetwall_continuity` |
| 可解释日志 | `placement_logging_mode` |

### 3.2 `StreetProgram`

代码来源：`src/roadgen3d/types.py`、`src/roadgen3d/street_program.py`

`StreetProgram` 是从配置、POI 和库存推导出的结构化街道意图：

| 字段 | 说明 |
| --- | --- |
| `query` | 原始/规范化设计需求 |
| `road_type` | 推断道路类型 |
| `lane_count` | 车道数 |
| `cross_section_type` | 横断面类型 |
| `road_width_m`、`sidewalk_width_m`、`furnishing_width_m` | 基础宽度 |
| `bands` | 功能带列表，每个 band 有 `name/kind/side/width_m/z_center_m/allowed_categories` |
| `furniture_requirements` | 类别 -> 需要数量 |
| `design_goals` | 设计目标 |
| `throughput_requirements` | 模式通行需求 |
| `band_bounds` | band 宽度上下限 |
| `observed_poi_counts` | 上下文 POI 统计 |
| `theme_segments` | 主题分段 |
| `poi_fit_report` | POI 与横断面适配报告 |

### 3.3 `ConstraintSet`

代码来源：`src/roadgen3d/design_rules.py`

`ConstraintSet` 是一组 `DesignRuleSpec`：

| 字段 | 说明 |
| --- | --- |
| `name` | profile 名称，如 `balanced_complete_street_v1` |
| `description` | 规则集说明 |
| `rules` | `DesignRuleSpec[]` |

`DesignRuleSpec` 关键字段：

| 字段 | 说明 |
| --- | --- |
| `name` | 规则名 |
| `target` | 规则作用对象，如 `lane_count`、`band_min_width` |
| `mode` | `hard` 或 `soft` |
| `operator` | `>=`、`<=`、`in`、`adjacent` 等 |
| `value` | 阈值/目标值 |
| `parameters` | 规则参数 |

### 3.4 `LayoutSolverResult`

代码来源：`src/roadgen3d/types.py`、`src/roadgen3d/layout_solver.py`

核心字段：

| 字段 | 说明 |
| --- | --- |
| `resolved_program` | 求解后的 `StreetProgram` |
| `band_solutions` | 每个 band 的宽度解 |
| `slot_plans` | 资产放置前的 slot 计划 |
| `rule_evaluations` | 每条规则的 pass/fail/score |
| `edits` | 求解器引入的解释性修改 |
| `conflicts` | 未解决冲突 |
| `topology_validity` | 拓扑有效性 |
| `cross_section_feasibility` | 横断面可行性 |
| `rule_satisfaction_rate` | 规则满足率 |
| `backend_requested` / `backend_used` | 便于追踪 fallback |

## 4. `scene_layout.json`

代码写出位置：`src/roadgen3d/street_layout.py::compose_street_scene()`

当前顶层字段：

| 字段 | 说明 |
| --- | --- |
| `query` | 生成 query |
| `config` | `StreetComposeConfig.to_dict()` |
| `selected_object_backend` | 对象资产 backend |
| `selected_ground_materials` | 地面材质选择 |
| `selected_sky` | sky 选择 |
| `environment_source_dataset(s)` | 环境素材来源 |
| `program_generation` | program generator 输出与 fallback 信息 |
| `street_program` | resolved `StreetProgram` |
| `constraint_set` | 规则集 |
| `solver` | `LayoutSolverResult` |
| `summary` | Viewer/评价/报告使用的摘要 |
| `placements` | 实际资产放置 |
| `building_footprints` | 建筑 footprint |
| `generated_lots` | 生成地块 |
| `building_placements` | 建筑放置 |
| `zoning_grid` | zoning 网格 |
| `functional_zones` | 功能区 |
| `production_steps` | 生产步骤快照 |
| `unplaced_slot_diagnostics` | 未放置诊断 |
| `placement_decision_log` | 放置决策日志路径与摘要 |
| `outputs` | 输出路径和配置摘要 |
| `supervision_sample` | 训练/监督样本 |
| `scene_graph` | Viewer 场景图 |

当前缺口：

- 尚无 `schema_version`。
- 尚无 JSON Schema。
- `outputs` 写入顺序存在历史复杂性，后续加 schema 时应明确哪些字段必须出现在文件内，哪些只属于返回对象。

## 5. Job 状态契约

`SceneJobStatusResponse` 用于 Viewer 轮询。

| 字段 | 说明 |
| --- | --- |
| `job_id` | 任务 id |
| `status` | `queued`、`running`/`processing`、`succeeded`、`failed` |
| `created_at`、`started_at`、`finished_at` | 时间 |
| `error` | 失败信息 |
| `stage` | 当前阶段，如 `context_resolving`、`asset_loading`、`scene_rendering` |
| `progress` | 0-100 |
| `operations` | 最近进度事件 |
| `result` | `SceneGenerationResult` |
| `trace` | RAG、参数来源、生成过程 trace |

Viewer 应优先使用后端返回的 `progress` 和 `operations`，只在缺失时 fallback 到本地 stage 估计。

## 6. 下一步 schema 工作

建议按以下顺序推进：

1. 为 `scene_layout.json` 增加 `schema_version: "roadgen3d.scene_layout.v1"`。
2. 新增 `schemas/scene_layout.schema.json`。
3. 新增 `schemas/api_scene_job.schema.json`。
4. 在 `tests/` 中加入 schema validation。
5. 给历史 layout 提供 migration 或 best-effort loader。
