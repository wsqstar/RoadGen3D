# RoadGen3D 项目报告自动导出

- 生成时间: `20260517T025624Z`
- 数据原则: 所有数值来自现有 artifact 或当前代码评估；缺失项统一写为 `N/A`。
- 评价坐标约定: `x=walkability`, `y=safety`, `z=beauty`。

## 数据来源
- benchmark_samples: `/Users/shiqi/Coding/github/GIStudio/RoadGen3D/artifacts/branch_benchmarks/samples.jsonl`
- scenario_catalog: `/Users/shiqi/Coding/github/GIStudio/RoadGen3D/data/scenario_designs/hkust_gz_gate_scenarios.json`
- scenario_rubric: `/Users/shiqi/Coding/github/GIStudio/RoadGen3D/data/scenario_designs/hkust_gz_gate_evaluation_rubric.json`
- design_matrix_root: `/Users/shiqi/Coding/github/GIStudio/RoadGen3D/artifacts/design_matrix`

## 全量评价坐标

- 可用评价样本数: `907`
- Pareto front 样本数: `55`
- 导出表: `tables/evaluation_samples.csv`

![evaluation_3d_all.png](figures/evaluation_3d_all.png)

![evaluation_3d_by_skeleton.png](figures/evaluation_3d_by_skeleton.png)

![evaluation_3d_by_furniture.png](figures/evaluation_3d_by_furniture.png)

![mean_scores_by_skeleton.png](figures/mean_scores_by_skeleton.png)

![mean_scores_by_furniture.png](figures/mean_scores_by_furniture.png)

## 道路骨架与街道家具设计矩阵

- 已发现 design matrix layout 数: `24`
- 导出表: `tables/design_matrix_layouts.csv`
- 注意: design matrix layout 本身若没有现成评价分数，表中评价字段保持 `N/A`。

![design_matrix_coverage](figures/design_matrix_coverage.png)

## 场景截图索引

### 方案 1 · 道路瘦身完整街道
- scenario_id: `scenario_01_basic_complete_street`
- image_source: `existing_3d_capture`
![gallery_01.png](scenario_gallery/scenario_01_basic_complete_street/gallery_01.png)
![gallery_02.png](scenario_gallery/scenario_01_basic_complete_street/gallery_02.png)

### 方案 2 · 四车道慢行强化与中央安全岛
- scenario_id: `scenario_02_four_lane_multimodal_safety_island`
- image_source: `existing_3d_capture`
![gallery_01.png](scenario_gallery/scenario_02_four_lane_multimodal_safety_island/gallery_01.png)
![gallery_02.png](scenario_gallery/scenario_02_four_lane_multimodal_safety_island/gallery_02.png)

### 方案 3 · 公交停靠与混合界面
- scenario_id: `scenario_03_school_commercial_mixed_frontage`
- image_source: `presentation_render`
![gallery_01.png](scenario_gallery/scenario_03_school_commercial_mixed_frontage/gallery_01.png)
![gallery_02.png](scenario_gallery/scenario_03_school_commercial_mixed_frontage/gallery_02.png)

### 方案 4 · 儿童友好型学校走廊
- scenario_id: `scenario_04_child_friendly_school_corridor`
- image_source: `presentation_render`
![gallery_01.png](scenario_gallery/scenario_04_child_friendly_school_corridor/gallery_01.png)
![gallery_02.png](scenario_gallery/scenario_04_child_friendly_school_corridor/gallery_02.png)

### 方案 5 · 街道家具与社区活动增强
- scenario_id: `scenario_05_furniture_enriched_activity_street`
- image_source: `presentation_render`
![gallery_01.png](scenario_gallery/scenario_05_furniture_enriched_activity_street/gallery_01.png)
![gallery_02.png](scenario_gallery/scenario_05_furniture_enriched_activity_street/gallery_02.png)

### 方案 6 · 中央绿化带完整街道
- scenario_id: `scenario_06_green_median_complete_street`
- image_source: `presentation_render`
![gallery_01.png](scenario_gallery/scenario_06_green_median_complete_street/gallery_01.png)
![gallery_02.png](scenario_gallery/scenario_06_green_median_complete_street/gallery_02.png)

### 方案 7 · 非对称共享街道与口袋公园
- scenario_id: `scenario_07_asymmetric_shared_street_pocket_park`
- image_source: `presentation_render`
![gallery_01.png](scenario_gallery/scenario_07_asymmetric_shared_street_pocket_park/gallery_01.png)
![gallery_02.png](scenario_gallery/scenario_07_asymmetric_shared_street_pocket_park/gallery_02.png)

## Case Study: 方案 4 儿童友好型学校走廊

- scenario_id: `scenario_04_child_friendly_school_corridor`
- 标题: 方案 4 · 儿童友好型学校走廊
- 设计意图: 在不改变道路拓扑的前提下，缩窄车道并把学校侧空间让给儿童友好步行和过街安全设施。
- 布局来源: `/Users/shiqi/Coding/github/GIStudio/RoadGen3D/artifacts/scenario_design_options/hkust_gz_gate_from_current_layout/scenario_04_child_friendly_school_corridor/scene_layout.json`
- 工作副本: `/Users/shiqi/Coding/github/GIStudio/RoadGen3D/artifacts/project_report/20260517T025258Z/case_study/scene_layout.json`

### Case 截图

- 3D capture status: `succeeded`
![01_entrance_1](case_study/view_captures/01_entrance_1.png)
![02_overview_oblique_45](case_study/view_captures/02_overview_oblique_45.png)
![03_overview_top](case_study/view_captures/03_overview_top.png)
![04_junction_pedestrian_1](case_study/view_captures/04_junction_pedestrian_1.png)
![05_junction_pedestrian_2](case_study/view_captures/05_junction_pedestrian_2.png)
![06_junction_1](case_study/view_captures/06_junction_1.png)

补充 presentation views:
![Final Plan Axonometric](case_study/presentation/presentation_views/final_plan_axonometric.png)
![Final Oblique 45 Axonometric](case_study/presentation/presentation_views/final_oblique_45_axonometric.png)
![Overview Top Design](case_study/presentation/presentation_views/overview_top_design.png)
![Hero Left](case_study/presentation/presentation_views/hero_left.png)

### 详细评价

| Metric | Value | Source |
| --- | --- | --- |
| walkability_index | 0.4137 | EvalEngine |
| safety_structural_score | 0.4842 | EvalEngine |
| safety_final_score | 0.4842 | EvalEngine |
| beauty_structural_score | 0.3923 | EvalEngine |
| beauty_final_score | 0.3923 | EvalEngine |
| evaluation_score | 0.4341 | EvalEngine |
| generation_quality_score | N/A | EvalEngine |
| rubric_status | Fail | ScenarioRubricEvaluator |
| rubric_total_score | 0.4493 | ScenarioRubricEvaluator |

- JSON: `tables/case_study_evaluation.json`
- CSV: `tables/case_study_metrics.csv`

### Walkability 指标
| Metric | Value |
| --- | --- |
| AMENITY_SERVICE_DENSITY | 1 |
| BUFFER_RATIO | 0.296 |
| CLEAR_CONT | 0.8478 |
| CLEAR_PATH_CONFLICT_PENALTY | 0 |
| CROSS_PROV | 0.5 |
| ENTR_DENS | 0 |
| FURNITURE_OCCUPATION_RATIO | 1 |
| FURN_D | 0.65 |
| LIGHT_UNI | 0 |
| MICRO_ENV | 0.2 |
| POI_MIX | 0.9977 |
| SID_CLR | 1 |
| TRANSIT_PROX | 0 |
| TREE_SHADE | 0 |

### Safety 指标
| Metric | Value |
| --- | --- |
| BOLLARD_DENSITY | 1 |
| BUFFER_RATIO | 0.296 |
| CROSS_PROV | 0.5 |
| LIGHT_UNI | 0 |
| VISIBILITY_PENALTY | 0 |

### Beauty 指标
| Metric | Value |
| --- | --- |
| active_front_ratio | 0 |
| anchor_poi_score | 0 |
| presentation_score | 0.7311 |
| visual_clutter | 0.0017 |

### Scenario Rubric
| Item | Value |
| --- | --- |
| status | Fail |
| total_score | 0.4493 |
| profile_pair | quiet_residential+balanced_complete |
| missing_metrics | N/A |

| Gate | Status | Description |
| --- | --- | --- |
| child_walk_expression | Pass | Child-friendly walk or play-walk surface should be expressed. |
| school_crossing_expression | Pass | School crossing or crossing surface should be present. |
| refuge_island_expression | Pass | Refuge island or safety island should be present. |

## N/A 说明

`N/A` 表示对应 artifact 中没有该字段、该图片不存在、或当前评估代码无法完成该项计算。脚本不会用均值、经验值或文字推测补齐缺失数据。
