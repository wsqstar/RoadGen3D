结论：**当前 metrics 可以作为“早期自动打分 / demo 质量筛查”使用，但还不足以支撑“科学评价”或论文级 claim。**
它现在更像是一个 **layout JSON 驱动的规则评分器 + 截图 LLM 视觉评分器**，而不是一个真正同时验证 **设计语义、几何可达性、三维渲染一致性、空间体验质量** 的评价系统。

文档显示，当前总分是：

[
overall = 0.45 \cdot Walkability + 0.35 \cdot Safety + 0.20 \cdot Beauty
]

并且 `safety` 和 `beauty` 依赖 LLM 可视输入，缺截图时会返回 `null`，整体分也不返回。

---

## 1. 当前 metrics 是否足够？

**不够。**

当前体系覆盖了三类指标：

1. **Walkability**

   * clear sidewalk width
   * clear continuity
   * furniture density
   * lighting uniformity
   * tree shade
   * buffer ratio
   * transit proximity
   * crossing provision
   * entrance density
   * POI mix
   * micro environment

2. **Safety**

   * crossing
   * lighting
   * buffer
   * bollard density
   * visibility
   * LLM visual judgment

3. **Beauty**

   * presentation score
   * active frontage
   * anchor POI
   * visual clutter / visual order
   * LLM visual judgment

这些指标对“街道横断面配置、设施数量、绿化、出入口、POI、多样性”是有用的。但是对于你们现在的任务——**给定 layout JSON，生成最终 GLB 街道场景，并评价生成质量**——它缺少至少四类关键评价：

### A. JSON → GLB 的一致性评价缺失

你们有 `layout json` 和最终 `glb`。这意味着最核心的问题之一不是“街道好不好”，而是：

> JSON 里定义的对象、位置、尺寸、类别、道路结构，是否真的被正确生成进 GLB？

当前文档主要是在 layout/summary 层取指标，再结合截图做 Safety/Beauty。它没有明确做：

* object count consistency
  JSON 中有 10 棵树，GLB 中是否真的有 10 棵？
* category consistency
  JSON 中的 `lamp` 是否在 GLB 中对应灯杆 mesh，而不是丢失或错类？
* position consistency
  JSON 中的树、灯、bench 是否落在正确 lane / sidewalk / furnishing zone？
* scale consistency
  GLB 中模型尺寸是否符合 JSON 的 `native_size_m * scale`？
* orientation consistency
  长椅、灯杆、公交站、路口设施方向是否正确？
* topology consistency
  道路、人行道、转角、交叉口是否连续闭合？

这部分应该是你们评价体系的第一层。否则可能出现：**JSON 分数很高，但 GLB 渲染错位、穿模、缺物体、尺度崩坏，最终仍然得高分。**

---

### B. 三维几何合法性评价缺失

GLB 是一个真实三维场景文件，所以必须评价几何本身。当前 Walkability 里面有 `SID_CLR`、`CLEAR_CONT`、`TREE_SHADE` 等二维/规则指标，但没有系统检查：

* mesh 是否穿插；
* 人行道是否被家具、树、灯杆阻塞；
* curb / sidewalk / road surface 是否高度连续；
* road surface 是否破面、重叠、法线错误；
* turn corner 是否形成空洞；
* lane polygon 是否自交；
* 设施是否悬空或沉入地面；
* 建筑净空 lane 是否被设施侵入；
* pedestrian clear path 是否在 GLB 几何中真的可行走。

这对你们的道路生成尤其关键。因为一个 layout 在 2D JSON 中可能看起来合理，但 GLB 中会因为模型 bounding box、scale、rotation、origin 不一致导致实际阻塞。

所以现在的 metrics **缺少 geometry validity / physical plausibility / navigability validation**。

---

### C. 视角依赖的视觉评价不稳定

Safety 和 Beauty 依赖 LLM 可视输入。文档里明确说，无可见视图输入时 Safety/Beauty 不可用，整体分也为 `null`。

这说明当前评价链对截图强依赖。但截图评价有几个问题：

1. **视角不唯一**
   从鸟瞰看很整齐，从行人视角可能很差。

2. **截图可能遮蔽错误**
   穿模、错位、背面破面、远端缺失，可能在某个 camera view 下不可见。

3. **LLM 分数不可严格复现**
   同一场景不同截图、不同 prompt、不同模型版本，可能给不同美观/安全评分。

4. **LLM 视觉分不能替代结构指标**
   它适合做 perceptual assessment，但不应该决定整体科学评价的核心部分。

因此，LLM 视觉分应该作为 **auxiliary perceptual score**，而不是主评价骨架。

---

### D. 缺少任务目标相关指标

你们的任务不是一般城市设计审美，而是道路/街道场景生成。当前指标偏 “complete street / walkability audit”，但缺少道路生成任务的直接指标：

* road skeleton fidelity；
* lane continuity；
* junction correctness；
* turning geometry smoothness；
* sidewalk corner continuity；
* multi-lane alignment；
* road-surface polygon validity；
* lane width consistency；
* asset placement compliance；
* scene graph / layout graph consistency；
* renderability and preview stability。

这些才是“道路生成”系统更核心的 metrics。

---

## 2. 当前计算是否科学？

我的判断是：**局部公式有合理性，但整体科学性不足。**

更准确地说：

> 当前计算是“工程启发式合理”，但不是“严格验证过的科学评价体系”。

---

## 2.1 合理的部分

有些指标是合理的。

例如：

### `SID_CLR`

用 clear path width 映射到 0–1：

[
SID_CLR = clamp\left(\frac{clear_width - 1.8}{3.2 - 1.8}, 0, 1\right)
]

这有明确语义：小于 1.8m 不理想，大于 3.2m 接近满分。作为行人净宽指标是合理的。

### `LIGHT_UNI`

用灯杆间距的变异系数 CV 计算均匀性：

[
LIGHT_UNI = clamp(1 - CV, 0, 1)
]

这也合理。它不只看灯的数量，而是看分布是否均匀。

### `TREE_SHADE`

实现从固定树冠面积改成几何并集估算，这是比单纯 `tree_count * area` 更科学的。文档也说明当前实现用 0.5m 网格估算树冠覆盖，而不是简单固定面积近似。

### `CROSS_PROV`

实现中加入 crossing uniformity，比单纯 `crossing_count / target` 更合理。因为两个斑马线挤在一起和均匀分布不是同一种空间服务质量。

所以：**Walkability 内部一些局部指标是有科学直觉的。**

---

## 2.2 不够科学的部分

### 问题 1：权重缺少校准依据

当前总权重是：

* Walkability: 0.45
* Safety: 0.35
* Beauty: 0.20

Walkability 内部又是：

* Protection: 0.4
* Comfort: 0.35
* Delight: 0.25

这些权重可以作为 demo 默认值，但如果要说“科学”，需要回答：

* 权重来自哪篇标准？
* 是否经过专家标注校准？
* 是否与用户偏好数据拟合？
* 是否通过真实街景 audit benchmark 验证？
* 不同街道类型是否应使用同一权重？

否则这只是一个 **manual weighted sum**。

建议把当前权重表述为：

> configurable heuristic weights, not empirically calibrated weights.

---

### 问题 2：大量 clamp 导致分数饱和

很多指标都用了 `clamp(x, 0, 1)`。这在工程上方便，但会带来分数饱和问题。

例如：

* 家具密度超过阈值后都等于 1；
* crossing 超过 target 后都等于 1；
* buffer ratio 高到一定程度后也等于 1；
* tree shade 超过一定比例后也等于 1。

这会导致两个问题：

1. **无法区分“刚好足够”和“过度堆砌”**
   例如家具太多可能造成 clutter 和通行阻碍，但 `FURN_D` 可能仍然高。

2. **无法惩罚过量设计**
   很多城市设计指标不是越多越好，而是存在最佳区间。

更科学的做法是把部分指标从“单调递增函数”改成“区间最优函数”：

[
score(x)=\exp\left(-\frac{(x-\mu)^2}{2\sigma^2}\right)
]

例如 furniture density、tree density、bollard density、POI density 都不应简单越多越好。

---

### 问题 3：`FURN_D` 的科学含义混合

文档指出，`FURN_D` 当前实现不是设计文档里的单纯数量密度，而是数量密度和面积密度 0.5/0.5 混合。

这个改动在工程上可以理解，但科学含义变得不清楚：

* count density 衡量设施服务频率；
* footprint area 衡量空间占用；
* 两者一个偏正面，一个可能偏负面。

把它们直接平均，会出现问题：

> 一个场景里设施巨大、占地很大，可能得到更高 `FURN_D`，但实际会压缩行人净宽。

所以建议拆开：

* `amenity_service_density`：设施服务密度，正向；
* `furniture_occupation_ratio`：设施占用率，可能是负向或区间最优；
* `clear_path_conflict_rate`：家具侵入净通行带比例，负向。

不要把“服务供给”和“空间占用”混成一个正向指标。

---

### 问题 4：Safety 过度依赖视觉 LLM

当前 Safety 的 LLM 增强分是：

[
Safety = 0.60 \cdot LLM_mean + 0.15 \cdot CROSS_PROV + 0.15 \cdot LIGHT_UNI + 0.10 \cdot BUFFER_RATIO
]

也就是说 LLM 视觉判断占 60%。文档明确写了这个实现。

这对 demo 友好，但对科学评价偏危险。

安全性应该更多来自结构化、几何化指标：

* crossing conflict exposure；
* pedestrian-vehicle separation；
* lighting coverage；
* sightline visibility；
* obstacle occlusion；
* sidewalk blockage；
* speed environment proxy；
* junction crossing distance；
* curb radius / turning radius；
* bollard placement correctness。

LLM 可以评价“看起来是否安全”，但不应该压过可计算的几何安全指标。

建议改成：

[
Safety = 0.65 \cdot Safety_{geom} + 0.20 \cdot Safety_{layout} + 0.15 \cdot Safety_{visual}
]

其中 LLM 只占 10–20%。

---

### 问题 5：Beauty 也是弱可复现指标

Beauty 中 LLM 占 40%，presentation 又占 40%。文档显示 Beauty 最终为：

[
Beauty = 0.40 \cdot LLM_mean + 0.40 \cdot presentation + 0.10 \cdot active_front + 0.10 \cdot anchor_poi
]



问题是 `beauty` 本身主观性很强。如果用于 demo，可以接受。如果用于论文，需要明确它是：

* perceptual preference score；
* visual coherence proxy；
* not objective urban quality.

更科学的方式是将 Beauty 拆成：

* visual order；
* material coherence；
* scale harmony；
* spatial rhythm；
* clutter penalty；
* landmark / focal readability；
* facade activation；
* vegetation balance。

并且尽量有一部分由几何和 asset metadata 计算，而不是完全依赖截图 LLM。

---

## 3. 基于 layout JSON + GLB，建议重构成四层评价体系

你们现在有两个输入：

```text
Input A: layout.json
Input B: scene.glb
Optional C: rendered screenshots / videos
```

建议评价体系改成：

```text
Layer 1: Layout Semantic Metrics
Layer 2: JSON–GLB Consistency Metrics
Layer 3: Geometry / Topology Validity Metrics
Layer 4: Perceptual Rendering Metrics
```

---

## Layer 1：Layout Semantic Metrics

这层保留你们当前大部分 Walkability / Safety / Beauty 指标。

输入：`layout.json`

评价对象：

* road width
* sidewalk width
* furnishing lane
* tree placement
* lamp placement
* crossing placement
* entrance density
* POI mix
* buffer ratio
* transit proximity

输出：

```json
{
  "walkability_layout": 0.73,
  "safety_layout": 0.62,
  "beauty_layout": 0.58
}
```

这一层评价的是：

> 设计意图是否合理。

但它不负责判断 GLB 是否真的生成正确。

---

## Layer 2：JSON–GLB Consistency Metrics

这是你们当前最应该补的一层。

输入：`layout.json + scene.glb`

评价：

### 1. Object Recall

[
Recall_{obj} = \frac{|O_{json} \cap O_{glb}|}{|O_{json}|}
]

JSON 中定义的对象，有多少在 GLB 中出现。

### 2. Object Precision

[
Precision_{obj} = \frac{|O_{json} \cap O_{glb}|}{|O_{glb}|}
]

GLB 中是否出现了 JSON 没定义的多余对象。

### 3. Category Accuracy

[
Acc_{cat} = \frac{1}{N} \sum_i \mathbf{1}[c_i^{json}=c_i^{glb}]
]

### 4. Position Error

[
E_{pos} = \frac{1}{N}\sum_i |p_i^{json} - p_i^{glb}|_2
]

### 5. Scale Error

[
E_{scale} = \frac{1}{N}\sum_i \left|\frac{s_i^{glb}}{s_i^{json}} - 1\right|
]

### 6. Orientation Error

[
E_{\theta} = \frac{1}{N}\sum_i |\theta_i^{json} - \theta_i^{glb}|
]

输出：

```json
{
  "json_glb_consistency": {
    "object_recall": 0.96,
    "object_precision": 0.94,
    "category_accuracy": 0.98,
    "mean_position_error_m": 0.12,
    "mean_scale_error": 0.05,
    "mean_orientation_error_deg": 4.2
  }
}
```

这一层的作用是：

> 评价生成是否忠实执行 layout。

这对你们的 road generation pipeline 非常关键。

---

## Layer 3：Geometry / Topology Validity Metrics

输入：`scene.glb`

这层评价真实三维几何是否合法。

建议至少加入：

### 1. Mesh Integrity

* non-manifold edge ratio
* flipped normal ratio
* degenerate triangle ratio
* duplicate face ratio
* disconnected component count

### 2. Collision / Penetration

* furniture vs sidewalk clear path collision
* tree/lamp/bench vs road surface conflict
* asset vs building clearance lane conflict
* floating object count
* underground object count

### 3. Walkable Surface Continuity

把 sidewalk mesh 投影到 XZ 平面，构建 walkable polygon graph：

[
G_W = (V_W, E_W)
]

检查：

* sidewalk 是否连通；
* crosswalk 是否连接两侧 sidewalk；
* corner sidewalk 是否断裂；
* junction 是否有 hole；
* clear path 是否被阻塞。

可输出：

```json
{
  "walkable_connectivity": 0.91,
  "blocked_clear_path_ratio": 0.08,
  "sidewalk_gap_count": 2,
  "crosswalk_connection_success": 0.75
}
```

### 4. Lane Geometry Validity

尤其针对你们道路生成任务：

* lane width deviation；
* road boundary self-intersection；
* turning radius smoothness；
* centerline-boundary consistency；
* sidewalk corner continuity；
* furnishing lane continuity；
* building clearance lane violation。

这比单纯 Walkability 更贴近你们的研究目标。

---

## Layer 4：Perceptual Rendering Metrics

输入：`screenshots / rendered video / multi-view images`

这层才使用 LLM 或 VLM。

建议固定相机协议，而不是随便截图：

```text
View 1: bird-eye orthographic
View 2: pedestrian view, left sidewalk
View 3: pedestrian view, right sidewalk
View 4: junction view
View 5: longitudinal street view
```

评价：

* human scale
* visual coherence
* material consistency
* clutter
* lighting impression
* safety perception
* aesthetic order

LLM 输出只能作为：

```json
{
  "visual_perception_score": 0.71,
  "needs_human_review": true
}
```

不建议让它主导整体分。

---

## 4. 建议新的总分结构

我建议不要继续直接：

[
0.45W + 0.35S + 0.20B
]

而是改成：

[
Score =
0.25S_{layout}

* 0.30S_{consistency}
* 0.30S_{geometry}
* 0.15S_{visual}
  ]

其中：

| 分数                | 来源                    | 作用        |
| ----------------- | --------------------- | --------- |
| (S_{layout})      | layout JSON           | 设计语义是否合理  |
| (S_{consistency}) | JSON + GLB            | 生成是否忠实    |
| (S_{geometry})    | GLB mesh              | 几何/拓扑是否合法 |
| (S_{visual})      | screenshots + LLM/VLM | 感知质量      |

如果你们是做“道路生成”论文，我甚至建议进一步提高 geometry 权重：

[
Score =
0.20S_{layout}

* 0.30S_{consistency}
* 0.35S_{geometry}
* 0.15S_{visual}
  ]

因为道路生成的核心不是“看起来美”，而是：

> layout 是否被正确实例化为几何连续、可通行、无冲突、可渲染的 3D 街道场景。

---

## 5. 当前指标中建议立即修改的点

### 1. `FURN_D` 拆分

当前 `FURN_D = count_score + area_score` 的混合不够清楚。建议拆成：

```text
amenity_service_density_score
furniture_occupation_penalty
clear_path_conflict_penalty
```

不要把设施越多、面积越大都视为正向。

---

### 2. `LIGHT_UNI` 少于 2 根灯不应直接 1.0

文档显示当前少于 2 根灯杆时直接给 1.0。

这不科学。没有灯或者只有一根灯，不应该认为 lighting uniformity 完美。

建议：

```python
if lamp_count == 0:
    light_uni = 0.0
elif lamp_count == 1:
    light_uni = 0.3
else:
    light_uni = clamp(1 - cv)
```

或者拆分成：

```text
lighting_adequacy
lighting_uniformity
```

数量足够和分布均匀应该分开。

---

### 3. `TREE_SHADE` 需要区分“遮荫”和“阻塞”

树冠覆盖高是好事，但树干/树池可能阻塞人行净宽。建议同时计算：

```text
tree_canopy_coverage_score
tree_trunk_clear_path_conflict_penalty
```

---

### 4. Safety 的 LLM 权重应降低

当前 LLM 占 Safety 60%。建议降到 15–20%。

安全评价应该由几何冲突、可视域、过街暴露、缓冲隔离、照明覆盖主导。

---

### 5. Beauty 不应进入核心总分太高

如果目标是“道路生成系统”，Beauty 可以是副指标。否则模型可能被引导去生成“好看的街道”，而不是“几何正确、通行合理的街道”。

---

### 6. 补上 `top_contributors`

文档说明 `WalkabilityIndicators.top_contributors` 字段存在，但当前 ext 实现没有填充，接口返回空数组。

这个应该补。否则评分不可解释。

建议输出：

```json
{
  "top_positive_contributors": [
    ["tree_shade", 0.91],
    ["sid_clr", 0.84],
    ["poi_mix", 0.78]
  ],
  "top_negative_contributors": [
    ["cross_prov", 0.22],
    ["light_uni", 0.31],
    ["buffer_ratio", 0.36]
  ]
}
```

---

## 6. 最终判断

### 当前体系可以保留吗？

可以。它适合作为：

```text
layout-level urban quality heuristic evaluator
```

也就是：

> 给一个初步街道方案，快速估计 walkability / safety / beauty 的大致水平。

---

### 当前体系能否作为科学评价？

还不能。

主要原因：

1. **过度依赖 layout JSON，缺少 GLB 几何验证。**
2. **缺少 JSON → GLB fidelity 评价。**
3. **Safety / Beauty 过度依赖截图 LLM。**
4. **权重没有经验校准或专家验证。**
5. **部分指标单调递增，不处理过量设计。**
6. **没有道路生成任务核心指标：lane continuity、junction correctness、turning smoothness、sidewalk corner validity。**

---

## 7. 最小可行改造方案

不需要推倒重来。建议按优先级补 6 个指标模块：

```text
Priority 1: JSON–GLB object consistency
Priority 2: asset position / scale / orientation error
Priority 3: mesh collision and floating-object check
Priority 4: sidewalk clear-path obstruction
Priority 5: lane / sidewalk / junction topology continuity
Priority 6: fixed multi-view rendering + LLM visual score
```

这样你们的评价体系会从：

```text
城市设计启发式评分
```

升级为：

```text
Layout semantic quality
+ 3D generation fidelity
+ geometry validity
+ perceptual rendering quality
```

这才更适合你们现在的“layout JSON → GLB 街道场景生成”任务。
