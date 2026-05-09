# 评价体系对照文档（代码真相优先）

本文档以**代码实现**为最终真相，逐项对照“设计文档定义”与“当前运行实现”，重点覆盖三个体系（Walkability / Safety / Beauty）下全部子指标。

- 设计规范参照：[`docs/scoring_formula_specification.md`](docs/scoring_formula_specification.md)
- 运行实现入口（现网）：`eval_engine_ext`（`road_metrics`）
  - `src/roadgen3d/llm/design_workflow.py`：服务层将 `EvalEngine(EvalConfig(enable_llm_eval=True))` 挂载到 `self.eval_engine`
  - 路由 `/api/design/evaluate/unified` 在 `web/api/main.py` 调用 `DesignAssistantService.evaluate_scene_unified`

文中公式中用到的通用函数约定：
- `clamp(x,0,1)=max(0,min(1,x))`
- `_mean`：算术均值
- 所有 subscore 均按 0~1 做归一化后再加权

---

## 0. 统一评分链与输出策略（先确认）

### 0.1 统一计算

```python
overall = W*walkability_index + S*safety_final + B*beauty_final
```

其中默认权重来自 `src/roadgen3d/eval_engine_ext/road_metrics/core/config.py` 的 `AggregationConfig`：
- `W=0.45`
- `S=0.35`
- `B=0.20`

### 0.2 API 输出

`DesignAssistantService.evaluate_scene_unified()` 统一返回：
- `walkability`: `int(walkability_index*100)`
- `safety`: `int(safety.final_score*100)`（仅 LLM 可用时）
- `beauty`: `int(beauty.final_score*100)`（仅 LLM 可用时）
- `overall`: `int(agg*100)`，要求 `safety` 与 `beauty` 均可用，缺任一返回 `null`
- `indicators`: 包含 walkability 三支柱和可用的 safety/beauty LLM 子分；
- `llm_status`: 带每个子系统 `enabled/available/source/cached/visual_input` 等元数据

对应文件：
- `src/roadgen3d/llm/design_workflow.py:579-636`
- `src/roadgen3d/eval_engine_ext/road_metrics/core/engine.py:_aggregate_scores`

### 0.3 与设计一致性一句话

- 设计文档给出综合权重结构一致；
- 但“服务端整体可用性规则”有补充：缺截图时，Safety/Beauty 设为 N/A，整体分不返回（`overall=null`），即实现侧有更严格的降级策略。

---

## 1. Walkability（11 个子指标）

`WalkabilityIndex` 分三支柱聚合，来源代码：
- `Protection = mean(LIGHT_UNI, BUFFER_RATIO, CROSS_PROV)`
- `Comfort = mean(SID_CLR, CLEAR_CONT, TREE_SHADE, MICRO_ENV)`
- `Delight = mean(FURN_D, TRANSIT_PROX, ENTR_DENS, POI_MIX)`
- `WalkabilityIndex = 0.4*Protection + 0.35*Comfort + 0.25*Delight`
- 代码实现：`src/roadgen3d/eval_engine_ext/road_metrics/metrics/walkability.py`

### SID_CLR

- 设计定义（文档）：
  `SID_CLR = clamp((mean(left_clear_path_width_m, right_clear_path_width_m) - 1.8) / (3.2 - 1.8), 0, 1)`
- 代码实现：
  - 缺失 `left/right_clear_path_width_m` 时按 `sidewalk_width_m` 回退；
  - 与设计一致地应用 `clamp((clear_width-1.8)/(3.2-1.8))`
- 公式实现：
  `clear_width = mean(left_clear or sidewalk_width, right_clear or sidewalk_width)`
  `SID_CLR = clamp((clear_width-1.8)/(3.2-1.8))`
- 源码：`src/roadgen3d/eval_engine_ext/road_metrics/metrics/walkability.py`

### CLEAR_CONT

- 设计定义：
  `clear_area = length*(left_clear+right_clear)`
  `sidewalk_area = length*sidewalk_width*2`
  `CLEAR_CONT = clamp(clear_area/sidewalk_area)`
- 代码实现：
  与设计一致，且在分母中防御 `sidewalk_area=0`，使用 `1e-3` 下限。
- 源码：`src/roadgen3d/eval_engine_ext/road_metrics/metrics/walkability.py`

### FURN_D（步行设施密度）

- 设计定义：
  `count_density = amenities_count / length`
  `FURN_D = clamp(count_density/0.15)`
- 代码实现：
  采用“数量+面积”混合，并引入两类基准：
  - `count_score = clamp((count/length)/amenity_count_density_ideal)`，默认 `amenity_count_density_ideal=0.15`
  - `area_score = clamp((footprint_area/length)/amenity_density_ideal)`，默认 `amenity_density_ideal=0.15`
  - 默认权重来自配置：`furn_count_weight=0.5`，`furn_area_weight=0.5`（传入 compose 时）
  - 组合：`FURN_D = 0.5*count_score + 0.5*area_score`
- 关键实现细节（实现来源）：
  - 参与项分类为 `bench/lamp/trash/bus_stop/mailbox/hydrant`（`AMENITY_CATEGORIES`）
  - 足迹面积来源：`bbox_xz` 优先；否则 `native_size_m * scale`
- 设计一致性：**不一致**（设计口径是单纯数量密度）
- 源码：`src/roadgen3d/eval_engine_ext/road_metrics/extractors/furniture.py`、`base_metrics/core.py`、`composers/furniture.py`、`metrics/walkability.py`

### LIGHT_UNI

- 设计定义：
  `gaps = sorted(lamp_x[i+1]-lamp_x[i])`
  `CV = std(gaps)/mean(gaps)`
  `LIGHT_UNI = clamp(1-CV, 0, 1)`
- 代码实现：
  - 仅用 `category=="lamp"` 的 `position_xyz[0]` 取 x；
  - 少于 2 根灯杆时直接 1.0；
  - 与设计公式一致。
- 源码：`src/roadgen3d/eval_engine_ext/road_metrics/metrics/walkability.py`、`base_metrics/core.py`

### TREE_SHADE

- 设计定义：
  `canopy_area = tree_count * (3.6*3.6)`（固定近似）
  `TREE_SHADE = clamp(canopy_area / sidewalk_area, 0, 1)`
- 代码实现：
  - 非固定面积近似，采用栅格并集估算树冠覆盖率；
  - 树冠半径来自 `TreeData`：
    - 有 `native_size.canopy_width_m` 时 `area=(width*scale)^2`
    - 无则默认 `3.6*3.6`
    - `radius = sqrt(area/π)`
  - 用网格离散估算：`grid_resolution=0.5m`，覆盖区域 `[ -L/2,L/2 ] x [ -sidewalk_width, +sidewalk_width ]`
  - `tree_shade = clamp(shaded_cells / total_cells)`
- 与设计一致性：**不一致**（实现改为几何并集估算）
- 源码：`src/roadgen3d/eval_engine_ext/road_metrics/extractors/trees.py`、`metrics/walkability.py`

### BUFFER_RATIO

- 设计定义：
  `(left_furnishing_width + right_furnishing_width) / road_width`
- 代码实现：
  与设计一致，输出已 clamp 到 `[0,1]`
- 源码：`metrics/walkability.py`

### TRANSIT_PROX

- 设计定义：
  `exp(-d_min/60)`，`d_min` 为最近公交站到人行道中心线距离
- 代码实现：
  - 人行道中心线取两条线：`z = ±(road_width/2 + sidewalk_width/2)`
  - 对每个公交站逐点取最近距离 `d_min`
  - `TRANSIT_PROX = exp(-d_min / decay_m)`，默认 `decay_m=60`
- 源码：`metrics/walkability.py`、`core/config.py`

### CROSS_PROV

- 设计定义：
  `target=length/80`
  `CROSS_PROV=clamp(crossings/target,0,1)`
- 代码实现：
  变为“双成分”：
  - `adequacy=clamp(crossings/target,0,1)`，`target=length/80`
  - `uniformity = compute_spatial_uniformity_1d(cross_x_positions)=clamp(1-CV)`
  - `CROSS_PROV = (1-penalty)*adequacy + penalty*uniformity`
  - 默认 `penalty = crossing_uniformity_penalty = 0.5`
- 与设计一致性：**不一致**（新增均匀性约束）
- 源码：`extractors/crossing.py`、`base_metrics/core.py`、`composers/crossing.py`、`metrics/walkability.py`

### ENTR_DENS

- 设计定义：
  `entrance_count/length/0.04`（clamp）
- 代码实现：
  完全一致。
- 源码：`metrics/walkability.py`、`base_metrics/core.py`

### POI_MIX

- 设计定义：
  以 Shannon 熵归一到最大熵 `log(K)`
- 代码实现：
  - 同时汇总 `land_use_summary` 与 `poi_points_by_type_xz`
  - 过滤掉非正值类别后计算熵
  - `POI_MIX = clamp(entropy / log(K))`
- 源码：`metrics/walkability.py`

### MICRO_ENV

- 设计定义：
  `0.5*TREE_SHADE + 0.3*mean_noise_shielding + 0.2*mean_entrance_openness`
- 代码实现：
  与设计一致。已做 clamp 到 `[0,1]`
- 源码：`metrics/walkability.py`

### Walkability 输出与诊断

- 输出包含 `sid_clr/clear_cont/furn_d/light_uni/tree_shade/buffer_ratio/transit_prox/cross_prov/entr_dens/poi_mix/micro_env`
  与 `protection/comfort/delight/walkability_index`
- `WalkabilityIndicators` 中存在 `top_contributors` 字段，但在 `ext` 当前实现里未在 `compute_walkability` 赋值，接口返回 `[]`（历史实现有对应计算逻辑，可用于回填对齐）。
- 源码：`core/types.py`（字段）、`metrics/walkability.py`（返回值构造）

---

## 2. Safety（安全）

### 2.1 结构化分

- 设计文档核心权重（结构化）：
  `0.15*CROSS_PROV + 0.15*LIGHT_UNI + 0.10*BUFFER_RATIO + 0.10*BOLLARD_DENSITY + max(0,0.1-visibility_penalty)`
- 代码实现：
  - `BOLLARD_DENSITY = clamp(count_density / 0.15)`，count_density=`bollard_count/length`
  - `visibility_penalty = clamp((1-mean_entrance_openness)*dropped_slot_rate)`
  - `visibility_score = 1-visibility_penalty`
  - 结构化打分：
    `0.15*CROSS_PROV + 0.15*LIGHT_UNI + 0.10*BUFFER_RATIO + 0.10*BOLLARD_DENSITY + 0.10*VISIBILITY_SCORE`
  - 与设计形式一致（`max(0,0.1-penalty)` 在当前代码中改为 `0.10 * (1 - penalty)`）
- 源码：`metrics/safety.py`、`core/types.py`

### 2.2 LLM增强分

- LLM 评分源：`lighting/visibility/protection/activation`，从 0–5 转到 0–1（`value/5` 后 clip）
- 代码实现：
  `llm_mean = mean(light, visibility, protection, activation)`
  `Safety = 0.60*llm_mean + 0.15*CROSS_PROV + 0.15*LIGHT_UNI + 0.10*BUFFER_RATIO`
- `needs_review = stddev(llm_scores) > 0.20`
- 是否可用：`evaluate_safety()` 无可见视图输入则 `available=False`
- 源码：`evaluators/safety_eval.py`、`metrics/safety.py`

### 2.3 与设计对照

- 设计与实现对齐项：四个结构化输入 + llm子维度集成和阈值。
- 关键差异：LLM 缺失时 safety 置为不可用，统一接口层直接让 `safety` 与 `overall` 为 `null`，并写入 `suggestions` 说明“需要截图”。

---

## 3. Beauty（美观）

### 3.1 结构化分

- 设计文档结构化：`0.4*presentation_score + 0.1*active_front_ratio + 0.1*anchor_poi_score + 0.1*(1-visual_clutter)`
- 代码实现：
  - `presentation_score`：来自 `summary.composition_report.presentation_score` 或 `summary.presentation_score`
  - `active_front_ratio = normalize_density(entrance_count * 4 / (2*length), ideal=0.70)`
  - `anchor_poi_score`：按类型加权的 POI 密度，默认权重表见代码中的 `_ANCHOR_POI_WEIGHTS`
  - `visual_order = 1 - clamp(visual_clutter)`，再 `0.1` 权重
  - `Beauty_struct = 0.4*presentation + 0.1*active_front_ratio + 0.1*anchor_poi + 0.1*visual_order`
- 源码：`metrics/beauty.py`、`core/config.py`

### 3.2 LLM增强分

- LLM 评分源：`coherence/human_scale/material_contrast/visual_interest`，0–5 转 0–1
- 代码实现：
  `llm_mean = mean(coherence, human_scale, material_contrast, visual_interest)`
  `Beauty = 0.40*llm_mean + 0.40*presentation + 0.10*active_front_ratio + 0.10*anchor_poi_score`
- `needs_review = stddev(llm_scores) > 0.20`
- 源码：`evaluators/beauty_eval.py`、`metrics/beauty.py`

### 3.3 与设计对照

- 设计定义与实现结构化公式基本一致；
- 差异：设计文档提到诊断性字段 `style_coherence/spacing_rhythm/focal_readability`，实现层 `features` 不直接承载该三项（`diagnose_beauty` 用时有可能拿不到，需注意展示语义）。

---

## 4. 实现和设计的并行版本对照（防混淆）

仓库中同时存在：
- `src/roadgen3d/eval_engine_ext/road_metrics/*`（当前服务链路）
- `src/roadgen3d/eval_engine/*`（新引擎雏形）

差异摘要：
- 新引擎 `walkability.compute_walkability` 与 `ext` 的输入输出结构不完全一致，且 LLM 增强在新引擎中是 TODO 占位。
- 新实现与 ext 的主要数值差异在：
  - `FURN_D`：新实现偏向面积成分逻辑，ext 为面积+数量混合
  - `CROSS_PROV`：新实现是纯 crossings/target，ext 有均匀性分量
  - `TREE_SHADE`：新实现文档/默认常用更接近固定面积近似

推荐：对外评审文档统一口径指向 `eval_engine_ext`；`eval_engine` 仅作为非生产分支。

---

## 5. 主要结论（给设计/研发对齐清单）

1. 目前最关键偏离点是三项：
   - `FURN_D` 的混合定义
   - `CROSS_PROV` 的均匀性增强
   - `TREE_SHADE` 的网格并集估算
2. 安全/美观 LLM 有明确“可视输入缺失即降级为 N/A”的实现策略，这个策略在 API 层体现比设计文档更强。
3. `WalkabilityIndicators.top_contributors` 字段在代码类型上存在，但当前 ext 实现未填充，若要对外展示建议补齐。
