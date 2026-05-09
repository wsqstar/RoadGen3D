# RoadGen3D 评估体系文档

## 概述

本文档描述 RoadGen3D 项目如何对生成结果进行检验，包括使用的评估方法、执行的标准、流程中的检验点、以及迭代优化机制。

---

## 0. 配置化权重

所有评估参数都支持通过配置文件或 API 参数调整，无需修改代码。

### 默认配置

```python
from roadgen3d.eval_engine.core.config import EvalConfig, AggregationConfig

# 默认权重
config = EvalConfig.default()
print(config.aggregation.walkability_weight)  # 0.45
print(config.aggregation.safety_weight)      # 0.35
print(config.aggregation.beauty_weight)       # 0.20
```

### 从配置文件加载

```python
import json
from roadgen3d.eval_engine.core.config import EvalConfig

# 加载配置文件
with open("configs/eval_config_pedestrian.json") as f:
    data = json.load(f)

config = EvalConfig.from_dict(data)
```

### 前端临时覆盖

```python
from roadgen3d.eval_engine.core.config import EvalConfig, AggregationConfig

# 创建自定义权重配置
config = EvalConfig(
    aggregation=AggregationConfig(
        walkability_weight=0.60,  # 步行优先
        safety_weight=0.25,
        beauty_weight=0.15
    )
)

# 传入控制器
controller = AutoIterationController(
    graph_ctx,
    eval_config=config,  # 使用自定义权重
    ...
)
```

### 预置配置文件

| 文件 | 场景 | W | S | B |
|------|------|---|---|---|
| `eval_config_default.json` | 平衡 | 0.45 | 0.35 | 0.20 |
| `eval_config_pedestrian.json` | 步行优先 | 0.60 | 0.25 | 0.15 |
| `eval_config_safety.json` | 安全优先 | 0.30 | 0.50 | 0.20 |
| `eval_config_beauty.json` | 美观优先 | 0.35 | 0.25 | 0.40 |

---

## 1. 评估方法体系

项目采用 **三层评估架构**：

```
┌─────────────────────────────────────────────────────────────┐
│                    综合评分 (Evaluation Score)               │
│        W × Walkability + S × Safety + B × Beauty            │
│        (W, S, B 可通过 EvalConfig 配置)                     │
└─────────────────────────────────────────────────────────────┘
         ↓                    ↓                    ↓
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│   Walkability   │  │     Safety      │  │     Beauty      │
│   (步行性)       │  │    (安全性)      │  │    (美观性)      │
└─────────────────┘  └─────────────────┘  └─────────────────┘

默认: W=0.45, S=0.35, B=0.20
```

### 1.1 Walkability (步行性)

**11 个底层指标 + 3 大支柱**

| 指标代码 | 说明 | 计算方式 |
|----------|------|----------|
| SID_CLR | 净空宽度 | `(clear_width - 1.8) / (3.2 - 1.8)` |
| CLEAR_CONT | 清晰连续性 | `clear_area / sidewalk_area` |
| FURN_D | 家具设施密度 | `amenity_density / 0.15` |
| LIGHT_UNI | 照明均匀度 | `1 - spacing_cv(lamp_xs)` |
| TREE_SHADE | 树荫覆盖率 | `canopy_area / sidewalk_area` |
| BUFFER_RATIO | 缓冲带比例 | `furnishing_width / road_width` |
| TRANSIT_PROX | 交通可达性 | `exp(-min_dist / 60)` |
| CROSS_PROV | 过街设施提供 | `crossing_count / target` |
| ENTR_DENS | 入口密度 | `entrance_count / length_m / 0.04` |
| POI_MIX | POI混合多样性 | Shannon熵 / 最大熵 |
| MICRO_ENV | 微环境舒适度 | `0.5×tree + 0.3×noise + 0.2×openness` |

**三大支柱权重：**
- Protection (保护性): 40%
- Comfort (舒适性): 35%
- Delight (愉悦性): 25%

**支柱计算公式：**
```
Protection = mean(LIGHT_UNI, BUFFER_RATIO, CROSS_PROV)
Comfort = mean(SID_CLR, CLEAR_CONT, TREE_SHADE, MICRO_ENV)
Delight = mean(FURN_D, TRANSIT_PROX, ENTR_DENS, POI_MIX)
WalkabilityIndex = 0.4×Protection + 0.35×Comfort + 0.25×Delight
```

### 1.2 Safety (安全性)

**结构化特征 (5项)：**
| 特征 | 权重 | 说明 |
|------|------|------|
| LIGHT_UNI | 0.15 | 照明均匀度 |
| CROSS_PROV | 0.15 | 过街设施 |
| BUFFER_RATIO | 0.10 | 缓冲带比例 |
| BOLLARD_DENSITY | 0.10 | 护柱密度 |
| VISIBILITY_PENALTY | 可变 | 可见性惩罚 |

**LLM 增强评分 (可选)：**
- `lighting`, `visibility`, `protection`, `activation` 四个子维度
- LLM权重: 60%，结构化权重: 40%
- 子维度标准差 > 0.20 时标记 `needs_review = True`

### 1.3 Beauty (美观性)

**结构化特征 (7项)：**
| 特征 | 权重 | 说明 |
|------|------|------|
| presentation_score | 0.40 | 呈现分数 |
| active_front_ratio | 0.10 | 活跃界面比例 |
| anchor_poi_score | 0.10 | 锚点POI分数 |
| visual_clutter | 0.10 | 视觉杂乱度 (反向) |
| style_coherence | - | 风格一致性 |
| spacing_rhythm | - | 间距节奏感 |
| focal_readability | - | 焦点可读性 |

**LLM 增强评分 (可选)：**
- `coherence`, `human_scale`, `material_contrast`, `visual_interest` 四个子维度

**锚点POI权重表：**
| 类型 | 权重 | 类型 | 权重 |
|------|------|------|------|
| museum | 1.2 | restaurant | 1.0 |
| cultural | 1.2 | library | 1.0 |
| healthcare | 1.1 | government | 1.0 |
| public_service | 1.1 | cafe | 0.9 |

### 1.4 Engineering Metrics (工程指标)

**布局质量指标：**
| 指标 | 说明 | 计算 |
|------|------|------|
| spacing_uniformity | 间距均匀度 | `1 - CV(gaps)` per category |
| style_consistency | 风格一致性 | mean(CLIP_scores) |
| balance_score | 左右平衡度 | `1 - |left-right| / total` |
| dropped_slot_rate | 丢弃槽位率 | `dropped / (placed + dropped)` |
| overlap_rate | 重叠率 | pair-wise AABB intersections |
| diversity_ratio | 多样性 | unique categories / total |
| retrieval_top3_category_hit | Top-3命中率 | category match in top-3 |

**拓扑与可行性：**
| 指标 | 说明 |
|------|------|
| topology_validity | 拓扑有效性 (0-1) |
| cross_section_feasibility | 横断面可行性 (0-1) |
| editability | 可编辑性 |
| conflict_explainability | 冲突可解释性 |

---

## 2. 评估流程

### 2.1 完整流程图

```
┌─────────────────────────────────────────────────────────────────┐
│                      设计查询 (Query)                            │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  Step 1: LLM 生成初始配置                                        │
│  DesignAssistantService.generate_initial_config_from_graph()      │
└─────────────────────────────────────────────────────────────────┘
                              ↓
                    ┌───────────────────┐
                    │   ITERATION LOOP  │
                    │   (最多 N 轮)     │
                    └───────────────────┘
                              ↓
         ┌────────────────────────────────────────────┐
         │  Step 2: 场景生成                          │
         │  generate_scene_from_graph_context()        │
         └────────────────────────────────────────────┘
                              ↓
         ┌────────────────────────────────────────────┐
         │  Step 3: 预览渲染                          │
         │  render_topdown_preview()                   │
         └────────────────────────────────────────────┘
                              ↓
         ┌────────────────────────────────────────────┐
         │  Step 4: 结构化评估 (立即执行)              │
         │  ┌─────────────────────────────────────┐   │
         │  │ • compute_walkability_indicators()  │   │
         │  │ • compute_structured_safety_report()│   │
         │  │ • compute_structured_beauty_report() │   │
         │  └─────────────────────────────────────┘   │
         └────────────────────────────────────────────┘
                              ↓
         ┌────────────────────────────────────────────┐
         │  Step 5: LLM 评估 (可选)                   │
         │  ┌─────────────────────────────────────┐   │
         │  │ • evaluate_safety()                  │   │
         │  │ • evaluate_beauty()                  │   │
         │  └─────────────────────────────────────┘   │
         └────────────────────────────────────────────┘
                              ↓
         ┌────────────────────────────────────────────┐
         │  Step 6: 综合评分计算                       │
         │  Score = W×Walkability + S×Safety + B×Beauty│
         │  (权重来自 EvalConfig.aggregation)            │
         └────────────────────────────────────────────┘
                              ↓
         ┌────────────────────────────────────────────┐
         │  Step 7: 回归检测                           │
         │  comparison.regressed_areas?                │
         └────────────────────────────────────────────┘
                              ↓
              ┌────────────────┴────────────────┐
              ↓                                  ↓
    ┌─────────────────┐              ┌─────────────────┐
    │ 分数提升?        │              │ 连续2轮无提升?  │
    └─────────────────┘              └─────────────────┘
              ↓                                  ↓
    ┌─────────────────┐              ┌─────────────────┐
    │ 记录最佳结果     │              │   EARLY STOP    │
    │ 更新配置         │              └─────────────────┘
    └─────────────────┘
              ↓
    ┌────────────────────────────────────────────┐
    │  Step 8: LLM 提议改进 (可选)                │
    │  DesignAssistantService.propose_improvement()│
    └────────────────────────────────────────────┘
                              ↓
                    ┌───────────────────┐
                    │   下一轮迭代?      │
                    └───────────────────┘
                              ↓
         ┌────────────────────────────────────────────┐
         │  Step 9: 保存最佳场景到 final/              │
         │  • scene_layout.json                       │
         │  • scene.glb                              │
         │  • preview.png                            │
         └────────────────────────────────────────────┘
                              ↓
         ┌────────────────────────────────────────────┐
         │  Step 10: 生成 iteration_log.json          │
         │  • 每轮评估结果                            │
         │  • 分数演进记录                           │
         │  • 配置变更历史                           │
         └────────────────────────────────────────────┘
```

### 2.2 检验时机

| 阶段 | 检验内容 | 输出 |
|------|----------|------|
| **生成后** | 结构化评估 (Walkability/Safety/Beauty) | 立即计算，无需LLM |
| **生成后** | LLM评估 (可选) | 更深层的语义理解 |
| **迭代中** | 回归检测 | 检测指标下降 |
| **迭代末** | 最佳选择 | 选择 Evaluation Score 最高的结果 |

### 2.3 Loop 机制

**迭代循环控制器**: `AutoIterationController` (`src/roadgen3d/auto_pipeline/iteration_controller.py`)

**循环终止条件：**
1. 达到最大迭代次数 (`max_iterations`)
2. 连续 2 轮无分数提升 → **早停 (Early Stop)**

**改进策略：**
```python
# 弱点驱动的 RAG 查询
if walkability_index < 0.5:
    weakness_queries.append("pedestrian friendly street design walkability")
if safety_score < 0.5:
    weakness_queries.append("street safety design guidelines")
if beauty_score < 0.5:
    weakness_queries.append("urban street beauty aesthetics")
```

---

## 3. 多方案比较

### 3.1 Rule vs Learned 对比

**脚本**: `scripts/layout_eval.py`

```bash
# 运行评估，同时执行两种策略
python scripts/layout_eval.py \
    --placement-policy learned \
    --compare-rule \
    --seed-start 0 \
    --seed-end 4
```

**输出**: `artifacts/m4/eval_report.json`

```json
{
  "summary": { /* learned 策略统计 */ },
  "rule_summary": { /* rule 策略统计 */ },
  "comparison_vs_rule": {
    "delta_spacing_uniformity": 0.0,
    "delta_style_consistency": 0.010,
    "delta_latency_ms_per_instance": 0.721,
    ...
  }
}
```

### 3.2 多 Query 对比

**脚本**: `scripts/run_auto_eval.py`

```bash
# 多设计场景并行评估
python scripts/run_auto_eval.py \
    --queries "modern transit boulevard" \
              "pedestrian-friendly green street" \
              "commercial shopping district" \
    --max-iterations 3
```

**输出**: `artifacts/auto_eval_*/eval_report.json`

### 3.3 参数演进散点图

**脚本**: `scripts/eval_scatter.py`

```bash
# 单参数直方图
python scripts/eval_scatter.py \
    --input artifacts/m4/eval_per_scene.csv \
    --x spacing_uniformity \
    --bins 20

# 双参数散点图
python scripts/eval_scatter.py \
    --input artifacts/m4/eval_per_scene.csv \
    --x walkability_index \
    --y safety_score \
    --show-regression \
    --show-pareto

# 分组对比
python scripts/eval_scatter.py \
    --input artifacts/m4/rule/eval_per_scene.csv \
           artifacts/m4/learned/eval_per_scene.csv \
    --x spacing_uniformity \
    --y style_consistency \
    --group-by policy_used
```

---

## 4. 执行标准

### 4.1 阈值标准

| 指标 | 最小值 | 理想值 | 说明 |
|------|--------|--------|------|
| 净空宽度 (SID_CLR) | 1.8m | 3.2m | 无障碍通行 |
| 家具密度 (FURN_D) | - | 0.15/m | 设施密度 |
| 入口密度 (ENTR_DENS) | - | 0.04/m | 商业活力 |
| 护柱密度 (BOLLARD_DENSITY) | - | 0.15/m | 安全设施 |
| 活跃界面比例 | - | 70% | 人行道界面 |
| 锚点POI密度 | - | 0.12/m | 目的地密度 |
| LLM评分方差阈值 | - | 0.20 | 触发人工审查 |

### 4.2 评分权重 (可配置)

```
综合评分 = W × Walkability + S × Safety + B × Beauty

默认: W=0.45, S=0.35, B=0.20
```

权重可调整，详见第0节「配置化权重」。

---

## 5. 输出文件

### 5.1 单次迭代输出

```
artifacts/
└── auto_pipeline/
    └── scene/
        ├── iter_00/
        │   ├── scene_layout.json      # 场景布局
        │   ├── scene.glb              # 3D模型
        │   ├── preview.png            # 俯视图预览
        │   ├── walkability.json       # 步行性报告
        │   ├── safety.json            # 安全性报告
        │   ├── beauty.json            # 美观性报告
        │   ├── evaluation.json        # LLM评估
        │   ├── improvement.json       # 改进建议
        │   └── config_patch.json      # 配置快照
        ├── iter_01/
        │   └── ...
        ├── final/
        │   ├── scene_layout.json      # 最佳场景
        │   ├── scene.glb
        │   └── preview.png
        └── iteration_log.json          # 完整迭代日志
```

### 5.2 批量评估输出

```
artifacts/
├── m4/
│   ├── eval_per_scene.csv    # 每场景一行
│   └── eval_report.json      # 汇总报告
└── eval_scatter_*.png       # 可视化图表
```

---

## 6. 相关代码文件

| 文件 | 职责 |
|------|------|
| `src/roadgen3d/eval_quality.py` | 步行性/安全性/美观性计算 |
| `src/roadgen3d/eval_metrics.py` | 工程指标计算 |
| `src/roadgen3d/eval_engine/core/config.py` | 评估配置 (权重、阈值) |
| `src/roadgen3d/eval_engine/` | 解耦的评估引擎 |
| `src/roadgen3d/auto_pipeline/iteration_controller.py` | 迭代循环控制器 (支持 EvalConfig) |
| `scripts/layout_eval.py` | 批量评估脚本 |
| `scripts/run_auto_eval.py` | 多Query评估脚本 |
| `scripts/eval_scatter.py` | 可视化散点图工具 |
| `scripts/snapshot_diff.py` | 参数演进对比工具 |
| `configs/eval_config_*.json` | 预置评估配置文件 |

---

## 7. 使用建议

1. **权重调整**: 通过 `EvalConfig` 或 `configs/*.json` 调整评估权重
2. **开发调试**: 使用 `--compare-rule` 验证新策略效果
3. **质量评估**: 检查 `walkability.json` 中的 11 项指标
4. **参数调优**: 使用 `eval_scatter.py` 分析参数敏感性
5. **回归检测**: 关注 `iteration_log.json` 中的分数下降
6. **人工审查**: LLM方差 > 0.20 时标记需要复查
