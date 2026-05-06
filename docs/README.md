# RoadGen3D 文档入口

> Status: current  
> Last verified: 2026-05-03

## 当前主文档

| 文档 | 说明 |
| --- | --- |
| [ROADGEN3D_FRAMEWORK.md](ROADGEN3D_FRAMEWORK.md) | 当前框架总览和主流程 source of truth |
| [DATA_CONTRACTS.md](DATA_CONTRACTS.md) | `DesignDraft`、`SceneContext`、`StreetComposeConfig`、`scene_layout.json` 等数据契约 |
| [EVALUATION.md](EVALUATION.md) | 当前评价 API、字段、降级规则和 road-metrics 边界 |
| [DEPLOYMENT_AND_JOBS.md](DEPLOYMENT_AND_JOBS.md) | FastAPI、Viewer dev middleware、job service 和 artifact 边界 |
| [ROAD_GENERATION_FRAMEWORK_AUDIT.md](ROAD_GENERATION_FRAMEWORK_AUDIT.md) | 2026-05-03 框架审查、缺口和文档治理建议 |

## 保留的专题文档

| 文档 | 说明 |
| --- | --- |
| [SCENARIO_DESIGN_OPTIONS.md](SCENARIO_DESIGN_OPTIONS.md) | `场景方案.pptx.pdf` 的设计解读，提炼更多道路与公共空间场景类型 |
| [CURRENT_PROJECT_ARCHITECTURE_ANALYSIS.md](CURRENT_PROJECT_ARCHITECTURE_ANALYSIS.md) | 代码级架构审查记录，部分内容可能随迁移过时 |
| [cross-junction-ribbon-corner-data-layer.md](cross-junction-ribbon-corner-data-layer.md) | cross junction surface 数据层设计 |
| [junction-editor.md](junction-editor.md) | junction editor 说明 |
| [ASSET_INVENTORY.md](ASSET_INVENTORY.md) | 资产库存说明 |
| [DATA_RECOVERY.md](DATA_RECOVERY.md) | 本地数据恢复说明 |

## 待归档或合并

以下文档仍有历史价值，但不应作为当前主流程入口：

- `workbench_web_vs_test_pipeline.md`
- `evaluation-system.md`
- `scoring_formula_specification.md`
- `EVALUATION_REPORT.md`
- `evaluation_module_plan.md`
- `SCENE_COMPARE_*`
- `comparison-features.md`
- `scene-compare-events.ts`
- `roadgen3d_scenario_plan.md`

后续建议迁入 `docs/archive/` 或合并到 `docs/features/`。
