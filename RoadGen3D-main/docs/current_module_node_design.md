# RoadGen3D 当前模块、节点与输入/处理/输出设计

更新时间：2026-03-17

本文按“当前仓库已经实现的代码”梳理项目设计，不按长期规划或论文式理想架构描述。

说明：

- 这里的“模块”是当前系统里稳定存在的职责边界。
- 这里的“节点”是当前实现中的公共入口、主流程阶段或关键运行时节点，不展开所有私有 helper。
- 输入 / 处理 / 输出优先描述真实代码中的数据对象、文件产物和运行副作用。

## 1. 当前系统主干

当前项目的主干已经不是单一的 `text -> 3D asset`，而是：

```text
Workspace Preparation
  -> Asset Library Ready
  -> OSM / POI Context Ready
  -> StreetProgram
  -> ConstraintSet / LayoutSolver
  -> Asset Realization / Placement
  -> Buildings / Export / Evaluation
  -> Research Training / Evaluation
```

主入口分成两类：

- 单资产闭环：`src/roadgen3d/pipeline.py`
- 街道级生成主链路：`src/roadgen3d/street_layout.py`

配套编排主要在：

- `scripts/m1_gradio_app.py`
- `scripts/m3_01_compose_street.py`
- `scripts/m4_*`
- `scripts/m5_*`
- `scripts/m6_*`

## 2. 模块总表

| 模块 | 主要职责 | 代表入口 | 主要输出 |
| --- | --- | --- | --- |
| 1. Workspace 准备 | 检查并补齐 manifest、latent、index、OSM cache、候选道路 | `prepare_workspace(...)` | `PrepareWorkspaceResult`、缓存文件 |
| 2. 资产库与单资产检索解码 | 完成 `text -> retrieval -> latent -> voxel -> mesh` | `M1Pipeline.run(...)` | `PipelineResult`、GLB/PLY、npy |
| 3. 参数化资产生成与入库 | 生成 bench/lamp/tree 等参数化资产并写入资产库 | `preview_parametric_asset(...)`、`scripts/m3_02_generate_procedural_assets.py` | mesh、质量指标、manifest 行 |
| 4. OSM / POI 空间理解 | 拉取 OSM、解析道路/建筑/POI、投影、本地化、选路和横断面推断 | `fetch_osm_data(...)`、`build_segment_graph(...)` | `PlacementContext`、`PoiContext`、`RoadSegmentGraph` |
| 5. StreetProgram 与设计规则 | 从 query + 空间上下文推断显式街道程序，并加载规则集 | `infer_street_program(...)`、`load_constraint_set(...)` | `StreetProgram`、`ConstraintSet` |
| 6. 布局求解 | 把程序和规则编译成可执行槽位计划 | `LayoutSolverRuntime.solve(...)` | `LayoutSolverResult` |
| 7. 场景实现与导出 | 检索资产、搜索位姿、约束评分、摆放实例、补全建筑、导出场景 | `compose_street_scene(...)` | `StreetComposeResult`、`scene.glb`、`scene_layout.json` |
| 8. 评估与可视化 | 工程指标、合规报告、空间图、scene graph、演示视图 | `compute_compliance(...)`、`build_scene_graph(...)` | JSON/CSV、图像、scene graph |
| 9. 研究训练 | 收集 policy / program 数据并训练 learned runtime | `collect_policy_data(...)`、`collect_program_data(...)` | `jsonl`、ckpt、训练曲线 |

## 3. 核心中间对象

| 对象 | 作用 | 主要生产者 | 主要消费者 |
| --- | --- | --- | --- |
| `WorkspaceReadiness` | 工作区是否可运行、还缺什么 | `inspect_workspace_readiness(...)` | UI 准备页、`prepare_workspace(...)` |
| `PrepareWorkspaceResult` | 准备流程的步骤汇总和结果摘要 | `prepare_workspace(...)` | UI 和准备日志 |
| `AssetRecord` / `RetrievalHit` | 资产库记录与检索命中 | ingest / embedding / FAISS | `M1Pipeline`、街道资产检索 |
| `PipelineResult` | 单资产闭环结果 | `M1Pipeline.run(...)` | CLI/UI |
| `StreetComposeConfig` | 一次街道生成的总配置 | CLI / UI 输入 | 几乎所有街道生成节点 |
| `InventorySummary` | 当前资产库按类别汇总 | `compose_street_scene(...)`、训练脚本 | Program / Solver / building planning |
| `StreetBand` | 横断面中的功能带 | `infer_street_program(...)` | solver、placement、导出 |
| `StreetProgram` | query 与上下文编译后的显式街道程序 | `infer_street_program(...)`、`ProgramGeneratorRuntime.generate(...)` | solver、导出、评估 |
| `ConstraintSet` | 规则配置文件对应的设计约束集合 | `load_constraint_set(...)` | solver |
| `LayoutSolverResult` | 求解后得到的槽位、编辑、冲突、规则评估 | `LayoutSolverRuntime.solve(...)` | 场景实现、评估、导出 |
| `PoiContext` | 轻量 POI 点集上下文 | `build_poi_context(...)` | POI 规则、scene graph、分析 |
| `RoadSegmentGraph` | OSM 道路段图和离散节点 | `build_segment_graph(...)` | theme、solver、OSM placement |
| `StreetPlacement` | 一个已落地实例 | `compose_street_scene(...)` | 导出、评估、scene graph |
| `StreetComposeResult` | 一次完整街道生成的顶层结果 | `compose_street_scene(...)` | CLI/UI/研究评估 |

## 4. 各模块与节点

### 4.1 Workspace 准备模块

主要编排位置：

- `scripts/m1_gradio_app.py`

| 节点 | 代码入口 | 输入 | 处理 | 输出 |
| --- | --- | --- | --- | --- |
| 就绪检查 | `inspect_workspace_readiness(...)` | dataset profile、manifest 路径、artifacts 路径、model 路径、layout mode、AOI bbox | 检查 manifest、latent、FAISS index、OSM cache、候选道路缓存是否存在，并统计库内类别/主题标签 | `WorkspaceReadiness` |
| manifest 准备 | `prepare_manifest_assets(...)` | `mock/real` profile、源 manifest、输出目录 | 生成或规范化 `assets.jsonl`，把真实资产库转成 pipeline 可读格式 | `StepResult`、`assets.jsonl`、`real_assets_for_pipeline.jsonl` |
| latent 准备 | `prepare_latents_if_needed(...)` | 真实 manifest、mesh root、latents dir、Shape-E 配置、encode mode | 判断是否跳过，否则批量生成或补齐 latent | `StepResult`、`.pt` latent 文件 |
| 向量索引准备 | `prepare_index_if_needed(...)` | assets/manifest、CLIP 模型、输出路径 | 运行 embedding 与 FAISS 建索引 | `StepResult`、`index_ip.faiss`、`id_map.json` |
| OSM cache 预热 | `prepare_osm_cache_if_needed(...)` | `layout_mode=osm`、AOI bbox、cache dir | 拉取并缓存 Overpass JSON | `StepResult`、`overpass_<hash>.json` |
| 候选道路发现 | `discover_poi_roads_if_needed(...)` | AOI bbox、OSM cache | 根据道路长度、POI 密度、核心 POI 数对候选道路打分并缓存 | `StepResult`、`discovered_poi_roads.jsonl` |
| 总编排 | `prepare_workspace(...)` | 上述所有输入 | 顺序执行检查、manifest、latent、index、OSM、road discovery，汇总状态 | `PrepareWorkspaceResult` |

### 4.2 资产库与单资产检索解码模块

主要代码位置：

- `src/roadgen3d/pipeline.py`
- `src/roadgen3d/embedder.py`
- `src/roadgen3d/index_store.py`
- `src/roadgen3d/latent_store.py`
- `src/roadgen3d/decoder.py`
- `src/roadgen3d/decoder_shapee.py`
- `src/roadgen3d/voxel_export.py`

#### 4.2.1 资产库构建节点

| 节点 | 代码入口 | 输入 | 处理 | 输出 |
| --- | --- | --- | --- | --- |
| 模拟资产播种 | `scripts/m1_01_seed_assets.py` `seed_assets(...)` | asset count、seed、latent 维度 | 生成 mock manifest 和随机 latent | `data/m1/assets.jsonl`、`latents/*.pt` |
| 文本嵌入 | `scripts/m1_02_embed_texts.py` | `assets.jsonl`、CLIP 模型 | 编码每条 description 并归一化 | embedding 数组 |
| FAISS 索引构建 | `scripts/m1_03_build_faiss.py` | embedding、asset_id 映射 | 构建 `IndexFlatIP` 与 id map | `index_ip.faiss`、`id_map.json` |
| 真实资产清洗 | `scripts/m2_10_ingest_assets.py` `ingest_assets(...)` | 原始 manifest、mesh 输出目录 | 校验字段、清洗路径、可选 mesh 归一化 | 标准化 manifest、mesh 引用 |
| 真实 latent 编码 | `scripts/m2_11_encode_shapee_latents.py` | manifest、mesh/mesh_ref、Shape-E 配置 | 逐条编码真实 mesh，为真实资产库补齐 latent | manifest 更新、`latents/*.pt` |
| 真实库建索引 | `scripts/m2_12_build_real_index.py` | 真实 manifest、CLIP 模型 | 编码 `text_desc` 并构建 FAISS | 实体资产检索索引 |

#### 4.2.2 单资产运行时节点

| 节点 | 代码入口 | 输入 | 处理 | 输出 |
| --- | --- | --- | --- | --- |
| 文本编码 | `embedder.encode_texts(...)` | query 文本 | CLIP 编码 + 归一化 | query embedding |
| 向量检索 | `index_store.search(...)` | query embedding、`topk` | FAISS 内积检索 | `RetrievalHit[]` |
| latent 读取 | `latent_store.load(...)` | `asset_id` | 载入 latent tensor / array | latent |
| latent 解码 | `decoder.decode(...)` | latent | `placeholder` 或 `shapee` 解码到 voxel | `voxel_prob`、`voxel_bin`、`meta` |
| 体素落盘 | `M1Pipeline.run(...)` | voxel | 保存概率体素与二值体素 | `voxel_prob.npy`、`voxel_bin.npy` |
| 网格导出 | `export_voxel_meshes(...)` | `voxel_bin`、导出配置 | marching cubes 或 cubes 导出网格 | `*_voxel.glb`、`*_voxel.ply` |
| 结果汇总 | `PipelineResult` + `save_result_json(...)` | query、top hit、voxel/mesh 元数据 | 组装单资产闭环结果 | `pipeline_result.json` |

### 4.3 参数化资产生成与入库模块

主要代码位置：

- `src/roadgen3d/parametric_assets.py`
- `scripts/m3_02_generate_procedural_assets.py`
- `scripts/m3_03_generate_parametric_asset.py`
- `scripts/m3_05_seed_production_parametric_assets.py`

| 节点 | 代码入口 | 输入 | 处理 | 输出 |
| --- | --- | --- | --- | --- |
| 参数化预览 | `preview_parametric_asset(...)` | asset kind、参数、质量档位、导出目录 | 调用程序化几何生成器构造 mesh，并做预览级质量检查 | 预览 mesh、元数据 |
| manifest 追加 | `append_parametric_asset_to_manifest(...)` | 预览结果、asset id、text desc、manifest 路径 | 把参数化资产转成标准 manifest 行 | manifest 新行 |
| 单个资产生成 | `scripts/m3_03_generate_parametric_asset.py` | asset kind、参数快照 | 生成正式 mesh 并导出 | 单个参数化资产文件 |
| 生产级批量生成 | `scripts/m3_02_generate_procedural_assets.py` | 类别、数量、seed、poly budget | 迭代参数采样、规则校验、重试、质量验收 | 生产资产、质量记录 |
| 生产库播种 | `scripts/m3_05_seed_production_parametric_assets.py` | 批量配置、目标 manifest | 批量生成并写入真实资产库 | 生产级参数化资产集 |

当前这部分的真实目标是：

- `Bench`
- `Lamp`
- 部分 tree / 占位 building 支持

输出会被后续街道场景实现模块直接消费。

### 4.4 OSM / POI 空间理解模块

主要代码位置：

- `src/roadgen3d/osm_ingest.py`
- `src/roadgen3d/road_discovery.py`
- `src/roadgen3d/cross_section_synthesis.py`
- `src/roadgen3d/osm_segment_graph.py`
- `src/roadgen3d/placement_zones.py`
- `src/roadgen3d/spatial_features.py`

| 节点 | 代码入口 | 输入 | 处理 | 输出 |
| --- | --- | --- | --- | --- |
| OSM 拉取 | `fetch_osm_data(...)` | AOI bbox、cache dir、Overpass endpoint | 构建 Overpass 查询、下载或命中缓存 | 原始 OSM JSON |
| OSM 解析 | `parse_osm_features(...)` | 原始 OSM JSON | 解析道路、建筑、POI、节点关系 | `OsmFeatures` |
| 本地投影 | `project_to_local(...)` | `OsmFeatures`、AOI 中心 | 选择 UTM EPSG、投影到本地米制坐标 | `ProjectedFeatures` |
| 候选道路发现 | `discover_poi_roads(...)` | `ProjectedFeatures`、POI 计数规则 | 统计 buffer 范围内 POI 数、长度、核心 POI 分数并排序 | `DiscoveredRoad[]` |
| POI 驱动横断面 | `synthesize_poi_driven_cross_section(...)` | 道路几何、附近 POI、基础宽度配置 | 依据 transit / entrance / parking 等 POI 调整左右侧功能带宽度 | `PoiDrivenCrossSection` |
| 道路段图构建 | `build_segment_graph(...)` | projected roads、POI 上下文、program bands | 将道路离散成 segment / node 图，并附加 band / 最近 POI 信息 | `RoadSegmentGraph` |
| 摆放上下文构建 | `build_placement_context(...)` | 投影后的道路、建筑、POI、配置 | 生成 road polygon、sidewalk polygon、有效摆放区和 AOI 范围 | `PlacementContext` |
| POI 上下文整理 | `build_poi_context(...)` | `PlacementContext` | 抽取标准化 POI 点集 | `PoiContext` |

这部分模块的核心输出，是供后续 `StreetProgram`、solver 和 placement 同时消费的空间中间表示。

### 4.5 StreetProgram 与设计规则模块

主要代码位置：

- `src/roadgen3d/street_program.py`
- `src/roadgen3d/design_rules.py`
- `src/roadgen3d/program_generator.py`

| 节点 | 代码入口 | 输入 | 处理 | 输出 |
| --- | --- | --- | --- | --- |
| profile 默认值加载 | `profile_defaults(...)` | `design_rule_profile` | 返回 profile 对应的横断面、宽度、密度缩放默认值 | profile defaults |
| 道路类型推断 | `_infer_road_type(...)` | query、fallback street type | 从 query 关键词映射 `mixed_use / transit_corridor / boulevard...` | `road_type` |
| 设计目标合并 | `_merge_goals(...)` | query、profile goals | 根据关键词补充 `walkability / transit_access / greening ...` | `design_goals` |
| POI 绑定与需求修正 | `_apply_observed_poi_bindings(...)` | POI counts、POI context、bands、profile | 依据 POI 数修正 furniture requirements，保留 bus stop band 等保留约束 | `reserved_band_categories`、修正后的 goals / requirements |
| 横断面带生成 | `_build_cross_section_bands(...)` | road width、clear path width、furnishing width、profile | 生成 `left/right furnishing`、`clear_path`、`carriageway` 等 `StreetBand` | `StreetBand[]` |
| 家具需求估计 | `_estimate_furniture_requirements(...)` | query、density、street length、profile scales、inventory hints | 估算每类 street furniture 的数量需求 | `furniture_requirements` |
| 启发式程序生成 | `infer_street_program(...)` | query、`StreetComposeConfig`、inventory、POI/road graph | 组合 road type、cross section、bands、requirements、goals、notes | `StreetProgram` |
| learned 程序推断 | `ProgramGeneratorRuntime.generate(...)` | `ProgramGenerationInput`、program ckpt | 向量化输入、MLP 预测，再把预测应用回结构化 program | `ProgramGenerationResult` |
| 规则集加载 | `load_constraint_set(...)` | profile 名 | 加载 `balanced / pedestrian / transit / noise_aware` 规则集 | `ConstraintSet` |

这个模块是当前街道生成最重要的“显式中间表示层”。

### 4.6 布局求解模块

主要代码位置：

- `src/roadgen3d/layout_solver.py`
- `src/roadgen3d/milp_solver.py`

| 节点 | 代码入口 | 输入 | 处理 | 输出 |
| --- | --- | --- | --- | --- |
| 规则参数解析 | `_rule_parameter(...)`、`_apply_numeric_rule(...)` | `DesignRuleSpec`、当前值 | 读取 rule 参数并按 `<= >= =` 约束修正 | 修正值、是否发生 edit |
| 程序编译 | `_compile_program(...)` | `LayoutSolverInput` | 依据 lane、band width、required category、inventory availability 等规则改写 program | 重写后的 `StreetProgram`、edits、conflicts |
| 可放置 band 解析 | `_allowed_band_map(...)`、`_reserved_band_map(...)` | `ConstraintSet` | 把 declarative rules 转成 category -> allowed band / reserved band 映射 | band 约束映射 |
| 槽位生成 | `_build_slot_plans(...)` | 编译后的 program、segment graph、POI clusters | 为每个类别和每个功能带生成离散 slot plan，可带 anchored segment | `LayoutSlotPlan[]` |
| 规则评估 | `_evaluate_rule(...)` | rule、program、slot plans、冲突信息 | 生成每条规则的满足情况和解释 | `RuleEvaluation[]` |
| banded 求解 | `solve_layout(...)` | `LayoutSolverInput` | 运行当前默认启发式 banded solver | `LayoutSolverResult` |
| MILP 求解 | `solve_candidate_assignment(...)` | `StreetProgram`、`RoadSegmentGraph`、required categories | 对候选离散位置做 greedy / PuLP 选择 | 备选 `slot_plans` 与冲突 |
| 运行时分发 | `LayoutSolverRuntime.solve(...)` | backend 名、solver input | 在 `banded` 与 `milp_template_v1` 间切换，必要时回退 | `LayoutSolverResult` |

输出的 `LayoutSolverResult` 里不只是槽位，还有：

- `slot_plans`
- `edits`
- `conflicts`
- `rule_evaluations`

因此它既是布局结果，也是解释层。

### 4.7 场景实现与导出模块

主要代码位置：

- `src/roadgen3d/street_layout.py`

这是当前业务最重的总编排模块。

| 节点 | 代码入口 | 输入 | 处理 | 输出 |
| --- | --- | --- | --- | --- |
| 场景总编排 | `compose_street_scene(...)` | `StreetComposeConfig`、manifest、模型路径、policy/program ckpt | 串联 inventory、OSM、program、solver、placement、building、export、evaluation | `StreetComposeResult` |
| manifest 载入 | `_load_real_manifest(...)` | manifest 路径 | 读取并清洗真实资产记录 | asset rows |
| mesh cache 构建 | `_load_mesh_cache(...)` | asset rows | 缓存可用 mesh、bbox、类别和质量信息 | `_MeshCacheEntry` 映射 |
| 资产候选检索 | `_pick_category_candidate(...)` | slot query、category、topk、embedder、index、policy runtime、feature context | 检索某类候选资产，并可用 learned policy / 规则分数做重排 | 选中资产、候选明细、score 来源 |
| 候选位姿生成 | `_iter_slot_candidate_groups(...)` | slot、band、segment、theme、placement context | 生成 exact / ring / segment / theme-side 多层搜索候选 | 候选位姿组 |
| 位姿评分与约束过滤 | `_evaluate_slot_candidate(...)` | 候选位姿、已放置实例、band/side/theme、POI 规则 | 检查 overlap、zone containment、side match、theme segment 命中，再组合能量打分 | 可落地候选或阻塞原因 |
| placement 能量计算 | `compose_candidate_energy(...)` | 候选、邻居、pair config、POI attraction | 组合 pair interaction、距离、POI attraction、priority 等 | `CandidateEnergy` |
| POI 规则评分 | `score_placement(...)` | 位置、类别、`PoiContext`、`PoiRuleSet` | 根据 fire hydrant、bus stop、entrance 等规则做软约束评分和 veto | `ConstraintResult` |
| 树注入 | `_inject_parametric_trees(...)` | manifest 行、inventory summary | 为缺少树资产时补参数化树候选 | 增强后的 inventory |
| 周边建筑规划 | `_place_surrounding_buildings(...)` | projected buildings / grid lots、theme segments、asset inventory | 生成建筑目标、检索 building 候选、确定朝向/高度/落位 | building placements、zoning summary |
| 场景底图生成 | `_build_base_scene(...)`、`_build_osm_base_scene(...)` | template 或 OSM placement context、风格配置 | 生成道路、人行道、功能带、OSM 调试代理体 | base scene |
| 实例写入 | `_add_instance_meshes(...)` | placements、mesh cache、scene | 给每个实例施加地面姿态并写入场景 | 完整场景 |
| 场景导出 | `_export_scene(...)` | scene、输出目录、格式 | 导出 `glb/ply` | `scene.glb`、`scene.ply` |
| 分步生产视图 | `_build_production_steps(...)` | placements、zoning grid、POI、空间上下文 | 生成分阶段 GLB 和 companion figure | `production_steps/*.glb`、`production_steps.json` |
| 入口分析 | `evaluate_all_entrances(...)` | placements、road geometry | 计算 entrance openness / noise shielding | entrance summary |
| 展示评分 | `compute_presentation_report(...)` | placements、style、beauty 信息 | 生成展示导向报告 | presentation report |
| scene graph 构建 | `build_scene_graph(...)` | layout payload、road graph、POI context | 生成语义节点边关系 | `scene_graph` |
| 最终 layout 汇总 | `scene_layout.json` 组装 | config、program、solver、placements、分析结果 | 汇总可复盘的完整场景 payload | `scene_layout.json` |

这部分模块最终把“规则求解结果”转成“真实场景结果”。

### 4.8 评估与可视化模块

主要代码位置：

- `src/roadgen3d/eval_metrics.py`
- `src/roadgen3d/compliance_eval.py`
- `src/roadgen3d/spatial_viz.py`
- `src/roadgen3d/scene_graph_viz.py`
- `src/roadgen3d/entrance_analysis.py`

| 节点 | 代码入口 | 输入 | 处理 | 输出 |
| --- | --- | --- | --- | --- |
| 工程指标计算 | `eval_metrics.py` | placements、summary、rule evaluations | 计算 overlap、dropped slot、spacing uniformity、style consistency、balance、rule satisfaction 等 | 数值指标 |
| 入口开放度分析 | `compute_entrance_openness(...)` | entrance、邻近实例、道路边界 | 通过扇区 / 射线几何计算入口开敞度 | openness 分数 |
| 噪声遮挡分析 | `compute_noise_shielding(...)` | entrance、邻近实例、车行道方向 | 估计设施对车行噪声方向的遮蔽效果 | shielding 分数 |
| 合规评估 | `compute_compliance(...)`、`evaluate_compliance_batch(...)` | placements 或 layout JSON | 汇总 violated rules、constraint penalty、feasibility | compliance report |
| 合规报告落盘 | `write_compliance_report(...)` | compliance report、per-scene rows | 写 JSON 和 CSV | 报告文件 |
| 空间总览图 | `plot_scene_with_markers(...)` | 空间上下文、placements、OSM geometry | 绘制平面图、marker、摆放结果 | matplotlib figure / PNG |
| zoning 预览 | `plot_zoning_grid_preview(...)` | zoning grid、context | 绘制地块和建筑规划预览 | figure / PNG |
| 距离热力图 | `plot_distance_heatmap(...)` | spatial context、POI 类型 | 绘制距离场或热力图 | figure / PNG |
| scene graph 构建 | `build_scene_graph(...)` | layout payload、road graph、POI context | 构造场景图节点与边 | `scene_graph` payload |
| scene graph 可视化 | `plot_scene_graph(...)` | `scene_graph` payload | 渲染 graph 图 | plotly figure |

### 4.9 研究训练模块

主要代码位置：

- `scripts/m4_01_collect_policy_data.py`
- `scripts/m4_02_train_layout_policy.py`
- `scripts/m4_10_eval_engineering.py`
- `scripts/m6_01_collect_program_data.py`
- `scripts/m6_02_train_program_generator.py`
- `src/roadgen3d/layout_features.py`
- `src/roadgen3d/layout_policy.py`
- `src/roadgen3d/program_generator.py`

| 节点 | 代码入口 | 输入 | 处理 | 输出 |
| --- | --- | --- | --- | --- |
| Policy 样本采集 | `collect_policy_data(...)` | query 集、seed、manifest、index、compose 配置 | 枚举 slot 候选，记录候选特征、最终选择和上下文 | `policy_train.jsonl` |
| Policy 特征向量化 | `vectorize_slot_candidates(...)` | slot、candidate descriptors、上下文 | 生成数值特征矩阵 | feature matrix |
| Policy 训练 | `train_layout_policy(...)` | train/val 样本、训练超参数 | 训练 MLP 候选打分器 | policy ckpt、meta、curve |
| 工程对比评估 | `run_eval(...)` | query 集、rule/learned 模式配置 | 批量跑 compose，汇总 rule 与 learned 模式差异 | `summary.json`、`per_scene.csv` |
| Program 样本采集 | `collect_program_data(...)` | query 集、bbox、constraint profile、layout mode | 保存 `ProgramGenerationInput` 与 target `StreetProgram` | `program_train.jsonl` |
| Program 输入向量化 | `vectorize_program_input(...)` | query、compose config、inventory、road graph、POI | 生成 program runtime 的输入向量 | feature vector |
| Program target 生成 | `program_to_targets(...)` | `StreetProgram` | 拆成 lane、band width、goal、count 等多头监督目标 | target dict |
| Program 训练 | `train_program_generator(...)` | train/val 样本、训练超参数 | 训练多头 MLP 预测结构化 street program | program ckpt、meta、curve |

## 5. 当前两条主生成链路

### 5.1 单资产闭环

```text
query
  -> CLIP text embedding
  -> FAISS retrieval
  -> top asset latent load
  -> decoder
  -> voxel
  -> mesh export
  -> PipelineResult
```

适用范围：

- M1 / M2 单资产验证
- UI 中的单资产查询
- 资产库和解码链路验收

### 5.2 街道生成主链路

```text
StreetComposeConfig
  -> inventory summary
  -> (optional) OSM fetch / parse / project / road discovery
  -> PlacementContext / PoiContext / RoadSegmentGraph
  -> StreetProgram
  -> ConstraintSet
  -> LayoutSolverResult
  -> asset retrieval
  -> pose search + constraint scoring
  -> building planning
  -> export + evaluation + scene graph
  -> StreetComposeResult
```

其中又分两种模式：

- `template`
  - 不依赖 OSM，使用规则化直路模板
  - 仍然走 `StreetProgram -> ConstraintSet -> LayoutSolver`
- `osm`
  - 真实消费 AOI、道路、建筑、POI
  - `StreetProgram`、solver、placement 都会显式使用空间上下文

## 6. 当前设计的几个关键特征

1. 项目现在是“准备 + 生成 + 研究”的一体化工作台，不是单一 demo。
2. 街道生成的核心中间表示已经前移到 `StreetProgram`，而不是直接从 query 跳到 placement。
3. 约束层是双层结构：
   - 声明式设计规则：`ConstraintSet`
   - 位置级软约束评分：`PoiRuleSet` + `placement_field`
4. OSM 模式已经形成真实空间理解链路：
   - OSM 拉取
   - 投影与本地化
   - POI 驱动横断面
   - 段图构建
   - 锚定槽位与规则求解
5. 资产实现层已经是混合式：
   - 检索真实资产
   - 生成参数化资产
   - 对缺类资产做替补或占位
6. 研究模块围绕两个 learned 组件展开：
   - slot-level `LayoutPolicyRuntime`
   - structured `ProgramGeneratorRuntime`

## 7. 一句话结论

如果用一句话概括当前项目设计，RoadGen3D 现在更像是：

> 一个以 `OSM / POI + StreetProgram + ConstraintSet + LayoutSolver + asset realization` 为主干，同时带有 `workspace preparation`、`evaluation` 和 `research training` 能力的街道生成工作台。

当前最关键的三个中枢模块是：

1. `osm_ingest / placement_zones / osm_segment_graph`
   - 把真实空间上下文准备好。
2. `street_program / design_rules / layout_solver`
   - 把意图编译成可执行布局。
3. `street_layout`
   - 把布局真正实现成资产摆放、建筑补全、导出与分析结果。
