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
| `template_patch` | object \| null | Graph template 变体 patch，仅 `graph_template` 生效 |

注意：`SceneContext` 是运行时上下文，不应混入 `compose_config_patch`。`template_patch` 不是 `StreetComposeConfig` 参数，它在 graph-template bridge 之前应用到 base annotation。

### 2.4 `template_patch`

代码来源：`src/roadgen3d/template_patch.py`
Schema：`data/schemas/template_patch.schema.json`

`template_patch` 是 Base Template 和 Scene Generation 之间的变体层。它可以修改 `cross_section_strips` 和 `functional_zones`，但不移动道路中心线、路口、建筑区域。

支持的 operation：

| op | 作用 |
| --- | --- |
| `resize_strip` | 调整某个 strip 宽度，例如缩窄机动车道或加宽人行道 |
| `update_strip` | 修改 strip 的 `zone/kind/width_m/direction` |
| `remove_strip` | 删除某个 strip，例如把四车道减到双向两车道 |
| `add_strip` | 添加新 strip，例如公交专用道、自行车道、中央绿带 |
| `replace_strips` | 替换某条 centerline 的完整横断面 |
| `add_functional_zone` / `upsert_functional_zone` | 添加或替换小广场、花园、户外座椅区等功能区 |
| `remove_functional_zone` | 删除功能区 |

默认约束会保证车道宽度、人行净宽、双向机动车通行等底线；例如默认至少保留双向各一条机动车道。

### 2.5 `generation_options`

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
| 路口与路缘几何 | `junction_corner_radius_mode`、`junction_corner_radius_m`、`junction_corner_min_radius_m`、`junction_corner_max_radius_m`、`junction_precision_grid_m`、`junction_seam_extension_m`、`junction_curve_max_angle_deg`、`junction_curve_max_chord_m`、`curb_width_m`、`curb_reveal_m`、`curb_top_mode` |
| 道路标线 | `urban_lane_edge_mode`、`junction_marking_setback_m` |
| 运行模式 | `layout_mode`、`road_selection`、`aoi_bbox`、`osm_cache_dir` |
| 规则与求解 | `design_rule_profile`、`layout_solver`、`constraint_mode`、`allow_solver_fallback` |
| 目标与需求 | `objective_profile`、`ped_demand_level`、`bike_demand_level`、`transit_demand_level`、`vehicle_demand_level` |
| 表现与资产 | `style_preset`、`beauty_mode`、`render_preset`、`scene_texture_mode`、`asset_curation_mode`、`asset_scale_mode` |
| 建筑/地块 | `building_density`、`building_max_per_100m`、`zoning_granularity`、`streetwall_continuity` |
| 可解释日志 | `placement_logging_mode` |

路口默认采用 `auto` 圆角：目标半径取相邻道路最大半宽的 `0.75` 倍，并限制在 `3–8m` 及可用道路臂长度内；曲线采样同时限制为每段不超过 `2°` 和 `0.25m`。路缘默认宽 `0.12m`、高差 `0.15m`，帽面与人行道齐平；生成器会在 1mm 精度网格上进行布尔归一化，并在导出网格中保留 2mm 的水平数值安全间隙，避免 GLTF float32 量化重新产生共面三角形。普通有路缘城市道路的 `urban_lane_edge_mode` 默认为 `explicit_only`，只有明确的路肩、专用车道或快速路语义才生成边缘标线；中心标线和边缘标线均在路口前 `0.5m` 截断。

`osm_geometry.surface_geometry_qa` 记录最终路缘/人行道重叠、残片和退化顶面；`osm_geometry.marking_geometry_qa` 记录路口侵入、重复标线、被抑制的自动边缘线和实际 ribbon 数量。超过容差的结果不会导出 GLB。

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

代码写出位置：

- 生成计算：`src/roadgen3d/street_layout.py::compose_street_scene()`
- 最终契约组装：`src/roadgen3d/scene_layout_payload.py`
- JSON Schema：`data/schemas/scene_layout.schema.json`

当前顶层字段：

| 字段 | 说明 |
| --- | --- |
| `schema_version` | 当前为 `roadgen3d.scene_layout.v1` |
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
| `semantic_design_layers` | A/B 语义分层最终解析结果，兼容 summary 内同名字段 |
| `environment_state` | C 环境表现层默认运行时状态 |
| `osm_semantic_blocks` | OSM multiblock 语义街区摘要 |
| `segment_semantic_profiles` | 道路 segment 的 OSM/语义 profile |
| `visual_style` | 材质、灯光 preset、surface palette、building profile |
| `placements` | 实际资产放置 |
| `environment_placements` | 天空盒等环境展示资产 |
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

### 4.1 语义分层字段

`scene_layout.json` 现在把街道生成语义拆成三层记录：

- **A: `skeleton_design_profile` / 骨架功能设计**：影响道路骨架、横断面、surface annotation、公交/慢行/车行优先级等空间功能。来源可为人工 Reference Annotation、LLM 标注或 OSM/POI 自动推断。
- **B: `street_furniture_profile` / 街道家具主题**：影响建筑、街道家具、设施组合、密度、材质和渲染风格。来源可为 Viewer 街道家具设计目标、LLM 推断或 A 层回退推荐。
- **C: `environment_state` / 环境表现层**：Viewer 运行时天气和日照默认状态；V1 只影响最终展示，不反向修改道路设计和评分。

优先级固定为 **manual annotation > LLM > OSM/POI automatic inference**。`summary.semantic_design_layers` 和顶层 `semantic_design_layers` 同时保留，便于旧 Viewer、评价器和报告工具读取。

### 4.2 OSM multiblock 字段

OSM 多街区流程保留原有 `semantic_profile_id`，同时把它作为 A 层 `skeleton_design_profile` 的兼容别名使用：

- `osm_semantic_blocks`：AOI 内 landuse / amenity / leisure / shop / tourism / office 等面状或关系要素形成的语义街区。
- `segment_semantic_profiles`：道路 segment 绑定的语义 profile、来源、置信度和理由。
- `summary.osm_semantic_mode`、`summary.semantic_block_count`、`summary.segment_semantic_profile_counts`：Viewer 和 demo 摘要使用的聚合记录。

### 4.3 Environment 字段

`environment_state` 默认由 sky manifest 派生，字段为：

| 字段 | 说明 |
| --- | --- |
| `weather_mode` | `clear`、`overcast`、`rain`、`fog` |
| `weather_intensity` | 0-1 展示强度 |
| `time_of_day_hours` | 0-24 艺术化日照时间 |
| `sun_cycle_enabled` | 是否默认自动循环 |
| `sun_cycle_speed` | `slow`、`medium`、`fast` |
| `source` | 默认 `default_runtime`，Viewer 调整后为运行时状态 |

`summary.environment_system` 记录 C 层是 `environment_runtime_v1`、天气枚举、`artistic_day_cycle` 和 `runtime_only=true`。

当前仍保留的历史复杂性：

- `outputs` 同时服务返回对象和落盘摘要，部分字段只在 `StreetComposeResult.outputs` 中补齐；schema 先允许 `outputs` 扩展，后续再把“文件内输出”和“返回对象输出”拆清。

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

1. 继续细化 `data/schemas/scene_layout.schema.json` 的 summary 子结构，优先覆盖 evaluation / Viewer 必读字段。
2. 新增 `schemas/api_scene_job.schema.json`。
3. 把 `outputs` 拆成落盘 `outputs` 与返回对象 `SceneComposeResult.outputs` 的明确边界。
4. 给历史 layout 提供 migration 或 best-effort loader。
