# RoadGen3D 当前模块与节点设计梳理

更新时间：2026-03-12

本文基于当前仓库代码实现整理，不按“规划中的目标系统”描述，而按“现在代码里实际已经存在的模块与节点”描述。

说明：

- 这里的“节点”按业务节点 / 公共入口 / 主流程阶段划分，不细拆所有私有 helper。
- 模块边界以当前源码职责为准；部分编排逻辑目前放在脚本层，尤其是 [`scripts/m1_gradio_app.py`](../scripts/m1_gradio_app.py) 和 [`scripts/m3_01_compose_street.py`](../scripts/m3_01_compose_street.py)。
- 当前系统的真实主干是 `准备 -> 资产库就绪 -> OSM/POI上下文 -> StreetProgram -> ConstraintSet/LayoutSolver -> 资产实现 -> 导出/评估/训练`。

## 1. 总体架构

```text
Prepare Workspace
  -> manifest / latents / index / OSM cache / discovered roads

Asset Library
  -> real manifest / parametric assets / FAISS index / latent store

Street Compose
  -> OSM fetch + parse + project
  -> road selection + POI-aware cross section + placement context
  -> road segment graph + theme segments + spatial context
  -> StreetProgram generation
  -> ConstraintSet compilation + LayoutSolver
  -> asset retrieval + pose search + constraint scoring + placement
  -> surrounding building planning
  -> scene export + production steps + scene graph + presentation views

Research
  -> policy data collection / policy training
  -> program data collection / program training
  -> engineering/compliance evaluation
```

## 2. 模块总表

| 模块 | 主要职责 | 关键文件 | 主要输出 |
| --- | --- | --- | --- |
| 1. Workspace 准备 | 检查工作区是否可运行，并补齐 manifest、latent、index、OSM cache、发现候选道路 | [`scripts/m1_gradio_app.py`](../scripts/m1_gradio_app.py) | `PrepareWorkspaceResult`、`discovered_poi_roads.jsonl` |
| 2. 资产库与检索解码 | 构建真实/模拟资产库，完成 `text -> retrieval -> latent -> voxel -> mesh` | [`src/roadgen3d/pipeline.py`](../src/roadgen3d/pipeline.py) | `PipelineResult`、`index_ip.faiss`、GLB/PLY |
| 3. 参数化资产生成 | 生成 Bench/Lamp 参数化资产，并登记到真实资产库 | [`src/roadgen3d/parametric_assets.py`](../src/roadgen3d/parametric_assets.py) | mesh、质量指标、manifest 行 |
| 4. OSM/POI 空间理解 | 拉取 OSM、解析道路/POI、投影、本地化、选路、横断面合成、构建图结构 | [`src/roadgen3d/osm_ingest.py`](../src/roadgen3d/osm_ingest.py) | `PlacementContext`、`RoadSegmentGraph`、`PoiContext` |
| 5. 街道程序与约束求解 | 从 query/上下文推断 `StreetProgram`，再编译为受规则约束的槽位计划 | [`src/roadgen3d/street_program.py`](../src/roadgen3d/street_program.py)、[`src/roadgen3d/layout_solver.py`](../src/roadgen3d/layout_solver.py) | `StreetProgram`、`ConstraintSet`、`LayoutSolverResult` |
| 6. 场景实现与导出 | 资产检索、位姿搜索、约束评分、实例摆放、建筑补全、导出场景与摘要 | [`src/roadgen3d/street_layout.py`](../src/roadgen3d/street_layout.py) | `scene.glb`、`scene_layout.json`、production steps、presentation views |
| 7. 评估与可视化 | 生成工程指标、合规指标、空间图和 scene graph | [`src/roadgen3d/eval_metrics.py`](../src/roadgen3d/eval_metrics.py)、[`src/roadgen3d/compliance_eval.py`](../src/roadgen3d/compliance_eval.py) | 报告 JSON/CSV、2D 图、scene graph |
| 8. 研究训练 | 收集 policy/program 训练样本，训练 learned policy 与 learned program generator | [`scripts/m4_01_collect_policy_data.py`](../scripts/m4_01_collect_policy_data.py)、[`scripts/m6_01_collect_program_data.py`](../scripts/m6_01_collect_program_data.py) | `policy_train.jsonl`、`program_train.jsonl`、ckpt、curve |

## 3. 核心中间对象

| 对象 | 作用 | 主要生产者 | 主要消费者 |
| --- | --- | --- | --- |
| `WorkspaceReadiness` | 工作区就绪状态 | `inspect_workspace_readiness` | `prepare_workspace`、UI 准备页 |
| `PrepareWorkspaceResult` | 准备流程汇总 | `prepare_workspace` | UI 准备页 |
| `StreetComposeConfig` | 一次街道生成的总配置 | CLI / UI 输入 | 几乎所有生成模块 |
| `InventorySummary` | 当前资产库的类别计数与资产 id 汇总 | `compose_street_scene`、训练脚本 | Program generator、Layout solver |
| `PlacementContext` | OSM 路面、人行空间、POI、本地 AOI 的几何上下文 | `build_placement_context` | Program、placement、可视化 |
| `PoiContext` | 轻量 POI 点集上下文 | `build_poi_context` | POI 规则评分、scene graph、空间分析 |
| `RoadSegmentGraph` | 将 OSM 道路离散为段图 | `build_segment_graph` | Theme inference、solver、program features |
| `StreetProgram` | 文本意图和 POI/规则之间的显式中间表示 | `infer_street_program` / `ProgramGeneratorRuntime.generate` | Layout solver、scene export |
| `ConstraintSet` | 声明式设计规则集合 | `load_constraint_set` | `LayoutSolverRuntime.solve` |
| `LayoutSolverResult` | 规则编译后的槽位计划和解释结果 | `LayoutSolverRuntime.solve` | `compose_street_scene`、评估与导出 |
| `StreetPlacement` | 一个已落地实例 | `compose_street_scene` | 导出、评估、scene graph |
| `StreetComposeResult` | 一次完整街道生成的顶层结果 | `compose_street_scene` | CLI、UI、评估脚本 |

## 4. 模块拆解

### 4.1 Workspace 准备模块

当前这部分编排主要在 [`scripts/m1_gradio_app.py`](../scripts/m1_gradio_app.py)。

| 节点 | 代码入口 | 输入 | 处理 | 输出 |
| --- | --- | --- | --- | --- |
| 就绪检查 | `inspect_workspace_readiness(...)` | 数据集 profile、manifest 路径、artifacts 路径、model 路径、layout mode、AOI bbox | 检查 manifest、latents、FAISS index、OSM cache 是否存在，同时统计 asset role/theme tag | `WorkspaceReadiness` |
| manifest 准备 | `prepare_manifest_assets(...)` | `mock` 或 `real` profile、源 manifest、输出目录 | `mock` 模式写 `assets.jsonl`；`real` 模式把真实 manifest 规范化为 pipeline 可读格式 | `StepResult`，以及 `assets.jsonl` / `real_assets_for_pipeline.jsonl` |
| latent 准备 | `prepare_latents_if_needed(...)` | 真实 manifest、mesh root、latents dir、Shape-E 配置、encode mode | 判断是否跳过；否则调用真实 latent 编码流程 | `StepResult`，以及 `.pt` latent 文件 |
| index 准备 | `prepare_index_if_needed(...)` | manifest / assets、CLIP 模型、输出路径 | 调用资产嵌入和 FAISS 构建 | `StepResult`，以及 `index_ip.faiss`、`id_map.json` |
| OSM cache 预热 | `prepare_osm_cache_if_needed(...)` | `layout_mode=osm`、AOI bbox、cache dir | 拉取并缓存 Overpass JSON | `StepResult`，以及 `overpass_<hash>.json` |
| 候选道路发现 | `discover_poi_roads_if_needed(...)` | AOI bbox、OSM cache | 对 AOI 内道路做 POI-rich 筛选，缓存发现结果 | `StepResult`，以及 `discovered_poi_roads.jsonl` |
| 总编排节点 | `prepare_workspace(...)` | 上述所有输入 | 依次执行检查、manifest、latent、index、OSM cache、road discovery，并汇总 summary | `PrepareWorkspaceResult` |

### 4.2 资产库与检索解码模块

这一部分既包含真实资产库构建，也包含单资产闭环 `text -> 3D asset`。

#### 4.2.1 资产库构建节点

| 节点 | 代码入口 | 输入 | 处理 | 输出 |
| --- | --- | --- | --- | --- |
| 模拟资产播种 | [`scripts/m1_01_seed_assets.py`](../scripts/m1_01_seed_assets.py) `seed_assets(...)` | 资产数量、seed、latent 维度 | 生成 mock `assets.jsonl` 和随机 latent | `data/m1/assets.jsonl`、`latents/*.pt` |
| 真实资产清洗 | [`scripts/m2_10_ingest_assets.py`](../scripts/m2_10_ingest_assets.py) `ingest_assets(...)` | 原始 manifest、mesh 输出目录 | 校验字段、可选 mesh 归一化、重写 `mesh_path/latent_path` | 标准化后的真实 manifest、归一化 mesh |
| 真实 latent 编码 | [`scripts/m2_11_encode_shapee_latents.py`](../scripts/m2_11_encode_shapee_latents.py) `encode_latents(...)` | manifest、latents dir、Shape-E 模型目录、encode mode | 支持 `shapee`、`mesh_ref`、`auto`；Shape-E 失败时可回退为 mesh reference 或 placeholder latent | 更新后的 manifest、latent 文件、编码统计 |
| 资产文本嵌入 | [`scripts/m1_02_embed_texts.py`](../scripts/m1_02_embed_texts.py) `run(...)` | `assets.jsonl`、CLIP 模型参数 | 用 CLIP 编码 description，输出 embedding 和元信息 | `asset_text_embeds.npy`、`asset_ids.json`、`embed_meta.json` |
| 真实资产索引构建 | [`scripts/m2_12_build_real_index.py`](../scripts/m2_12_build_real_index.py) `main()` | 真实 manifest、CLIP 模型、artifacts 路径 | 读取 `text_desc`，计算 embedding，构建 FAISS，并额外导出 pipeline 用资产表 | `index_ip.faiss`、`id_map.json`、`real_assets_for_pipeline.jsonl` |
| FAISS 存储层 | [`src/roadgen3d/index_store.py`](../src/roadgen3d/index_store.py) `FaissIndexStore` | embedding 矩阵、asset ids 或现有 index 文件 | 构建 / 保存 / 加载 / 搜索 `IndexFlatIP` | `RetrievalHit` 列表、索引文件 |

#### 4.2.2 单资产检索解码节点

| 节点 | 代码入口 | 输入 | 处理 | 输出 |
| --- | --- | --- | --- | --- |
| 文本编码 | [`src/roadgen3d/embedder.py`](../src/roadgen3d/embedder.py) `ClipTextEmbedder.encode_texts(...)` | query 或 description 文本 | CLIP `get_text_features` + L2 归一化 | `float32` embedding |
| latent 读取 | [`src/roadgen3d/latent_store.py`](../src/roadgen3d/latent_store.py) `LatentStore.load(...)` | `asset_id` | 解析 manifest、定位 latent；支持 tensor latent 或 `{mesh_path}` reference latent | latent tensor 或 mesh reference |
| 占位解码器 | [`src/roadgen3d/decoder.py`](../src/roadgen3d/decoder.py) `PlaceholderVoxelDecoder.decode(...)` | latent tensor | 把 latent 摊平后映射为体素概率场，并二值化 | `voxel_prob`、`voxel_bin`、decoder meta |
| Shape-E 适配器 | [`src/roadgen3d/decoder_shapee.py`](../src/roadgen3d/decoder_shapee.py) `ShapeEDecoder.decode(...)` | Shape-E latent、mesh reference 或 mesh path | 优先解到 mesh，再体素化；失败时可回退到 placeholder | `voxel_prob`、`voxel_bin`、mesh / fallback meta |
| 体素导出 | [`src/roadgen3d/voxel_export.py`](../src/roadgen3d/voxel_export.py) `export_voxel_meshes(...)` | 二值体素、导出配置 | marching cubes 或 cubes fallback 导出 GLB/PLY | `mesh_glb`、`mesh_ply` |
| 单资产闭环编排 | [`src/roadgen3d/pipeline.py`](../src/roadgen3d/pipeline.py) `M1Pipeline.run(...)` | query、topk、embedder、index、latent store、decoder | 依次做 query embedding、FAISS 检索、top hit latent 读取、解码、导出 mesh | `PipelineResult`、retrieval hits、`pipeline_result.json` |

### 4.3 参数化资产生成模块

当前参数化资产模块只真正覆盖 `bench` 和 `lamp`。

| 节点 | 代码入口 | 输入 | 处理 | 输出 |
| --- | --- | --- | --- | --- |
| 请求标准化 | [`src/roadgen3d/parametric_assets.py`](../src/roadgen3d/parametric_assets.py) `_to_request(...)` / `_validate_request(...)` | `GenerationRequest` 或 JSON payload | 统一 asset kind、runtime profile、device backend、quality profile | 规范化后的 `GenerationRequest` |
| Bench 参数校验 | `_validate_bench_params(...)` | `params` 字典 | 对尺寸、靠背角、slat 数、style/material 做 `clamp / reject / warn` | `BenchParams` |
| Lamp 参数校验 | `_validate_lamp_params(...)` | `params` 字典 | 对杆高、半径、底座、灯臂、光型做 `clamp / reject / warn` | `LampParams` |
| Bench 几何组装 | `_build_bench_mesh(...)` | `BenchParams`、detail level | 程序化拼装 slat、rail、backrest、legs/armrest，并做 ground 落地 | mesh + `_BenchAudit` |
| Lamp 几何组装 | `_build_lamp_mesh(...)` | `LampParams`、detail level | 程序化拼装 pole、base、collar、arm、luminaire，并计算净空/细长比 | mesh + `_LampAudit` |
| 质量评估 | `_bench_quality_metrics(...)` / `_lamp_quality_metrics(...)` / `_quality_gate(...)` | mesh、params、audit、runtime profile | 检查面数、poly budget、尺寸误差、ground contact、稳定性/净空 | `GenerationQualityMetrics` |
| 单资产生成编排 | `generate_parametric_asset(...)` | 请求对象 | 请求校验 -> 参数校验 -> 组装 -> 质量门槛 -> 返回元信息 | `ParametricAssetResult` |
| 单资产导出登记 | [`scripts/m3_03_generate_parametric_asset.py`](../scripts/m3_03_generate_parametric_asset.py) `main()` | request JSON、out dir、可选 manifest out | 导出 GLB、写 placeholder latent、写 `.result.json`、可选追加 manifest 行 | 生成资产文件、manifest 行 |
| 批量资产库生成 | [`scripts/m3_02_generate_procedural_assets.py`](../scripts/m3_02_generate_procedural_assets.py) `generate_all(...)` | `AssetSpec` 列表、mesh out dir、manifest out、backend mode | 对 Bench/Lamp 优先走 parametric；其他类别走 legacy generator；检查 face budget 后写真实资产库 | `data/real/real_assets_manifest.jsonl`、`data/real/meshes/*.glb` |

### 4.4 OSM/POI 空间理解模块

这一模块是当前 OSM 模式的关键前置层。

| 节点 | 代码入口 | 输入 | 处理 | 输出 |
| --- | --- | --- | --- | --- |
| OSM 拉取缓存 | [`src/roadgen3d/osm_ingest.py`](../src/roadgen3d/osm_ingest.py) `fetch_osm_data(...)` | AOI bbox、cache dir | 构造 Overpass QL，网络获取并缓存 JSON | 原始 Overpass JSON |
| OSM 解析 | `parse_osm_features(...)` | 原始 Overpass JSON | 解析 roads、buildings、entrances、bus_stops、hydrants 以及规范化 `poi_points_by_type` | `OsmFeatures` |
| 坐标投影 | `project_to_local(...)` | `OsmFeatures`、bbox | 自动推断 UTM EPSG，把 WGS84 转为以 AOI 中心为原点的局部米制坐标 | `ProjectedFeatures` |
| POI-rich 道路发现 | [`src/roadgen3d/road_discovery.py`](../src/roadgen3d/road_discovery.py) `discover_poi_roads(...)` | 城市 bbox、OSM cache、阈值 | 统计道路 buffer 内 POI，筛选满足长度/POI score/core POI 条件的道路 | `DiscoveredRoad` 列表、JSONL |
| 道路选择 | [`src/roadgen3d/placement_zones.py`](../src/roadgen3d/placement_zones.py) `apply_road_selection(...)` | `ProjectedFeatures`、road selection 配置 | 选择 primary/longest/all 或指定 OSM id 的道路 | 过滤后的 `ProjectedFeatures` |
| POI 驱动横断面合成 | [`src/roadgen3d/cross_section_synthesis.py`](../src/roadgen3d/cross_section_synthesis.py) `synthesize_poi_driven_cross_section(...)` | roads、POI points、road width、lane count、sidewalk seed width、profile defaults | 根据 POI 相对道路左右位置和类别，重新分配左右 clear path / furnishing 宽度，并检查 POI containment | `PoiDrivenCrossSection` |
| 几何上下文构建 | [`src/roadgen3d/placement_zones.py`](../src/roadgen3d/placement_zones.py) `build_placement_context(...)` | `ProjectedFeatures`、`StreetComposeConfig` | 构建 carriageway、多侧 sidewalk zone、过滤保留 corridor 内 POI，并记录横断面指标 | `PlacementContext` |
| 轻量 POI 上下文 | [`src/roadgen3d/poi_rules.py`](../src/roadgen3d/poi_rules.py) `build_poi_context(...)` | `PlacementContext` | 把 shapely/placement 数据整理成规则打分用的纯 tuple 点集 | `PoiContext` |
| 路段图构建 | [`src/roadgen3d/osm_segment_graph.py`](../src/roadgen3d/osm_segment_graph.py) `build_segment_graph(...)` | `ProjectedFeatures`、segment length、POI 信息 | 将道路切成 segment node，给每段附带左右 band 和附近 POI 类型 | `RoadSegmentGraph` |
| 空间距离上下文 | [`src/roadgen3d/spatial_features.py`](../src/roadgen3d/spatial_features.py) `build_spatial_context(...)` | config、road graph、poi context | 抽取 junction、entrance、bus stop、hydrant 作为空间特征源 | `SpatialContext` |
| 主题分段与建筑上下文 | [`src/roadgen3d/theme_buildings.py`](../src/roadgen3d/theme_buildings.py) `infer_theme_segments(...)` / `collect_building_footprints(...)` | query、target street type、road graph、projected buildings | 给道路切主题段，并收集周边建筑 footprint 或 fallback lot | `ThemeSegment`、`BuildingFootprint` |

### 4.5 街道程序与约束求解模块

这是当前“神经符号街道生成”的中心层。

| 节点 | 代码入口 | 输入 | 处理 | 输出 |
| --- | --- | --- | --- | --- |
| Heuristic StreetProgram 生成 | [`src/roadgen3d/street_program.py`](../src/roadgen3d/street_program.py) `infer_street_program(...)` | `StreetComposeConfig`、available categories、POI/placement context | 推断 road type、cross section bands、家具需求、design goals、control points、observed POI counts、reserved bands | `StreetProgram` |
| Learned program runtime | [`src/roadgen3d/program_generator.py`](../src/roadgen3d/program_generator.py) `ProgramGeneratorRuntime.generate(...)` | `ProgramGenerationInput` | 先生成 heuristic base program，再用 learned 模型预测 road_type / cross_section / lane / band width / counts / goals；必要时回退 | `ProgramGenerationResult` |
| 规则配置加载 | [`src/roadgen3d/design_rules.py`](../src/roadgen3d/design_rules.py) `load_constraint_set(...)` | `design_rule_profile` 名称 | 载入声明式规则集合，例如 lane 上限、clear path 下限、reserved transit edge、required category 等 | `ConstraintSet` |
| 程序编译 | [`src/roadgen3d/layout_solver.py`](../src/roadgen3d/layout_solver.py) `_compile_program(...)` | `LayoutSolverInput` | 按规则修改 `StreetProgram`，包括 lane count、band width、category min count、category substitution | 修正后的 program、edits、conflicts |
| 槽位规划 | `_build_slot_plans(...)` | resolved program、POI anchors、available categories | 为每个 category 生成 `LayoutSlotPlan`；有 POI 的类别优先生成 anchored slots，其余按带状/长度均匀铺开 | `slot_plans` |
| Banded 求解器 | `solve_layout(...)` | `LayoutSolverInput` | 走规则编译 + banded slot planning，再做 rule evaluation | `LayoutSolverResult` |
| MILP 模板求解器 | [`src/roadgen3d/milp_solver.py`](../src/roadgen3d/milp_solver.py) `solve_candidate_assignment(...)` | resolved program、segment graph、requirements、required categories | 把候选 band/segment 当作离散 candidate，做 greedy / PuLP 选择 | `slot_plans` + MILP conflict |
| 求解器运行时分发 | `LayoutSolverRuntime.solve(...)` | `LayoutSolverInput`、backend 名称 | 在 `banded` 与 `milp_template_v1` 间切换；不支持 anchored slot 或 MILP 无解时可回退 | `LayoutSolverResult` |

### 4.6 场景实现与导出模块

这部分主要集中在 [`src/roadgen3d/street_layout.py`](../src/roadgen3d/street_layout.py)，是当前最大的业务编排模块。

| 节点 | 代码入口 | 输入 | 处理 | 输出 |
| --- | --- | --- | --- | --- |
| 场景总编排 | `compose_street_scene(...)` | `StreetComposeConfig`、manifest、artifacts、模型参数、policy/program ckpt | 串起 OSM、program、solver、资产检索、位置搜索、建筑补全、导出、评估 | `StreetComposeResult` |
| 资产候选检索 | `_pick_category_candidate(...)` | slot query、category、topk、embedder、index、policy runtime、feature context | 对某个 category 检索候选资产，可用 rule 或 learned policy 做候选排序与选择 | 选中资产行、score、source、候选细节 |
| 候选位置搜索 | `_iter_slot_candidate_groups(...)` | slot、band、segment、theme、placement context | 生成 exact / ring / segment / theme-side 等多层候选位置组 | 候选位姿组 |
| 候选打分与约束过滤 | `_evaluate_slot_candidate(...)` + [`src/roadgen3d/placement_field.py`](../src/roadgen3d/placement_field.py) `compose_candidate_energy(...)` + [`src/roadgen3d/poi_rules.py`](../src/roadgen3d/poi_rules.py) `score_placement(...)` | 候选位姿、已有 placements、POI 规则、空间 hash、theme/zone 信息 | 检查 overlap、zone containment、theme range、side match，并综合 pair interaction、POI attraction、constraint penalty 得到 placement energy | 可落地候选或阻塞原因 |
| 周边建筑规划 | `_place_surrounding_buildings(...)` + [`src/roadgen3d/theme_buildings.py`](../src/roadgen3d/theme_buildings.py) | projected buildings、theme segments、road graph、asset inventory | 收集 OSM footprint 或 grid-growth lot，检索 building 类资产，生成周边建筑 placement | building placements、building summary、zoning grid |
| 场景底图构建 | `_build_base_scene(...)` / `_build_osm_base_scene(...)` | resolved program 或 placement context、style palette | 生成 template 或 OSM 版本的地面、车行道、人行道、功能带代理体 | base scene |
| 实例写入与导出 | `_add_instance_meshes(...)` / `_export_scene(...)` | `StreetPlacement` 列表、mesh cache、导出格式 | 把 street furniture 和 building mesh 写入场景并导出 | `scene.glb`、`scene.ply` |
| 分步生产可视化 | `_build_production_steps(...)` | placements、zoning grid、POI、空间上下文 | 生成分阶段 GLB 和 companion figure，记录每一步显隐对象 | `production_steps/*.glb`、`production_steps.json` |
| 场景分析与摘要 | `evaluate_all_entrances(...)`、`compute_presentation_report(...)`、`build_scene_graph(...)`、`render_presentation_views(...)` | 最终 placements、solver result、POI/空间上下文 | 计算 entrance openness / shielding、presentation score、scene graph，并生成渲染视图 | `scene_layout.json` 中的 `summary`/`scene_graph`，以及 presentation 图片 |

### 4.7 评估与可视化模块

| 节点 | 代码入口 | 输入 | 处理 | 输出 |
| --- | --- | --- | --- | --- |
| 工程指标计算 | [`src/roadgen3d/eval_metrics.py`](../src/roadgen3d/eval_metrics.py) | placements、summary rows、solver evaluations | 计算 overlap、dropped slot rate、spacing uniformity、style consistency、balance、rule satisfaction 等 | scene-level 数值指标 |
| 合规评估 | [`src/roadgen3d/compliance_eval.py`](../src/roadgen3d/compliance_eval.py) `compute_compliance(...)` / `evaluate_compliance_batch(...)` | `scene_layout.json` 中 placements | 汇总每个 placement 的 `violated_rules`、constraint penalty、feasibility | compliance report、per-scene rows |
| 合规报告写出 | `write_compliance_report(...)` | compliance report、per-scene rows、out dir | 写 JSON + CSV | `compliance_report.json`、`compliance_per_scene.csv` |
| Scene graph 构建 | [`src/roadgen3d/scene_graph_viz.py`](../src/roadgen3d/scene_graph_viz.py) `build_scene_graph(...)` | layout payload、road graph、POI context | 把 road segment、POI、slot plan、placement 转成节点边关系 | `scene_graph` payload |
| 空间可视化 | [`src/roadgen3d/spatial_viz.py`](../src/roadgen3d/spatial_viz.py) `plot_scene_with_markers(...)` / `plot_zoning_grid_preview(...)` | `SpatialContext`、placements、OSM geometry、zoning grid | 生成平面图、热力图、zoning preview | matplotlib figure / PNG |
| 工程评估回放 | [`scripts/m4_10_eval_engineering.py`](../scripts/m4_10_eval_engineering.py) | query 集、policy/program 配置、compose 配置 | 批量调用 `compose_street_scene`，汇总 rule 与 learned 模式指标差异 | `summary.json`、`per_scene.csv`、delta report |

### 4.8 研究训练模块

| 节点 | 代码入口 | 输入 | 处理 | 输出 |
| --- | --- | --- | --- | --- |
| Policy 样本蒸馏 | [`scripts/m4_01_collect_policy_data.py`](../scripts/m4_01_collect_policy_data.py) `collect_policy_data(...)` | query 集、seed 范围、manifest、index、compose 参数 | 在 template 模式下枚举 slot，记录 candidate 列表、chosen asset、上下文和距离特征 | `policy_train.jsonl` |
| Policy 特征构建 | [`src/roadgen3d/layout_features.py`](../src/roadgen3d/layout_features.py) `vectorize_slot_candidates(...)` | `PolicyFeatureContext`、candidate descriptors | 构造 35 维候选特征，包含几何、上下文、空间距离和类别 one-hot | `[N, 35]` feature matrix |
| Policy 训练 | [`src/roadgen3d/layout_policy.py`](../src/roadgen3d/layout_policy.py) `train_layout_policy(...)` | train/val samples、训练超参数 | MLP 候选打分训练，支持 entropy regularization 和 reward weighting | policy ckpt、meta、curve |
| Program 样本蒸馏 | [`scripts/m6_01_collect_program_data.py`](../scripts/m6_01_collect_program_data.py) `collect_program_data(...)` | query 集、constraint profiles、layout modes、bbox 集、几何变体 | 生成 `ProgramGenerationInput` 特征和 `StreetProgram` target，保存 program 监督样本 | `program_train.jsonl` |
| Program 特征向量化 | [`src/roadgen3d/program_generator.py`](../src/roadgen3d/program_generator.py) `vectorize_program_input(...)` / `program_to_targets(...)` | compose config、inventory、road graph、POI/spatial context、resolved program | 构造 54 维输入，和 road type / cross section / lane / band widths / counts / goals 的监督目标 | feature 向量 + target dict |
| Program 训练 | `train_program_generator(...)` | train/val samples、训练超参数 | 训练多头 MLP 预测结构化 `StreetProgram` | program ckpt、meta、curve |

## 5. 当前两条主生成链路

### 5.1 Template 模式主链路

```text
StreetComposeConfig
  -> inventory summary
  -> heuristic/learned StreetProgram
  -> ConstraintSet + LayoutSolver
  -> slot plans
  -> category retrieval + pose search
  -> placement + export
  -> summary / presentation / scene graph
```

特点：

- 不依赖 OSM。
- 道路几何是规则化直路模板。
- 仍然可以使用 learned policy 和 learned program generator。

### 5.2 OSM 模式主链路

```text
AOI bbox
  -> fetch_osm_data
  -> parse_osm_features
  -> project_to_local
  -> road selection
  -> POI-driven cross section
  -> PlacementContext + PoiContext + RoadSegmentGraph + ThemeSegments
  -> StreetProgram
  -> ConstraintSet + LayoutSolver
  -> anchored slot plans
  -> asset retrieval + placement scoring
  -> buildings + export + analysis
```

特点：

- 真实依赖 POI 和道路几何。
- `StreetProgram`、solver、placement 都会消费 POI 信息。
- 当前系统对 OSM 模式的表达能力明显强于 template 模式。

## 6. 当前设计的几个实现特征

1. 系统已经不是单纯的 “text -> 3D asset” demo，而是一个“准备 + 生成 + 研究”的工作台。
2. 当前街道生成的核心中间表示是 `StreetProgram`，不是直接从 query 跳到 placement。
3. 当前约束层分成两层：
   - 声明式设计规则：`ConstraintSet`
   - 几何/POI 软约束评分：`PoiRuleSet` + `placement_field`
4. 当前 OSM 模式会显式构造 `PlacementContext`、`RoadSegmentGraph`、`ThemeSegment`，这说明系统已从单一路段模板演进到“带空间上下文的离散求解”。
5. 当前资产实现层已经是混合式：
   - 检索资产库中的真实/已有资产
   - 对 Bench/Lamp 优先用参数化生成资产
6. 当前研究模块是围绕两个 learned 组件展开：
   - slot-level layout policy
   - structured StreetProgram generator

## 7. 结论

如果用一句话概括当前项目设计，RoadGen3D 现在更像是：

> 一个以 `OSM + POI + StreetProgram + ConstraintSet + LayoutSolver + asset realization` 为主干，同时带有 `workspace preparation` 与 `research/training` 能力的街道生成工作台。

从代码结构上看，当前最核心的三个中枢模块是：

1. [`src/roadgen3d/placement_zones.py`](../src/roadgen3d/placement_zones.py) / [`src/roadgen3d/osm_ingest.py`](../src/roadgen3d/osm_ingest.py)：负责把真实空间上下文准备好。
2. [`src/roadgen3d/street_program.py`](../src/roadgen3d/street_program.py) / [`src/roadgen3d/layout_solver.py`](../src/roadgen3d/layout_solver.py)：负责把意图编译成可执行布局。
3. [`src/roadgen3d/street_layout.py`](../src/roadgen3d/street_layout.py)：负责把布局真正实现成资产摆放、建筑补全、导出和分析结果。

