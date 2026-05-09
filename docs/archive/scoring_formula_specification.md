# RoadGen3D 评分公式规范

> 版本：v1.0  
> 更新日期：2026-04-12

本文档详细定义了 RoadGen3D 中街道场景的三维评分体系：**步行性（Walkability）**、**安全性（Safety）**、**美观性（Beauty）**，以及最终的**综合评分（Evaluation Score）**。所有公式均基于 `scene_layout.json` 中的结构化数据，兼顾可解释性与城市设计文献依据。

---

## 1. 综合评分

```
EvaluationScore = 0.45 * WalkabilityIndex
                + 0.35 * SafetyScore
                + 0.20 * BeautyScore
```

三个维度的输出范围均为 `[0, 1]`，因此综合评分亦落在 `[0, 1]`。在 UI 报表中通常乘以 100 展示为 0–100 分。

---

## 2. 步行性（Walkability）

### 2.1 理论基础

步行性指标基于 **Protection–Comfort–Delight（保护-舒适-愉悦）** 框架，综合了 Cervero & Kockelman（1997）的 3D 原则、Gehl（2010）的步行友好理论，以及 ITDP《Pedestrians First》的定量建议。

### 2.2 11 项基础指标

所有指标均归一化至 `[0, 1]`。

#### SID_CLR — 有效人行道净宽

- **定义**：去除障碍后的人行道可通行净宽。
- **公式**：
  ```
  clear_width = mean(left_clear_path_width_m, right_clear_path_width_m)
  SID_CLR = clamp((clear_width - 1.8) / (3.2 - 1.8), 0, 1)
  ```
- **数据来源**：`summary.sidewalk_width_m`、`summary.left_clear_path_width_m`、`summary.right_clear_path_width_m`。
- **文献**：Gehl 2010；NACTO 2013《Urban Street Design Guide》。

#### CLEAR_CONT — 无障碍连续性

- **定义**：clear-path 面积占整个人行道面积的比率。
- **公式**：
  ```
  clear_area = length_m * (left_clear + right_clear)
  sidewalk_area = length_m * sidewalk_width_m * 2
  CLEAR_CONT = clamp(clear_area / sidewalk_area, 0, 1)
  ```
- **文献**：ADA Standards 2010。

#### FURN_D — 步行设施密度

- **定义**：bench、lamp、trash、bus_stop、mailbox、hydrant 的线密度。
- **公式**：
  ```
  amenities_per_m = count(amenities) / length_m
  FURN_D = clamp(amenities_per_m / 0.15, 0, 1)
  ```
- **文献**：Gehl 2010；CPTED。

#### LIGHT_UNI — 照明均匀性

- **定义**：灯杆沿街道纵向（x 轴）分布的均匀度。
- **公式**：
  ```
  gaps = sorted(lamp_x[i+1] - lamp_x[i])
  CV(gaps) = std(gaps) / mean(gaps)
  LIGHT_UNI = clamp(1 - CV(gaps), 0, 1)
  ```
- **文献**：Crowe 2000《CPTED》。

#### TREE_SHADE — 树冠遮荫率

- **定义**：树冠投影面积占人行道面积的比例。
- **公式**：
  ```
  canopy_area = tree_count * (3.6 * 3.6)
  sidewalk_area = 2 * sidewalk_width_m * length_m
  TREE_SHADE = clamp(canopy_area / sidewalk_area, 0, 1)
  ```
- **文献**：ITDP 2018。

#### BUFFER_RATIO — 行人缓冲强度

- **定义**：左右家具/植被带总宽度与车行道宽度的比率。
- **公式**：
  ```
  BUFFER_RATIO = clamp((left_furnishing_width_m + right_furnishing_width_m) / road_width_m, 0, 1)
  ```
- **文献**：NACTO《Urban Street Design Guide》。

#### TRANSIT_PROX — 公交可达性

- **定义**：人行道中心到最近公交站的距离衰减得分。
- **公式**：
  ```
  d_min = min EuclideanDistance(sidewalk_center, bus_stop)
  TRANSIT_PROX = exp(-d_min / 60)
  ```
- **文献**：Ewing & Cervero 2010（D4：Transit Accessibility）。

#### CROSS_PROV — 人行横道供给

- **定义**：过街设施数量与街段长度的配比。
- **公式**：
  ```
  target = length_m / 80
  CROSS_PROV = clamp(crossings / target, 0, 1)
  ```
- **文献**：ITDP《Pedestrians First》。

#### ENTR_DENS — 入口密度

- **定义**：沿街建筑入口的线密度。
- **公式**：
  ```
  ENTR_DENS = clamp((entrance_count / length_m) / 0.04, 0, 1)
  ```
- **文献**：Jacobs 1961；Ewing & Handy 2009。

#### POI_MIX — 场所多样性

- **定义**：基于 Shannon 熵的 POI 功能混合度。
- **公式**：
  ```
  p_i = count_i / total_count
  H = -sum(p_i * log(p_i))
  POI_MIX = clamp(H / log(K), 0, 1)
  ```
  其中 `K` 为实际观测到的类别数。
- **文献**：Cervero & Kockelman 1997；Montgomery 1998。

#### MICRO_ENV — 微气候综合

- **定义**：树荫、噪声屏蔽与入口开敞度的加权综合。
- **公式**：
  ```
  MICRO_ENV = clamp(0.5 * TREE_SHADE + 0.3 * mean_noise_shielding + 0.2 * mean_entrance_openness, 0, 1)
  ```
- **文献**：Gehl 2010；Frank et al. 2010。

### 2.3 三大支柱与总体步行指数

| 支柱 | 权重 | 包含指标 |
|------|------|----------|
| Protection | 0.40 | LIGHT_UNI、BUFFER_RATIO、CROSS_PROV |
| Comfort | 0.35 | SID_CLR、CLEAR_CONT、TREE_SHADE、MICRO_ENV |
| Delight | 0.25 | FURN_D、TRANSIT_PROX、ENTR_DENS、POI_MIX |

```
Protection = mean(LIGHT_UNI, BUFFER_RATIO, CROSS_PROV)
Comfort    = mean(SID_CLR, CLEAR_CONT, TREE_SHADE, MICRO_ENV)
Delight    = mean(FURN_D, TRANSIT_PROX, ENTR_DENS, POI_MIX)

WalkabilityIndex = 0.40 * Protection + 0.35 * Comfort + 0.25 * Delight
```

### 2.4 可解释性：Top Contributors

系统在计算完 `WalkabilityIndex` 后，会自动计算每个指标提升 `+0.1` 对最终指数的边际贡献，返回贡献最大的前 3 项：

```
delta_pillar = pillar_weight * (new_pillar_mean - current_pillar_mean)
delta_index  = delta_pillar
```

结果存储在 `walkability.json` 的 `top_contributors` 字段中，供前端展示改进优先级。

---

## 3. 安全性（Safety）

### 3.1 结构化特征

| 特征 | 权重（融合中） | 说明 |
|------|----------------|------|
| LIGHT_UNI | 0.15 | 与步行性共享 |
| CROSS_PROV | 0.15 | 与步行性共享 |
| BUFFER_RATIO | 0.10 | 与步行性共享 |
| BOLLARD_DENSITY | 诊断用 | bollard 线密度（上限 0.15/m） |
| VISIBILITY_PENALTY | 诊断用 | `(1 - mean_entrance_openness) * dropped_slot_rate` |

**结构性安全分**（ always computed ）：
```
StructuralSafety = 0.15*CROSS_PROV + 0.15*LIGHT_UNI + 0.10*BUFFER_RATIO
                 + 0.10*BOLLARD_DENSITY + max(0, 0.1 - VISIBILITY_PENALTY)
```

### 3.2 LLM 感知安全分

LLM 从场景俯视渲染图中评估四个子维度（0–5 分），归一化到 0–1：

- `lighting`：照明连续性
- `visibility`：视线与开敞度
- `protection`：交通物理隔离
- `activation`：活动 surveillance

```
LLM_SafetyMean = mean(lighting, visibility, protection, activation)
```

### 3.3 最终安全分

当 LLM 评分可用时：
```
SafetyScore = 0.6 * LLM_SafetyMean
            + 0.15 * CROSS_PROV
            + 0.15 * LIGHT_UNI
            + 0.10 * BUFFER_RATIO
```

当 LLM 评分不可用时，回退到 `StructuralSafety`。

### 3.4 诊断与审查标记

- `diagnosis.weakest`：得分最低的子维度（LLM 或结构化）。
- `needs_review`：若 LLM 四个子维度的标准差 `> 0.20`（即 0–5 分制下差异 > 1.0），则标记需要人工复核。

---

## 4. 美观性（Beauty）

### 4.1 结构化特征

| 特征 | 权重（融合中） | 说明 |
|------|----------------|------|
| presentation_score | 0.40 | 渲染材质/光影/构图综合分 |
| active_front_ratio | 0.10 | 活跃界面沿街占比 |
| anchor_poi_score | 0.10 | 加权锚点 POI 密度 |
| style_coherence | 诊断用 | 材质风格一致性 |
| visual_clutter | 诊断用 | 视觉杂乱度（越低越好） |
| spacing_rhythm | 诊断用 | 家具节奏感 |
| focal_readability | 诊断用 | 焦点清晰度 |

**结构性美观分**：
```
StructuralBeauty = 0.4 * presentation_score
                 + 0.1 * active_front_ratio
                 + 0.1 * anchor_poi_score
                 + 0.1 * (1 - visual_clutter)
```

### 4.2 LLM 感知美观分

LLM 从渲染图中评估四个子维度（0–5 分），归一化到 0–1：

- `coherence`：视觉一致性
- `human_scale`：人性化尺度
- `material_contrast`：材质对比美感
- `visual_interest`：视觉兴趣度

```
LLM_BeautyMean = mean(coherence, human_scale, material_contrast, visual_interest)
```

### 4.3 最终美观分

当 LLM 评分可用时：
```
BeautyScore = 0.4 * LLM_BeautyMean
            + 0.4 * presentation_score
            + 0.1 * active_front_ratio
            + 0.1 * anchor_poi_score
```

当 LLM 评分不可用时，回退到 `StructuralBeauty`。

### 4.4 诊断与审查标记

- `diagnosis.weakest`：得分最低的子维度。
- `needs_review`：若 LLM 子维度标准差 `> 0.20`，标记需要复核。

---

## 5. 数据归一化函数

所有指标统一使用 clamp 函数：

```python
def clamp(value, lo=0.0, hi=1.0):
    return max(lo, min(hi, value))
```

对于理论上无上限的密度类指标，采用目标值除法规一化（如 `FURN_D` 除以 `0.15/m`）。该目标值来源于 CPTED 与 NACTO 的工程建议，并在系统中被视为“理想值”。

---

## 6. 文献索引

| 文献 | 在指标中的体现 |
|------|----------------|
| Cervero & Kockelman (1997) | POI_MIX、3D 框架 |
| Crowe (2000) CPTED | LIGHT_UNI、BOLLARD_DENSITY |
| Ewing & Cervero (2010) | TRANSIT_PROX |
| Ewing & Handy (2009) | ENTR_DENS、视觉指标 |
| Frank et al. (2010) | MICRO_ENV |
| Gehl (2010) | SID_CLR、FURN_D、TREE_SHADE |
| ITDP (2018) | TREE_SHADE、CROSS_PROV |
| Jacobs (1961) | ENTR_DENS、active_front_ratio |
| Montgomery (1998) | POI_MIX、场所活力 |
| Nasar (1994) | focal_readability |
| NACTO (2013) | SID_CLR、BUFFER_RATIO、CROSS_PROV |

---

## 7. 版本记录

| 版本 | 日期 | 变更说明 |
|------|------|----------|
| v1.0 | 2026-04-12 | 初始发布，整合 LLM 安全/美观评分、Top Contributors 与诊断机制 |
