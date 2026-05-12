# RoadGen3D 评价契约

> Status: current draft  
> Last verified: 2026-05-08  
> Scope: 当前 RoadGen3D 主流程使用的 road-metrics 评价接口、输出字段和失败降级。历史评价计划文档仍可参考，但本文作为当前实现入口。

## 1. 当前评价主线

当前主评价入口是：

```text
web/api/main.py
  /api/design/evaluate/unified
  ↓
DesignAssistantService.evaluate_scene_unified()
  ↓
road_metrics.EvalEngine.evaluate()
  ↓
walkability / safety / beauty / overall
```

输入是 `scene_layout.json`，可选输入是 Viewer 捕获的 `rendered_views` 或 legacy `image_path`。默认评价 profile 是 `local_segment_v1`，面向当前单街段、小场景对比；未来或更大尺度路网可切到 `network_v1`。

场景化七方案批量评价是独立的一层：

```text
data/scenario_designs/hkust_gz_gate_evaluation_rubric.json
  ↓
ScenarioRubricEvaluator
  ↓
ScenarioDesignService run manifest / SCENARIO_GENERATION_REPORT.md
```

这层不替代 `/api/design/evaluate/unified`，也不调用 LLM 做自动 Pass / Review / Fail。它只复用 road-metrics 的结构化 walkability、safety、beauty 分数，再叠加机器可读阈值和 semantic gates，用于场景方案批量报告与后续校准。

## 2. API 请求

`POST /api/design/evaluate/unified`

```json
{
  "layout_path": "/abs/path/to/scene_layout.json",
  "image_path": null,
  "evaluation_profile": "local_segment_v1",
  "rendered_views": [
    {
      "view_id": "pedestrian_forward",
      "label": "Pedestrian forward view",
      "image_data_url": "data:image/png;base64,..."
    },
    {
      "view_id": "child_forward",
      "label": "Child forward view",
      "image_data_url": "data:image/png;base64,..."
    }
  ]
}
```

`rendered_views` 当前由 `web/viewer/src/viewer-evaluation-capture.ts` 捕获。`pedestrian_forward`、`pedestrian_reverse`、`overview_topdown` 用于 safety/beauty 的视觉 LLM 评价；`child_forward` 是 1.1m 儿童视角，只用于独立的 child-friendly 辅助评分。

## 3. API 响应

`evaluate_scene_unified()` 当前返回：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `walkability` | number | 0-100，结构化步行性评分 |
| `safety` | number \| null | 0-100，视觉/LLM safety 可用时返回 |
| `beauty` | number \| null | 0-100，视觉/LLM beauty 可用时返回 |
| `overall` | number \| null | 0-100，safety 和 beauty 均可用时返回 |
| `score_weights` | object | 当前权重 |
| `score_formula` | string | 公式文本 |
| `evaluation_profile` | string | 当前评价 profile，默认 `local_segment_v1` |
| `child_friendly` | object | 儿童友好辅助分，不参与 overall |
| `indicator_meta` | object | 指标权重、来源、适用尺度和低区分度标注 |
| `evaluation` | string | 评价摘要 |
| `suggestions` | array | 改进建议 |
| `indicators` | object | 子指标摘要 |
| `config_patch` | object | 可用于再生成的建议 patch |
| `llm_status` | object | LLM visual eval 状态 |

重要降级规则：

- walkability 是结构化指标，通常应该可用。
- safety / beauty 依赖视觉/LLM report，可不可用。
- `overall` 只有 safety 和 beauty 都可用时才返回；否则为 `null`，避免把缺失视觉评价伪装成完整总分。

## 4. 评分维度

当前评价分为三大类：

| 维度 | 当前意义 | 数据来源 |
| --- | --- | --- |
| Walkability | 步行性与完整街道基础表现 | `scene_layout.json` 结构化字段 |
| Safety | 感知安全、照明、保护、可见性等 | 结构化指标 + rendered views/LLM |
| Beauty | 美观性、空间丰富度、材质与场景表现 | 结构化指标 + rendered views/LLM |

综合评分公式应以 API 返回的 `score_formula` 为准。默认设计意图仍是：

```text
overall = W * walkability + S * safety + B * beauty
```

`child_friendly` 是辅助维度，不并入上面的 `overall`。缺少 `child_forward` 视角时，它返回 `score: null` 和 `status: missing_child_view`，避免用成人视角或纯结构化数据伪装儿童体验。

## 5. 评价 profile

| Profile | 用途 | 可达性处理 |
| --- | --- | --- |
| `local_segment_v1` | 当前默认；适合单街段、小范围场景横向比较 | 降低 `TRANSIT_PROX` 权重，因为这些场景的可达性通常相同或差异很小 |
| `network_v1` | 预留给更大路网、多个街段或真实 OD/公交可达性差异 | 恢复 `TRANSIT_PROX` 为常规 delight 子项权重 |

`TRANSIT_PROX` 不删除，而是在 `indicator_meta` 中标注为 `network_scale`，并在 `local_segment_v1` 下标记 `low_discrimination: true`。

## 6. 生成来源与评分来源

生成来源和评分来源是两套概念，不能混在一起解释：

| 字段 | 说明 |
| --- | --- |
| `generation_method=llm_assisted` | 参数来自 LLM，并结合 RAG、scenario parameter triples、规则约束或优化指令 |
| `generation_method=pure_llm` | 参数主要由 LLM 直接给出，仍经过 sanitizer 和 deterministic exporter |
| `generation_method=parametric` | 参数来自 preset、手工 patch、Pareto/search sample 或纯参数采样 |
| `generation_method=unknown_legacy` | 历史样本缺少足够 provenance，读取时兜底 |

评分来源则由 `llm_status`、`indicator_meta`、`branch_score_fallback` 等字段表达：walkability 是结构化指标；safety/beauty 在视觉 LLM 可用时来自 rendered views，否则 API 保持 N/A，Branch/Benchmark 内部可用带 provenance 的结构化 fallback 维持散点分析。

## 7. 与旧文档的关系

当前仓库中存在多份评价相关文档：

- `docs/archive/evaluation-system.md`
- `docs/archive/scoring_formula_specification.md`
- `docs/archive/EVALUATION_REPORT.md`
- `docs/archive/evaluation_module_plan.md`
- `docs/archive/evaluate_implementation_analysis.md`
- `src/roadgen3d/eval_engine_ext/road_metrics/LAYERED_ARCHITECTURE.md`

建议后续合并为：

- 本文：主仓当前评价契约和 API 字段。
- road-metrics 文档：评价引擎内部架构。
- archive/planning 文档：历史公式设计和未来扩展。

## 8. 需要补齐的评价能力

### P0：契约稳定

- 固定 `EvaluateRequestModel` 和响应 schema。
- 给 `indicators` 写字段表。
- 给 `llm_status` 写状态枚举。
- 明确 `None` / `N/A` / 失败之间的区别。

### P1：benchmark

- 固定 benchmark scene set。
- 每个 benchmark 固定 seed、asset manifest、eval config。
- 保存 golden `scene_layout.json`、rendered views、evaluation result。

### P2：道路工程评价

当前评价偏街道空间和视觉质量，还缺少：

- lane-level conflict points。
- crossing exposure time。
- signal/control availability。
- vehicle / pedestrian / bike delay。
- accessibility and reachability。
- safety surrogate metrics from simulation。

这些补齐前，不建议把评价宣称为完整交通工程评价。
