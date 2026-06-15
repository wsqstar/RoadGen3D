# 评估模块规范（步行友好度 · 安全 · 美观）

_最近更新：2026-04-09_

## 0. 背景与二维平面假设

- `scripts/m3_01_compose_street.py` 在默认 `layout_mode="template"` 下，仅生成一条线性街道走廊；`StreetProgram` 将整条街拆分为左右对称的 `carriageway / sidewalk / furnishing band`。
- `src/roadgen3d/topdown_render.py` 的 `_layout_bounds`、`_road_and_sidewalk_polygons` 显式以单条走廊的长宽参数（`length_m`、`road_width_m`、`sidewalk_width_m`）构建 2D 纹理和栅格。不存在真实街网节点，因此所有指标都必须基于“独立街段”设定。
- `src/roadgen3d/eval_metrics.py` 现有指标（`spacing_uniformity`、`balance_score`、`mean_noise_shielding` 等）已经可直接用于二维平面。新增模块无需修改 3D 资产流程，只需读写 `scene_layout.json` 与 `presentation_views/overview_top_design.png`。

因此，本规范以**单条街段**为研究对象，选择 11 个可量化的步行性指标；再组合安全、美观两个主观维度，形成完整评估模块。

## 1. 数据流程概览

```
scene_layout.json
  ├─ Walkability 指标计算（新增 eval_walkability.py）
  │     ├─ placements / zoning_grid / summary
  │     └─ 生成 11 个维度 → WalkabilityIndex
  ├─ Safety 渲染包 + 文本要素（topdown_render + solver summary）
  │     └─ LLM 批处理打分 → SafetyScore (含方差)
  ├─ Beauty 渲染包 + beauty.py 指标
  │     └─ LLM 打分 + presentation_score → BeautyScore
  └─ 汇总器（扩展 eval_metrics.aggregate_scene_rows）
        └─ eval_report.json / eval_per_scene.csv / UI 报表
```

新增工件：
- `artifacts/eval/<scene_id>/walkability.json`
- `artifacts/eval/<scene_id>/llm_safety.json`
- `artifacts/eval/<scene_id>/llm_beauty.json`
- `artifacts/eval/<scene_id>/render_topdown.png`（可复用 `presentation_views/overview_top_design.png`）

## 2. 步行友好度（定量 11 指标）

文献依据：Cervero 与 Kockelman（1997）、Ewing 与 Cervero（2010）、Ewing 与 Handy（2009）、Institute for Transportation and Development Policy（2018）、Gehl（2010）、Frank 等（2010）、Jacobs（1961）、Montgomery（1998）。这些研究均强调“Density / Diversity / Design”三要素及“保护-舒适-愉悦（Protection-Comfort-Delight）”框架。在单街段模型下，我们从 `scene_layout.json`、`StreetProgram` 与 `poi_context` 中提取 11 个量化指标（均归一化至 [0,1]），兼顾物理空间、街具配置与场所多样性。

### 2.1 指标逐项说明

#### `SID_CLR` —— 有效人行道净宽
- **内容定义**：人行道在去除树池、街具等障碍后的可通行净宽，映射 Gehl 对“舒适”与 NACTO 的最小 1.8 m 建议。 
- **数据与公式**：读取 `summary.sidewalk_width_m`，结合 `placements` 中占用 AABB 面积得到 `clear_width`；公式 `clamp((clear_width - 1.8) / (3.2 - 1.8), 0, 1)`。 
- **文献依据**：Gehl 2010、NACTO 2013《Urban Street Design Guide》。 
- **项目契合**：模板街段的宽度参数与所有家具 AABB 已写入 `scene_layout.json`，无需额外几何推断。

#### `CLEAR_CONT` —— 无障碍连续性
- **内容定义**：左右 clear-path 带在整段街道的覆盖率，衡量 ADA 强调的无障碍连续性。 
- **数据与公式**：`zoning_grid` 按 `lane_role` 区分 `left/right_clear_path`，求其面积与 sidewalk 面积之比 `clear_path_area / sidewalk_area`。 
- **文献依据**：ADA Standards 2010、ITDP《Pedestrians First》对连续人行系统的要求。 
- **项目契合**：RoadGen3D 在 `placement_zones` 阶段已经输出细分网格，可直接汇总而不会遗漏家具占地。

#### `FURN_D` —— 步行设施密度
- **内容定义**：bench/lamp/trash/bus_stop/mailbox/hydrant 等街具的线密度，反映 Gehl “Comfort” 子项中的“就地停留”与 CPTED 的可用设施。 
- **数据与公式**：从 `placements` 过滤相关类别，得到单位长度密度 `amenities_per_m`，再使用 `min(1, amenities_per_m / 0.15)` 归一。 
- **文献依据**：Gehl 2010、CPTED 街具配置建议。 
- **项目契合**：现有 `placements` 已附 `category`、`position_xyz`，与 `config.length_m` 结合即可得线密度。

#### `LIGHT_UNI` —— 照明均匀性
- **内容定义**：灯杆沿街分布的均匀度，直接影响夜间安全感（CPTED Lighting）。 
- **数据与公式**：提取 lamp 的 x 坐标序列，计算相邻间距的变异系数 `CV`，得分 `1 - CV(gap_x)`（调用 `compute_spacing_uniformity`）。 
- **文献依据**：Crowe 2000《CPTED》，NACTO 2013 夜间照明章节。 
- **项目契合**：RoadGen3D placements 记录所有 lamp，且单街段 x 轴与行进方向一致，计算简洁稳定。

#### `TREE_SHADE` —— 树冠遮荫率
- **内容定义**：树冠投影面积占人行道可达面积的比例，体现 ITDP 对遮荫的定量要求（30%+）。 
- **数据与公式**：复用 `topdown_render._SPRITE_SIZE_M` 中树冠宽度，结合树木个数与 sidewalk 面积得到 `canopy_area / sidewalk_area`。 
- **文献依据**：ITDP 2018、Gehl “Protection” 对气候缓冲的描述。 
- **项目契合**：模板街段中树木仅位于 furnishing 带，矩形近似即可满足精度。

#### `BUFFER_RATIO` —— 行人缓冲强度
- **内容定义**：左右家具/植被带对车流形成的缓冲强度。 
- **数据与公式**：从 `summary.left_furnishing_width_m`、`right_furnishing_width_m` 与 `road_width_m` 计算 `clamp((left+right)/road_width, 0, 1)`。 
- **文献依据**：NACTO《Urban Street Design Guide》关于缓冲带宽的比例关系。 
- **项目契合**：`StreetProgram` 决定的横断面已经在 summary 中暴露，可直接使用。

#### `TRANSIT_PROX` —— 公交可达性
- **内容定义**：人行道中心到最近 `bus_stop` 的距离得分，用于衡量 Ewing & Cervero “Distance to Transit”。 
- **数据与公式**：计算 sidewalk 中线至每个 bus_stop `position_xyz` 的欧氏距离，取最小值 `d`，得分 `exp(-d / 60)`。 
- **文献依据**：Ewing & Cervero 2010 五要素中的 D4（Transit Accessibility）。 
- **项目契合**：RoadGen3D placements 含 bus_stop 类别，且默认街长 60–120 m，尺度与经验值兼容。

#### `CROSS_PROV` —— 人行横道供给
- **内容定义**：横道数量与街段长度的比例，检验 ITDP 对 80 m 内横道布置的要求。 
- **数据与公式**：来自 `summary.spatial_context.poi_points_by_type_xz.crossing` 的 `crossings`，使用 `min(1, crossings / (length_m / 80))`。 
- **文献依据**：ITDP《Pedestrians First》Block Length 指标、NACTO 交叉口指南。 
- **项目契合**：RoadGen3D 的 crossing POI 在 OSM/模板两种模式下均可生成，可满足自动化统计。

#### `ENTR_DENS` —— 街墙活力/入口密度
- **内容定义**：沿街建筑入口的线密度，反映 Jacobs “eyes on the street” 与 Ewing & Handy 的“透明度（transparency）”。 
- **数据与公式**：`summary.entrance_count` / `length_m`，再按 `min(1, entrances_per_m / 0.04)` 归一。 
- **文献依据**：Jacobs 1961、Ewing & Handy 2009。 
- **项目契合**：`street_layout.py` 已执行入口分析（`entrance_report`），包括 `entrance_count` 与开敞度，可无损使用。

#### `POI_MIX` —— 场所/POI 多样性
- **内容定义**：街道两侧建筑与 POI 的功能多样性，体现 Cervero & Kockelman “Diversity” 与 Montgomery 对“place vitality”的强调。 
- **数据与公式**：使用 `summary.land_use_summary` 或 `summary.spatial_context.poi_points_by_type_xz` 中的类别计数 `n_i`，计算香农指数 `H = -Σ p_i log p_i`，并用 `POI_MIX = H / log(K)` 归一（`K` 为观测到的类别数）。 
- **文献依据**：Cervero & Kockelman 1997、Montgomery 1998、Jacobs 1961（多用途混合街区）。 
- **项目契合**：RoadGen3D 在 `StreetProgram` 阶段已经输出 `land_use_summary`（包含 mixed_use、civic、retail 等长度配比）以及 POI taxonomy，可直接计算而不依赖真实城市图。 
- **说明**：该指标取代原 `BAL_SCORE`，因为在单街段语境中，场所混合度对吸引力的解释力高于左右数量平衡；`balance_score` 仍可作为附加诊断但不计入 11 项核心指标。

#### `MICRO_ENV` —— 微气候综合
- **内容定义**：树荫、噪声屏蔽与入口开敞度的综合舒适度，映射 Gehl 的“保护-舒适-愉悦”。 
- **数据与公式**：使用 `TREE_SHADE`、`summary.mean_noise_shielding`、`summary.mean_entrance_openness`，公式 `0.5*TREE_SHADE + 0.3*mean_noise_shielding + 0.2*mean_entrance_openness`。 
- **文献依据**：Gehl 2010、Frank et al. 2010（微气候对步行健康影响）。 
- **项目契合**：噪声与开敞度已在 `street_layout.py`（entrance / noise analysis）中计算，可被直接复用。

### 2.2 分组与权重

- **Protection**（0.40）：`LIGHT_UNI`、`BUFFER_RATIO`、`CROSS_PROV`
- **Comfort**（0.35）：`SID_CLR`、`CLEAR_CONT`、`TREE_SHADE`、`MICRO_ENV`
- **Delight / Access**（0.25）：`FURN_D`、`TRANSIT_PROX`、`ENTR_DENS`、`POI_MIX`

总体评分：
```
WalkabilityIndex = 0.40 * mean(Protection) + 0.35 * mean(Comfort) + 0.25 * mean(Delight)
```

### 2.3 实现要点

1. 新增 `src/roadgen3d/eval_walkability.py`：
   - 解析 `scene_layout.json`，构建 `WalkabilityIndicators` 数据类；
   - 复用 `topdown_render._SPRITE_SIZE_M` 计算树冠面积；
   - 从 `zoning_grid` 聚合 clear_path 面积；
   - 支持 `--dump-indicators` 输出调试 JSON。
2. 新 CLI `scripts/eval_walkability.py`，可独立运行单场景；M4/M5 评估脚本通过模块 API 引用。
3. 指标写入 `artifacts/eval/<scene_id>/walkability.json`，同时追加到 `eval_per_scene.csv`；其中 `POI_MIX` 需同步缓存 `land_use_summary` 与 POI 统计以便回溯。

## 3. 安全维度（LLM + 结构化纠偏）

安全评价遵循 Jacobs 1961 “eyes on the street” 理论、CPTED（Crowe 2000）、NACTO 2013 交通缓解指南，最终输出 0~1 的 `SafetyScore`。评估由结构化特征 + LLM 主观判断两部分组成。

### 3.1 结构化安全特征

- **`LIGHT_UNI` – 照明均匀性**  
  - 内容：夜间可视性核心指标，与 Walkability 共享。  
  - 计算：`placements` 中 lamp 的 x 坐标 → `1 - CV(gap_x)`。  
  - 文献：CPTED Lighting。  
  - 契合度：已有实现，可直接复用。
- **`CROSS_PROV` – 横道供给**  
  - 内容：横道密度决定过街冲突。  
  - 计算：`min(1, crossings / (length_m / 80))`，`crossings` 来自 `poi_points_by_type_xz`。  
  - 文献：ITDP Block Length。  
  - 契合度：POI 数据现成。
- **`BUFFER_RATIO` – 家具带缓冲**  
  - 内容：家具/绿化带对机动车隔离能力。  
  - 计算：`clamp((left+right)/road_width, 0, 1)`，输入源于 summary。  
  - 文献：NACTO Buffer design。  
  - 契合度：横断面参数已暴露。
- **`BOLLARD_DENSITY` – 车行防护节点**  
  - 内容：关键节点的物理隔离。  
  - 计算：`count(bollard) / length_m`，上限 0.15/m。  
  - 文献：Gehl Protection。  
  - 契合度：placements 已标注 bollard。
- **`VISIBILITY_PENALTY` – 可视性惩罚**  
  - 内容：入口封闭 + 插槽失败导致的“视线盲区”。  
  - 计算：`(1 - mean_entrance_openness) * dropped_slot_rate`。  
  - 文献：Jacobs eyes-on-street。  
  - 契合度：两个输入都在 summary 中。

上述特征写入 `safety_structured.json`，供 LLM 参考并在最终权重中作为锚点。

### 3.2 LLM 打分工作流

1. **输入包**  
   - `overview_top_design.png`，叠加额外图层（照明、横道、缓冲）；
   - `scene_layout.summary` 摘要（实例数、规则满足、POI 报告、结构化特征）；
   - 自动观察列表（规则：灯间距>20 m、横道缺失、bollard 稀缺等）。
2. **提示词模板**  
```
You are an urban design auditor scoring perceived pedestrian safety (0-5).
Inputs: scene facts (JSON), heuristics list, image URL.
Criteria: lighting continuity, visibility/eyes-on-street, traffic protection, activity activation.
Return JSON {"lighting": x, "visibility": y, "protection": z, "activation": w, "overall": o}.
```
3. **执行策略**：`src/roadgen3d/llm/safety_eval.py` 调用指定模型（温度 0.2，`n=3`），缓存哈希由渲染 + 摘要组成。

### 3.3 分数融合与诊断

```
SafetyScore = 0.6 * mean(llm_overall)
              + 0.15 * CROSS_PROV
              + 0.15 * LIGHT_UNI
              + 0.10 * BUFFER_RATIO
```

- 若 `stddev(overall) > 0.8` 或结构化特征与 LLM 结论差异 >0.3，标记 `needs_review=true`。
- 报告中同时记录四个 LLM 子项及结构化特征，供 UI 呈现。

## 4. 美观与场所吸引力（LLM + 场所指标）

该部分覆盖视觉美感与“值得前往”的场所体验，结合 Kaplan & Kaplan 1989（coherence/complexity）、Nasar 1994、Ewing & Handy 2009，以及 Cervero & Kockelman、Montgomery 1998 关于多功能场所的论述。输出包括 `BeautyScore` 与场所相关指标。

### 4.1 `ACTIVE_FRONT_RATIO` —— 活跃界面占比
- **定义**：商业/社区服务等“活跃用途”沿街长度占总 frontage 的比例，体现“eyes on the street”。  
- **数据与公式**：`street_layout.py` 的 `frontage_summary.active_use_length / frontage_summary.total_length`，再按 `clamp(value / 0.7, 0, 1)` 归一。  
- **文献**：Jacobs 1961、Ewing & Handy 2009（Transparency）。  
- **契合度**：`StreetProgram` 已输出 frontage 分解（mixed_use、civic、blank），不需额外建模。

### 4.2 `ANCHOR_POI_SCORE` —— 目的地吸引强度
- **定义**：按类别权重衡量“锚点”POI（餐饮、文化、教育、公共服务、开放空间等）的密度，反映人们愿意步行到此的动机。  
- **数据与公式**：从 `summary.spatial_context.poi_points_by_type_xz` 统计各类 count，设置权重 `w_k`（例如餐饮 1.0、文化 1.2、教育 0.8、公服 1.1、休闲 0.9），计算 `Σ w_k * count_k / length_m`，再除以目标密度 `0.12` 归一。  
- **文献**：Cervero & Kockelman 1997（Diversity/Destination）、Montgomery 1998 Great Streets、ITDP Destination Accessibility。  
- **契合度**：RoadGen3D POI taxonomy 已覆盖这些类别；模板/OSM 模式都会生成相应坐标。

### 4.3 Presentation/Beauty 指标

- **`style_coherence`**：材质/色彩一致性（Kaplan coherence），由 `beauty.py` 计算。  
- **`visual_clutter`**：视觉杂乱度（Kaplan complexity），来自 `beauty.py`，得分越低越好。  
- **`spacing_rhythm`**：家具节奏与重复性（Nasar legibility），`beauty.py` 输出 0~1。  
- **`focal_readability`**：焦点清晰度（Ewing & Handy imageability），`beauty.py` 提供。  
- **`presentation_score`**：综合演示得分，整合渲染材质/光影表现。

### 4.4 LLM 评分与融合

1. **输入**：`overview_top_design.png`、style preset 元数据、active frontage/anchor 指标、结构化美观指标。  
2. **提示词**（示例）：  
```
You are a streetscape design critic. Score coherence, human_scale, material_contrast, visual_interest (0-5) based on the image and facts.
Return JSON {"coherence": ..., "human_scale": ..., "material_contrast": ..., "visual_interest": ..., "overall": ...}.
```
3. **融合**：  
```
BeautyScore = 0.4 * mean(llm_metrics)
              + 0.4 * presentation_score
              + 0.1 * ACTIVE_FRONT_RATIO
              + 0.1 * ANCHOR_POI_SCORE
```
- 记录每个子项、LLM 模型版本、提示模板哈希，方便追踪。
- `ACTIVE_FRONT_RATIO` 与 `ANCHOR_POI_SCORE` 也单独输出，供 UI 构建“场所吸引力”卡片。

## 5. 复合评分与报表

- `eval_per_scene.csv` 新增列：`walkability_index`、`safety_score`、`beauty_score` 以及 11 个基础指标。
- `eval_metrics.aggregate_scene_rows` 扩展对应字段并支持 rule/learned 模式差分。
- 面向 UI 的综合指标：
```
EvaluationScore = 0.45 * WalkabilityIndex + 0.35 * SafetyScore + 0.20 * BeautyScore
```

## 6. 工程计划

### 步骤 1：基础指标与测试（Algorithms）
- 开发 `src/roadgen3d/eval_walkability.py`，实现 11 个量化指标、`POI_MIX`、结构化缓存。
- 编写 `tests/test_eval_walkability.py`，构建合成场景验证指标单调性与 JSON 输出。

### 步骤 2：评估主流程（Eval）
- 扩展 `scripts/m4_10_eval_engineering.py`，在每个场景产出 `walkability.json`、`safety_structured.json`、`beauty_structured.json`。
- 将所有新字段汇入 `eval_per_scene.csv` 与 `eval_report.json`，支持 rule/learned 对比。

### 步骤 3：LLM 模块与缓存（ML Platform）
- 实现 `src/roadgen3d/llm/safety_eval.py`、`llm/beauty_eval.py`，封装提示词、并发调用、方差诊断。
- 在 `artifacts/eval/scene_<id>/` 维护基于渲染 + 摘要哈希的缓存，避免重复计费。

### 步骤 4：批量调度工具（Eval）
- 新增 `scripts/eval_quality.py` CLI，支持读取 query 列表、并发运行评估、断点续跑、失败重试。

### 步骤 5：API/UI 集成（Workbench）
- 更新 `GET /api/scene/jobs/{job_id}` 等接口返回 Walkability/Safety/Beauty 及子指标。
- 前端展示雷达图、诊断标签、LLM 置信度，允许下载 per-scene JSON。

### 步骤 6：权重校准与验证（Research）
- 收集 ≥50 组人工标注街段（真实或专家打分），对权重与阈值做回归/AHP 调节。
- 输出 calibration 报告，记录版本号和推荐配置。

## 7. 校验与 QA

- **单元测试**：对 11 指标分别构造极端场景（无树、有树）验证单调性；
- **金标集**：缓存 10 个代表场景的 LLM 结果（脱敏），对比未来模型输出的 `δ`；
- **敏感性分析**：侧向调节 `sidewalk_width_m`、`left_furnishing_width_m` 等参数，检查 WalkabilityIndex 是否符合预期趋势；
- **Explainability**：在 `walkability.json` 内写入 `top_contributors`（Shapley 或加权差值）帮助设计师定位问题。

## 8. 交付物

- 本文档 `docs/evaluation_module_plan.md`
- 新增 Python 模块、脚本、测试及 `artifacts/eval` 缓存规范
- 报告样例（JSON + CSV + PNG）

目录结构：
```
artifacts/
  eval/
    scene_<id>/
      walkability.json
      safety_llm.json
      beauty_llm.json
      render_topdown.png
```

## 9. 待决问题

1. 离线 LLM 采用哪一版（GPT-4.1、GPT-4o、Claude 3.7 还是本地视觉模型）？
2. 是否需要扩展至车辆安全（速度/碰撞风险），抑或继续聚焦行人感知？
3. `bus_stop` 是否足以代表公共交通，还是要引入 GTFS/OSM transit layer？
4. M8 若引入多段街网，`CROSS_PROV` 与 `ENTR_DENS` 需改写为图级指标，如何兼容？

## 10. 参考文献（APA 7th）

Cervero, R., & Kockelman, K. (1997). Travel demand and the 3Ds: Density, diversity, and design. *Transportation Research Part D: Transport and Environment, 2*(3), 199–219. https://doi.org/10.1016/S1361-9209(97)00009-6  
Crowe, T. D. (2000). *Crime prevention through environmental design* (2nd ed.). Butterworth-Heinemann.  
Ewing, R., & Cervero, R. (2010). Travel and the built environment. *Journal of the American Planning Association, 76*(3), 265–294. https://doi.org/10.1080/01944361003766766  
Ewing, R., & Handy, S. (2009). Measuring the unmeasurable: Urban design qualities related to walkability. *Journal of Urban Design, 14*(1), 65–84. https://doi.org/10.1080/13574800802451155  
Frank, L. D., Sallis, J. F., Saelens, B. E., Leary, L., Cain, K., Conway, T. L., & Hess, P. M. (2010). The development of a walkability index: Application to the Neighborhood Quality of Life Study. *British Journal of Sports Medicine, 44*(13), 924–933. https://doi.org/10.1136/bjsm.2009.058701  
Gehl, J. (2010). *Cities for people*. Island Press.  
Institute for Transportation and Development Policy. (2018). *Pedestrians first: Tools for a walkable city*. https://www.itdp.org/publication/pedestrians-first/  
Jacobs, J. (1961). *The death and life of great American cities*. Random House.  
Kaplan, R., & Kaplan, S. (1989). *The experience of nature: A psychological perspective*. Cambridge University Press.  
Montgomery, J. (1998). Making a city: Urbanity, vitality and urban design. *Journal of Urban Design, 3*(1), 93–116. https://doi.org/10.1080/13574809808724418  
Nasar, J. L. (1994). Urban design aesthetics: The evaluative qualities of building exteriors. *Environment and Behavior, 26*(3), 377–401. https://doi.org/10.1177/0013916594263008  
National Association of City Transportation Officials. (2013). *Urban street design guide*. https://nacto.org/publication/urban-street-design-guide/  
U.S. Department of Justice. (2010). *2010 ADA standards for accessible design*. https://www.ada.gov/regs2010/2010ADAStandards/2010ADAStandards_prt.pdf
