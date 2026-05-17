# RoadGen3D 文档入口

> Status: current
> Last verified: 2026-05-15

本目录是 RoadGen3D 的唯一文档导航入口。当前事实以“当前主文档”为准；专题文档可以记录功能细节和设计过程；归档文档只用于追溯历史。

## 组会材料

| 文档 | 说明 |
| --- | --- |
| [PROJECT_SUMMARY_FOR_MEETING.md](PROJECT_SUMMARY_FOR_MEETING.md) | 下午组会可直接使用的一页中文项目总结 |

## 当前主文档

| 文档 | 说明 |
| --- | --- |
| [current-progress.md](current-progress.md) | 当前进度、架构状态、文档整合、todo/next/plan/drop 总控页 |
| [ROADGEN3D_FRAMEWORK.md](ROADGEN3D_FRAMEWORK.md) | 当前框架总览和主流程 source of truth |
| [ACTIVE_ENTRYPOINTS.md](ACTIVE_ENTRYPOINTS.md) | 当前活跃入口、子模块边界和 legacy alias |
| [DATA_CONTRACTS.md](DATA_CONTRACTS.md) | `DesignDraft`、`SceneContext`、`StreetComposeConfig`、`scene_layout.json` 等数据契约 |
| [EVALUATION.md](EVALUATION.md) | 当前评价 API、字段、降级规则和 road-metrics 边界 |
| [DEPLOYMENT_AND_JOBS.md](DEPLOYMENT_AND_JOBS.md) | FastAPI、Viewer dev middleware、job service 和 artifact 边界 |

## 支撑文档

| 文档 | 说明 |
| --- | --- |
| [ASSET_INVENTORY.md](ASSET_INVENTORY.md) | 资产库存说明 |
| [DATA_RECOVERY.md](DATA_RECOVERY.md) | 本地数据恢复说明 |
| [../web/viewer/README.md](../web/viewer/README.md) | Viewer 当前入口 |
| [../web/viewer/ARCHITECTURE.md](../web/viewer/ARCHITECTURE.md) | Viewer 代码组织守则 |
| [../src/roadgen3d/eval_engine_ext/README.md](../src/roadgen3d/eval_engine_ext/README.md) | road-metrics 子模块入口 |
| [../evaluation/scenario_evaluation_standards.md](../evaluation/scenario_evaluation_standards.md) | 七个场景方案的人读评价方法说明；自动执行以 rubric JSON 为准 |

## 功能专题

| 文档 | 说明 |
| --- | --- |
| [features/README.md](features/README.md) | Viewer、scene compare、junction editor、场景设计和测试工作流专题索引 |

## 历史归档

| 文档 | 说明 |
| --- | --- |
| [archive/README.md](archive/README.md) | 旧架构、旧评价、Workbench 和历史审查文档索引 |
