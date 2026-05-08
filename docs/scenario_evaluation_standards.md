# RoadGen3D 七方案评价标准

本文档为 `data/scenario_designs/hkust_gz_gate_scenarios.json` 中七个场景方案建立评价标准。这里采用更轻量的做法：不新增 `Scenario Compliance` 维度，只使用项目当前已有或已经规划的 evaluation 指标，按不同方案调整指标阈值、维度权重和总分标准。

## 1. 评价结构

每个方案统一使用三维度：

| 维度 | 含义 | 主要指标 |
|---|---|---|
| Walkability | 步行连续性、净空、可达性、舒适性 | `SID_CLR`, `CLEAR_CONT`, `FURN_D`, `TREE_SHADE`, `TRANSIT_PROX`, `ENTR_DENS`, `POI_MIX`, `MICRO_ENV` |
| Safety | 过街安全、照明、缓冲、防护、视线 | `LIGHT_UNI`, `CROSS_PROV`, `BUFFER_RATIO`, `BOLLARD_DENSITY`, `VISIBILITY_PENALTY` |
| Place Quality | 美观、活动、界面活力、空间秩序 | `presentation_score`, `active_front_ratio`, `anchor_poi_score`, `style_coherence`, `spacing_rhythm`, `focal_readability`, `visual_clutter` |

总分：

```text
TotalScore = wW * Walkability + wS * Safety + wP * PlaceQuality
```

所有分值统一为 `[0, 1]`。UI 展示时可乘以 100。

## 2. 等级判定

| 等级 | 条件 |
|---|---|
| Pass | `TotalScore >= target_total` 且关键指标无严重失败 |
| Review | `TotalScore >= minimum_total` 但低于 target，或关键指标接近下限 |
| Fail | `TotalScore < minimum_total`，或安全关键指标严重不达标 |

默认标准：

| 项 | 标准值 |
|---|---:|
| `minimum_total` | 0.60 |
| `target_total` | 0.75 |
| `excellent_total` | 0.85 |

## 3. 阈值来源与校准说明

本文档中的 `minimum / target / excellent` 不是从单一规范条文直接抄得的固定值，而是三类依据的组合：

| 来源类型 | 说明 | 适用指标 |
|---|---|---|
| Code-derived | 项目代码已把原始物理量归一化到 `[0, 1]`，阈值按代码公式解释。 | `SID_CLR`, `CLEAR_CONT`, `TREE_SHADE`, `BUFFER_RATIO`, `TRANSIT_PROX`, `CROSS_PROV`, `ENTR_DENS`, `POI_MIX`, `MICRO_ENV` |
| Guideline-informed | 文献和指南给出明确设计方向或物理建议，但不一定给 0-1 分数；本文把这些原则转译为项目指标阈值。 | 学校安全、道路瘦身、中央安全岛、公交停靠、共享街道、绿化街道相关指标 |
| Heuristic-to-calibrate | 当前缺少真实标注数据或专家评分，阈值是工程化建议，后续应通过专家打分、案例库或生成样本分布校准。 | `presentation_score`, `active_front_ratio`, `anchor_poi_score`, `style_coherence`, `spacing_rhythm`, `focal_readability`, `visual_clutter` |

示例：`SID_CLR` 在现有实现中大致按 `1.8m` 到 `3.2m` 映射到 `[0, 1]`，因此 `SID_CLR=0.75` 表示人行净空已接近较高舒适水平。`CROSS_PROV` 按过街点数量相对目标间距归一化，因此学校和中央安全岛方案会设置更高 target。`presentation_score` 等视觉指标目前没有强规范阈值，因此只作为待校准的经验标准。

建议在论文或报告中表述为：

```text
The proposed thresholds are scenario-specific heuristic standards derived from RoadGen3D's normalized metric definitions, urban street design and pedestrian safety guidelines, and the intended design priorities of each scenario. They should be calibrated in future work using expert ratings or benchmark scene datasets.
```

中文表述：

```text
本文档中的 minimum / target / excellent 阈值并非直接来自单一规范条文，而是基于 RoadGen3D 当前归一化指标定义、城市设计与交通安全指南中的方向性要求，以及各方案设计目标进行工程化设定。后续应通过专家评分、真实案例或生成样本分布进一步校准。
```

## 4. 指标说明与通用标准

### 4.1 Walkability

| Indicator | 含义 | 通用 minimum | 通用 target |
|---|---|---:|---:|
| `SID_CLR` | 人行净空宽度 | 0.60 | 0.75 |
| `CLEAR_CONT` | 净空连续性 | 0.65 | 0.80 |
| `FURN_D` | 街道家具密度 | 0.35 | 0.60 |
| `TREE_SHADE` | 树冠遮荫 | 0.35 | 0.55 |
| `TRANSIT_PROX` | 公交可达性 | 0.35 | 0.65 |
| `ENTR_DENS` | 沿街入口密度 | 0.30 | 0.50 |
| `POI_MIX` | POI/业态混合度 | 0.35 | 0.60 |
| `MICRO_ENV` | 微环境舒适性 | 0.40 | 0.60 |

### 4.2 Safety

| Indicator | 含义 | 通用 minimum | 通用 target |
|---|---|---:|---:|
| `LIGHT_UNI` | 照明均匀度 | 0.60 | 0.75 |
| `CROSS_PROV` | 过街设施供给 | 0.45 | 0.65 |
| `BUFFER_RATIO` | 慢行与车行缓冲 | 0.25 | 0.40 |
| `BOLLARD_DENSITY` | 护柱/隔离设施密度 | 0.20 | 0.45 |
| `VISIBILITY_PENALTY` | 视线惩罚，越低越好 | <=0.25 | <=0.15 |

### 4.3 Place Quality

| Indicator | 含义 | 通用 minimum | 通用 target |
|---|---|---:|---:|
| `presentation_score` | 方案呈现质量 | 0.50 | 0.68 |
| `active_front_ratio` | 活跃界面比例 | 0.30 | 0.50 |
| `anchor_poi_score` | 锚点 POI 活力 | 0.30 | 0.50 |
| `style_coherence` | 风格一致性 | 0.50 | 0.70 |
| `spacing_rhythm` | 空间节奏 | 0.50 | 0.68 |
| `focal_readability` | 重点设施可读性 | 0.45 | 0.65 |
| `visual_clutter` | 视觉杂乱，越低越好 | <=0.50 | <=0.35 |

## 5. 七个方案标准

### 5.1 方案 1：道路瘦身完整街道

`scenario_01_basic_complete_street`

设计意图：四车道压缩为双向两车道，把外侧车道转为骑行空间，并扩大慢行空间。

维度标准：

| 维度 | 权重 | minimum | target | excellent |
|---|---:|---:|---:|---:|
| Walkability | 0.35 | 0.65 | 0.78 | 0.88 |
| Safety | 0.35 | 0.65 | 0.78 | 0.88 |
| Place Quality | 0.30 | 0.55 | 0.70 | 0.82 |
| Total | 1.00 | 0.65 | 0.78 | 0.88 |

指标标准：

| Indicator | minimum | target | excellent | 说明 |
|---|---:|---:|---:|---|
| `SID_CLR` | 0.65 | 0.78 | 0.92 | 道路瘦身后慢行空间应明显提升 |
| `CLEAR_CONT` | 0.75 | 0.85 | 0.95 | 骑行和家具不能阻断人行净空 |
| `FURN_D` | 0.35 | 0.55 | 0.75 | 提供基础家具但不过度占用空间 |
| `TREE_SHADE` | 0.35 | 0.50 | 0.70 | 回收空间应改善舒适性 |
| `TRANSIT_PROX` | 0.30 | 0.55 | 0.75 | 完整街道保留基本公交可达 |
| `ENTR_DENS` | 0.30 | 0.50 | 0.70 | 支撑两侧街道界面 |
| `POI_MIX` | 0.45 | 0.62 | 0.80 | 校园/商业混合界面 |
| `MICRO_ENV` | 0.45 | 0.60 | 0.78 | 遮荫、隔音、开放度 |
| `LIGHT_UNI` | 0.60 | 0.72 | 0.85 | 夜间慢行安全 |
| `CROSS_PROV` | 0.60 | 0.72 | 0.88 | 道路瘦身应改善过街 |
| `BUFFER_RATIO` | 0.30 | 0.45 | 0.65 | 骑行/步行与车行隔离 |
| `BOLLARD_DENSITY` | 0.20 | 0.40 | 0.65 | 冲突点保护 |
| `VISIBILITY_PENALTY` | <=0.25 | <=0.15 | <=0.08 | 慢行空间不可被遮挡 |
| `presentation_score` | 0.50 | 0.65 | 0.80 | 道路瘦身后的视觉秩序 |
| `active_front_ratio` | 0.35 | 0.52 | 0.72 | 回收空间是否形成活动界面 |
| `spacing_rhythm` | 0.50 | 0.66 | 0.82 | 树木、灯具、家具节奏 |

关键检查：`lane_count` 应接近 2；骑行空间应在布局或 surface annotation 中明确表达。该检查作为解释性 gate，不单独计入第四维度。

主要阈值理由：

- `SID_CLR` 和 `CLEAR_CONT` 高于通用标准，因为道路瘦身的核心收益是把车行空间转化为连续慢行空间；该判断来自 RoadGen3D 净空归一化公式和 FHWA Road Diet 的空间再分配原则。
- `CROSS_PROV` 和 `BUFFER_RATIO` 高于通用标准，因为道路瘦身应同步改善过街安全和慢行隔离，而不是只减少车道数；依据 FHWA Road Diet 与 NACTO complete street 设计原则。
- `FURN_D` 没有设置过高 target，因为方案 1 的目标是完整街道基本家具，而不是活动设施密集街道；过高家具密度会与 `CLEAR_CONT` 冲突。
- `active_front_ratio`、`POI_MIX` 属于经验阈值，依据 Jacobs 和 Global Street Design Guide 对混合使用与街道活力的原则，后续需用案例或专家评分校准。

### 5.2 方案 2：四车道慢行强化与中央安全岛

`scenario_02_four_lane_multimodal_safety_island`

设计意图：保持四车道通行能力，同时通过中央安全岛和双侧慢行空间强化安全。

维度标准：

| 维度 | 权重 | minimum | target | excellent |
|---|---:|---:|---:|---:|
| Walkability | 0.25 | 0.62 | 0.75 | 0.85 |
| Safety | 0.50 | 0.70 | 0.82 | 0.92 |
| Place Quality | 0.25 | 0.50 | 0.65 | 0.80 |
| Total | 1.00 | 0.66 | 0.80 | 0.90 |

指标标准：

| Indicator | minimum | target | excellent | 说明 |
|---|---:|---:|---:|---|
| `SID_CLR` | 0.62 | 0.75 | 0.88 | 保持四车道时仍需慢行空间 |
| `CLEAR_CONT` | 0.72 | 0.84 | 0.94 | 安全岛和家具不能破坏连续通行 |
| `FURN_D` | 0.30 | 0.50 | 0.70 | 基础家具即可 |
| `TREE_SHADE` | 0.45 | 0.62 | 0.78 | 中央岛与两侧慢行微气候 |
| `TRANSIT_PROX` | 0.25 | 0.45 | 0.70 | 非主目标，保留基本可达 |
| `ENTR_DENS` | 0.25 | 0.45 | 0.65 | 沿街界面支持 |
| `POI_MIX` | 0.35 | 0.55 | 0.75 | 林荫慢行和公交商业界面 |
| `MICRO_ENV` | 0.55 | 0.70 | 0.85 | 绿化、隔音和开放度 |
| `LIGHT_UNI` | 0.68 | 0.80 | 0.90 | 四车道场景的夜间安全 |
| `CROSS_PROV` | 0.70 | 0.82 | 0.95 | 中央安全岛必须服务过街 |
| `BUFFER_RATIO` | 0.32 | 0.48 | 0.65 | 车行与慢行隔离 |
| `BOLLARD_DENSITY` | 0.30 | 0.52 | 0.75 | 安全岛和路缘保护 |
| `VISIBILITY_PENALTY` | <=0.20 | <=0.15 | <=0.08 | 中央绿化/安全岛不能挡视线 |
| `presentation_score` | 0.45 | 0.62 | 0.78 | 中央岛视觉连续性 |
| `spacing_rhythm` | 0.50 | 0.66 | 0.82 | 中央树阵/两侧设施节奏 |

关键检查：`lane_count` 应接近 4；应存在 median / safety island；中央岛不应造成严重视线惩罚。

主要阈值理由：

- Safety 权重和 `CROSS_PROV` target 明显提高，因为中央安全岛的主要价值是降低过街暴露风险；依据 FHWA median / pedestrian refuge island 与 NACTO pedestrian safety island 指南。
- `VISIBILITY_PENALTY` 设置严格上限，因为中央岛和绿化不能遮挡驾驶员与行人的相互可见性；这是安全岛设计的关键约束。
- `TREE_SHADE`、`MICRO_ENV` 比通用街道略高，因为方案文本明确要求可种植中央安全岛和慢行强化。
- `TRANSIT_PROX` 不作为高标准指标，因为该方案核心是四车道条件下的慢行安全，而非公交导向。

### 5.3 方案 3：公交停靠与混合界面

`scenario_03_school_commercial_mixed_frontage`

设计意图：校园、商业、居住混合界面下，强化公交停靠、站台铺装和多模式接驳。

维度标准：

| 维度 | 权重 | minimum | target | excellent |
|---|---:|---:|---:|---:|
| Walkability | 0.30 | 0.62 | 0.75 | 0.86 |
| Safety | 0.30 | 0.65 | 0.78 | 0.90 |
| Place Quality | 0.40 | 0.55 | 0.72 | 0.86 |
| Total | 1.00 | 0.66 | 0.80 | 0.90 |

指标标准：

| Indicator | minimum | target | excellent | 说明 |
|---|---:|---:|---:|---|
| `SID_CLR` | 0.62 | 0.76 | 0.90 | 公交站周边净空 |
| `CLEAR_CONT` | 0.72 | 0.84 | 0.94 | 站台和骑行铺装不阻断行走 |
| `FURN_D` | 0.40 | 0.62 | 0.82 | 候车、停留和商业街家具 |
| `TREE_SHADE` | 0.30 | 0.50 | 0.70 | 候车舒适性 |
| `TRANSIT_PROX` | 0.60 | 0.78 | 0.92 | 本方案核心指标 |
| `ENTR_DENS` | 0.35 | 0.55 | 0.78 | 校园和商住入口服务公交 |
| `POI_MIX` | 0.50 | 0.68 | 0.85 | 校园、商业、居住混合 |
| `MICRO_ENV` | 0.45 | 0.62 | 0.78 | 候车微环境 |
| `LIGHT_UNI` | 0.70 | 0.82 | 0.92 | 候车安全 |
| `CROSS_PROV` | 0.55 | 0.70 | 0.88 | 到达公交站的安全过街 |
| `BUFFER_RATIO` | 0.30 | 0.45 | 0.62 | 公交/骑行/步行分隔 |
| `BOLLARD_DENSITY` | 0.20 | 0.42 | 0.65 | 站台边界和骑行冲突保护 |
| `VISIBILITY_PENALTY` | <=0.22 | <=0.15 | <=0.08 | 公交停靠段视线安全 |
| `presentation_score` | 0.55 | 0.72 | 0.86 | 公交节点表达 |
| `active_front_ratio` | 0.40 | 0.58 | 0.78 | 商住混合界面活力 |
| `anchor_poi_score` | 0.45 | 0.65 | 0.85 | 公交站服务目标地 |
| `focal_readability` | 0.50 | 0.68 | 0.84 | 站台、公交停靠和骑行引导可读 |

关键检查：应存在公交停靠段和站台候车铺装；这些作为场景语义检查，不额外增加维度。

主要阈值理由：

- `TRANSIT_PROX` 设置为核心高标准，因为公交停靠与换乘便利是方案 3 的主要目标；该指标由项目距离衰减公式计算，阈值对应较近且可达的公交服务。
- `LIGHT_UNI` 和 `VISIBILITY_PENALTY` 更严格，因为公交站涉及候车安全、夜间安全和人车交互；依据 NACTO Transit Street Design Guide 与 TCRP Report 19。
- `active_front_ratio`、`anchor_poi_score` 高于通用标准，因为混合界面需要校园、商业、居住目标地支撑公交使用和街道活力。
- `FURN_D` 目标高于方案 1，因为公交站和商业界面需要候车、停留和街道家具，但仍受 `CLEAR_CONT` 限制。

### 5.4 方案 4：儿童友好型学校走廊

`scenario_04_child_friendly_school_corridor`

设计意图：学校侧步行空间扩大，设置儿童彩色步道、彩色过街和安全岛。

维度标准：

| 维度 | 权重 | minimum | target | excellent |
|---|---:|---:|---:|---:|
| Walkability | 0.30 | 0.70 | 0.82 | 0.92 |
| Safety | 0.55 | 0.75 | 0.88 | 0.95 |
| Place Quality | 0.15 | 0.55 | 0.70 | 0.84 |
| Total | 1.00 | 0.70 | 0.84 | 0.93 |

指标标准：

| Indicator | minimum | target | excellent | 说明 |
|---|---:|---:|---:|---|
| `SID_CLR` | 0.70 | 0.85 | 0.95 | 学校侧儿童和家长通行 |
| `CLEAR_CONT` | 0.78 | 0.88 | 0.96 | 上下学高峰连续通行 |
| `FURN_D` | 0.30 | 0.50 | 0.70 | 等候、休息和导向设施 |
| `TREE_SHADE` | 0.40 | 0.60 | 0.78 | 学校侧舒适性 |
| `TRANSIT_PROX` | 0.25 | 0.45 | 0.70 | 可选，但校门可达性有益 |
| `ENTR_DENS` | 0.30 | 0.50 | 0.70 | 学校入口和支持商业界面 |
| `POI_MIX` | 0.35 | 0.55 | 0.75 | 学校、儿童活动、支持商业 |
| `MICRO_ENV` | 0.50 | 0.68 | 0.84 | 儿童步行舒适 |
| `LIGHT_UNI` | 0.80 | 0.90 | 0.96 | 儿童安全关键指标 |
| `CROSS_PROV` | 0.80 | 0.90 | 1.00 | 学校门前过街安全 |
| `BUFFER_RATIO` | 0.35 | 0.55 | 0.72 | 儿童与机动车隔离 |
| `BOLLARD_DENSITY` | 0.35 | 0.60 | 0.82 | 校门和过街保护 |
| `VISIBILITY_PENALTY` | <=0.15 | <=0.10 | <=0.05 | 儿童身高视线更敏感 |
| `presentation_score` | 0.50 | 0.68 | 0.82 | 彩色步道与过街表达 |
| `focal_readability` | 0.60 | 0.78 | 0.90 | 儿童可理解的空间导向 |
| `visual_clutter` | <=0.40 | <=0.30 | <=0.18 | 避免复杂混乱环境 |

关键检查：学校侧彩色步道、学校门前彩色过街、安全岛应存在；缺失时即使总分尚可也应进入 Review 或 Fail。

主要阈值理由：

- Safety 权重最高，`LIGHT_UNI`、`CROSS_PROV`、`VISIBILITY_PENALTY` 的 target 均高于一般街道，因为儿童友好学校走廊是安全敏感场景；依据 FHWA Safe Routes to School 与 GDCI Designing Streets for Kids。
- `SID_CLR` 和 `CLEAR_CONT` 高标准是为了容纳儿童、家长、轮椅、婴儿车和上下学高峰人流；对应 AASHTO/NACTO 对无障碍与清晰步行空间的要求。
- `focal_readability` 和 `visual_clutter` 被显式约束，因为儿童场景需要清楚、易理解、低混乱的空间提示；依据 Designing Streets for Kids 和环境认知相关文献。
- `TRANSIT_PROX` 不是核心高标准，因为该方案重点是学校侧步行安全，而非公交换乘。

### 5.5 方案 5：街道家具与社区活动增强

`scenario_05_furniture_enriched_activity_street`

当前状态：`enabled=false`。该方案需要更多自由功能区和活动设施，当前可先用现有指标评估其倾向，不建议作为正式自动生成排名依据。

维度标准：

| 维度 | 权重 | minimum | target | excellent |
|---|---:|---:|---:|---:|
| Walkability | 0.30 | 0.62 | 0.75 | 0.86 |
| Safety | 0.20 | 0.60 | 0.72 | 0.84 |
| Place Quality | 0.50 | 0.65 | 0.82 | 0.92 |
| Total | 1.00 | 0.64 | 0.78 | 0.90 |

指标标准：

| Indicator | minimum | target | excellent | 说明 |
|---|---:|---:|---:|---|
| `SID_CLR` | 0.62 | 0.75 | 0.88 | 活动设施不能挤占行走空间 |
| `CLEAR_CONT` | 0.65 | 0.78 | 0.90 | 家具增强的底线约束 |
| `FURN_D` | 0.70 | 0.85 | 1.00 | 本方案核心指标 |
| `TREE_SHADE` | 0.35 | 0.55 | 0.75 | 停留舒适性 |
| `TRANSIT_PROX` | 0.30 | 0.50 | 0.72 | 社区活动可达性 |
| `ENTR_DENS` | 0.45 | 0.65 | 0.85 | 活动界面和人流来源 |
| `POI_MIX` | 0.70 | 0.82 | 0.95 | 多功能社区活动 |
| `MICRO_ENV` | 0.45 | 0.65 | 0.82 | 停留微环境 |
| `LIGHT_UNI` | 0.60 | 0.75 | 0.88 | 夜间活动和社区安全 |
| `CROSS_PROV` | 0.50 | 0.68 | 0.85 | 活动空间之间安全连接 |
| `BUFFER_RATIO` | 0.25 | 0.40 | 0.60 | 活动节点与车流隔离 |
| `BOLLARD_DENSITY` | 0.20 | 0.40 | 0.65 | 活动边界保护 |
| `VISIBILITY_PENALTY` | <=0.25 | <=0.18 | <=0.10 | 活动设施不能遮挡视线 |
| `presentation_score` | 0.65 | 0.80 | 0.92 | 公共空间整体表达 |
| `active_front_ratio` | 0.50 | 0.70 | 0.88 | 社区活动界面 |
| `anchor_poi_score` | 0.45 | 0.65 | 0.82 | 信息站和活动节点吸引力 |
| `style_coherence` | 0.58 | 0.75 | 0.88 | 多设施仍需风格一致 |
| `spacing_rhythm` | 0.60 | 0.75 | 0.88 | 家具布置秩序 |
| `visual_clutter` | <=0.50 | <=0.40 | <=0.25 | 丰富但不杂乱 |

关键检查：如果功能区、儿童游乐、健身站、自行车修理站等资产暂不支持，应在报告中标记为能力边界，而不是通过新增维度扣分。

主要阈值理由：

- Place Quality 权重最高，`FURN_D`、`POI_MIX`、`active_front_ratio` 和 `presentation_score` 均高于通用标准，因为该方案目标是社区活动增强，而不是单纯通行效率。
- `CLEAR_CONT` 保持较高下限，是为了防止家具和活动设施占用通行净空；这是把 Whyte/Gehl 的停留活动原则转译到项目净空指标后的约束。
- `visual_clutter` 设置为约束项，因为设施丰富不等于视觉混乱；Ewing & Handy 的城市设计品质研究支持复杂性与秩序之间需要平衡。
- 方案 5 当前 `enabled=false`，部分设施属于系统能力边界，因此阈值应被视为 future-ready 标准，而非当前生成失败的唯一依据。

### 5.6 方案 6：中央绿化带完整街道

`scenario_06_green_median_complete_street`

设计意图：保持四车道和两侧慢行空间，同时形成连续中央绿化带。

维度标准：

| 维度 | 权重 | minimum | target | excellent |
|---|---:|---:|---:|---:|
| Walkability | 0.25 | 0.62 | 0.75 | 0.86 |
| Safety | 0.25 | 0.65 | 0.78 | 0.90 |
| Place Quality | 0.50 | 0.68 | 0.84 | 0.94 |
| Total | 1.00 | 0.66 | 0.80 | 0.92 |

指标标准：

| Indicator | minimum | target | excellent | 说明 |
|---|---:|---:|---:|---|
| `SID_CLR` | 0.62 | 0.75 | 0.88 | 绿化带不应削弱两侧慢行 |
| `CLEAR_CONT` | 0.75 | 0.86 | 0.95 | 中央绿化与两侧连续性 |
| `FURN_D` | 0.35 | 0.55 | 0.75 | 商业停留界面家具 |
| `TREE_SHADE` | 0.60 | 0.75 | 0.90 | 本方案核心指标 |
| `TRANSIT_PROX` | 0.25 | 0.45 | 0.70 | 非主目标，保留基本可达 |
| `ENTR_DENS` | 0.30 | 0.50 | 0.70 | 商业界面活动 |
| `POI_MIX` | 0.40 | 0.60 | 0.78 | 绿化、商业、慢行复合 |
| `MICRO_ENV` | 0.60 | 0.75 | 0.88 | 绿化改善微气候 |
| `LIGHT_UNI` | 0.65 | 0.78 | 0.90 | 中央绿化带周边夜间安全 |
| `CROSS_PROV` | 0.45 | 0.65 | 0.85 | 绿化带不能阻断过街 |
| `BUFFER_RATIO` | 0.30 | 0.48 | 0.65 | 车行与慢行缓冲 |
| `BOLLARD_DENSITY` | 0.20 | 0.42 | 0.65 | 中央带和过街保护 |
| `VISIBILITY_PENALTY` | <=0.20 | <=0.15 | <=0.08 | 绿化不能遮挡 |
| `presentation_score` | 0.65 | 0.80 | 0.92 | 中央绿化街景表现 |
| `style_coherence` | 0.55 | 0.72 | 0.86 | 绿化和家具风格 |
| `spacing_rhythm` | 0.60 | 0.75 | 0.88 | 中央树阵/设施节奏 |

关键检查：应存在 `median_green` 或等价连续中央绿化表达；如果中央绿化带导致过街或视线问题，应下调 Safety。

主要阈值理由：

- Place Quality 权重最高，`TREE_SHADE`、`MICRO_ENV`、`presentation_score`、`spacing_rhythm` 目标较高，因为该方案核心是连续中央绿化带和街景品质。
- `CROSS_PROV` 和 `VISIBILITY_PENALTY` 仍保留安全下限，因为中央绿化带不能切断过街，也不能遮挡视线；依据 NACTO/FHWA 对 median refuge 和 pedestrian crossing 的要求。
- `FURN_D` 仅保持中等标准，因为该方案不是家具密集型，而是绿化和慢行空间品质导向。
- `TRANSIT_PROX` 标准较低，因为公交不是此方案核心目标。

### 5.7 方案 7：非对称共享街道与口袋公园

`scenario_07_asymmetric_shared_street_pocket_park`

当前状态：`enabled=false`。该方案需要共享街道、口袋公园、社区集市、雨水花园和不规则树阵，当前可先用现有指标评估其公共空间倾向。

维度标准：

| 维度 | 权重 | minimum | target | excellent |
|---|---:|---:|---:|---:|
| Walkability | 0.35 | 0.68 | 0.82 | 0.92 |
| Safety | 0.30 | 0.68 | 0.82 | 0.92 |
| Place Quality | 0.35 | 0.70 | 0.85 | 0.95 |
| Total | 1.00 | 0.68 | 0.82 | 0.93 |

指标标准：

| Indicator | minimum | target | excellent | 说明 |
|---|---:|---:|---:|---|
| `SID_CLR` | 0.68 | 0.82 | 0.94 | 共享街道和口袋公园慢行优先 |
| `CLEAR_CONT` | 0.72 | 0.84 | 0.94 | 非对称空间仍需连续可达 |
| `FURN_D` | 0.55 | 0.78 | 0.95 | 集市、停留、社区节点 |
| `TREE_SHADE` | 0.65 | 0.80 | 0.92 | 口袋公园和树阵 |
| `TRANSIT_PROX` | 0.25 | 0.45 | 0.70 | 可选公共交通接入 |
| `ENTR_DENS` | 0.45 | 0.65 | 0.85 | 社区活动界面 |
| `POI_MIX` | 0.75 | 0.88 | 0.98 | 集市、社区、公园复合 |
| `MICRO_ENV` | 0.60 | 0.78 | 0.90 | 绿化、雨水花园、开放度 |
| `LIGHT_UNI` | 0.68 | 0.82 | 0.92 | 共享空间夜间安全 |
| `CROSS_PROV` | 0.70 | 0.84 | 0.96 | 共享街道频繁安全穿越 |
| `BUFFER_RATIO` | 0.20 | 0.35 | 0.55 | 传统缓冲不是唯一目标，但仍需边界安全 |
| `BOLLARD_DENSITY` | 0.25 | 0.48 | 0.72 | 共享空间边界保护 |
| `VISIBILITY_PENALTY` | <=0.20 | <=0.12 | <=0.06 | 树阵/集市设施不可遮挡 |
| `presentation_score` | 0.70 | 0.84 | 0.94 | 口袋公园与共享街道表达 |
| `active_front_ratio` | 0.55 | 0.75 | 0.90 | 社区集市与界面活力 |
| `anchor_poi_score` | 0.50 | 0.70 | 0.88 | 社区节点吸引力 |
| `style_coherence` | 0.55 | 0.75 | 0.90 | 多功能空间保持整体风格 |
| `visual_clutter` | <=0.50 | <=0.38 | <=0.24 | 活跃但不混乱 |

关键检查：如果当前系统不能表达 shared street polygon、pocket park polygon 或不规则树阵，应在报告中标记为能力边界。该标记不作为第四维度，只解释为什么该方案暂不适合自动排名。

主要阈值理由：

- Walkability、Safety、Place Quality 三个维度都较高，因为共享街道与口袋公园同时要求慢行优先、低冲突和高公共空间质量。
- `POI_MIX`、`active_front_ratio`、`presentation_score` target 高，是因为社区集市、口袋公园和共享街道依赖多功能活动和沿街界面；依据 Global Street Design Guide、Jacobs 和 Whyte。
- `TREE_SHADE`、`MICRO_ENV` target 高，是因为方案包含雨水花园、不规则树阵和口袋公园，强调生态与舒适。
- `BUFFER_RATIO` 没有设置很高，因为共享街道不一定依赖传统硬隔离；但 `CROSS_PROV`、`LIGHT_UNI` 和 `VISIBILITY_PENALTY` 仍需高标准，以控制共享空间中的冲突风险。
- 方案 7 当前 `enabled=false`，因此这些阈值更适合作为未来实现 shared street / pocket park 能力后的评价目标。

## 6. 文献依据与简要内容

| 文献 | 简要内容 | 对本文档的作用 |
|---|---|---|
| FHWA, *Road diets* | 道路瘦身可通过车道重分配降低冲突，并释放空间给自行车道、人行空间、公交和安全岛。 | 支撑方案 1 的 `SID_CLR`, `BUFFER_RATIO`, `CROSS_PROV` 提升目标。 |
| FHWA, *Medians and pedestrian refuge islands* | 中央安全岛让行人分阶段过街，降低暴露在车流中的时间。 | 支撑方案 2、4 对 `CROSS_PROV`, `VISIBILITY_PENALTY`, `BOLLARD_DENSITY` 的高标准。 |
| FHWA, *Safe Routes to School* | 学校周边步行骑行安全需要工程、教育、执法和评价协同。 | 支撑方案 4 的儿童友好和学校过街安全标准。 |
| FHWA, *Traffic calming ePrimer* | 交通 calming 通过降低速度和减少冲突改善社区街道安全。 | 支撑方案 4、7 的低速、共享空间和社区安全判断。 |
| NACTO, *Urban street design guide* | 城市街道应服务多模式交通、公共空间和沿街活动。 | 支撑完整街道、共享街道、sidewalk、crossing、parklet 等标准。 |
| NACTO, *Transit street design guide* | 公交街道设计应覆盖步行接入、候车、上车和换乘体验。 | 支撑方案 3 的 `TRANSIT_PROX`, `LIGHT_UNI`, `active_front_ratio`。 |
| NACTO, *Urban street stormwater guide* | 街道绿化和雨洪设施可同时提升气候韧性、舒适性和公共空间质量。 | 支撑方案 6、7 的 `TREE_SHADE`, `MICRO_ENV`, `presentation_score`。 |
| GDCI, *Designing streets for kids* | 从儿童和照护者视角设计街道，强调安全、可读性、舒适和活动。 | 支撑方案 4 的 `focal_readability`, `visual_clutter`, `CROSS_PROV`。 |
| GDCI & NACTO, *Global street design guide* | 街道不仅是交通通道，也可以是公共空间、市场、前院和社区活动场所。 | 支撑方案 5、7 的 `POI_MIX`, `active_front_ratio`, `presentation_score`。 |
| TRB, *TCRP Report 19* | 公交站位置与设计需考虑乘客接入、安全、候车设施和运营效率。 | 支撑方案 3 的公交停靠和站台评价。 |
| Ewing & Handy (2009) | 将 imageability、enclosure、human scale、transparency、complexity 等城市设计品质与步行体验关联。 | 支撑 Place Quality 指标，如 `style_coherence`, `spacing_rhythm`, `focal_readability`。 |
| Gehl (2010) | 强调人的尺度、停留、步行舒适和街道生活。 | 支撑 `FURN_D`, `active_front_ratio`, `MICRO_ENV`。 |
| Jacobs (1961) | 强调混合使用、街道活力和自然监视。 | 支撑 `POI_MIX`, `ENTR_DENS`, `active_front_ratio`。 |
| Whyte (1980) | 通过公共空间观察强调座椅、停留、可达和人的实际使用。 | 支撑方案 5 的家具和活动空间标准。 |

## 7. APA 引用

American Association of State Highway and Transportation Officials. (2004). *Guide for the planning, design, and operation of pedestrian facilities*. AASHTO.

Federal Highway Administration. (n.d.). *Medians and pedestrian refuge islands in urban and suburban areas*. U.S. Department of Transportation. https://highways.dot.gov/safety/proven-safety-countermeasures/medians-and-pedestrian-refuge-islands-urban-and-suburban-areas

Federal Highway Administration. (n.d.). *Road diets (roadway reconfiguration)*. U.S. Department of Transportation. https://highways.dot.gov/safety/other/road-diets

Federal Highway Administration. (n.d.). *Safe Routes to School*. U.S. Department of Transportation. https://www.fhwa.dot.gov/environment/safe_routes_to_school/

Federal Highway Administration. (n.d.). *Traffic calming ePrimer*. U.S. Department of Transportation. https://highways.fhwa.dot.gov/safety/speed-management/countermeasures/traffic-calming-eprimer

Global Designing Cities Initiative. (2020). *Designing streets for kids*. National Association of City Transportation Officials. https://globaldesigningcities.org/publication/designing-streets-for-kids/

National Association of City Transportation Officials. (2013). *Urban street design guide*. Island Press. https://nacto.org/publication/urban-street-design-guide/

National Association of City Transportation Officials. (2016). *Transit street design guide*. Island Press. https://nacto.org/publication/transit-street-design-guide/

National Association of City Transportation Officials. (2017). *Urban street stormwater guide*. Island Press. https://nacto.org/publication/urban-street-stormwater-guide/

National Association of City Transportation Officials & Global Designing Cities Initiative. (2016). *Global street design guide*. Island Press. https://globaldesigningcities.org/publication/global-street-design-guide/

Transportation Research Board. (1996). *Guidelines for the location and design of bus stops* (TCRP Report 19). National Academies Press. https://www.trb.org/Main/Blurbs/153827.aspx

Ewing, R., & Handy, S. (2009). Measuring the unmeasurable: Urban design qualities related to walkability. *Journal of Urban Design, 14*(1), 65-84. https://doi.org/10.1080/13574800802451155

Gehl, J. (2010). *Cities for people*. Island Press.

Jacobs, J. (1961). *The death and life of great American cities*. Random House.

Whyte, W. H. (1980). *The social life of small urban spaces*. Conservation Foundation.
