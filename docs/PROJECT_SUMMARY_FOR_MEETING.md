# RoadGen3D 组会项目总结

> Status: current meeting brief  
> Last verified: 2026-05-08  
> Audience: 组会口头汇报，建议 3-5 分钟讲完

## 一句话定位

RoadGen3D 目前更准确的定位是：一个规则/约束驱动、AI 辅助的 3D 街道场景生成与评价框架。它把场景设计目录、图模板/参考标注、preset/config patch 和可选 prompt 转换为可解释、可评价、可迭代改进的街道场景。

组会上不建议直接说“神经符号框架”。代码里确实有显式符号结构和可选学习/LLM 接口，但当前 Viewer 主用的 Scenario Designs 生成路径是 catalog + template patch 驱动，并且批量生成时设置 `preset_id=skip_llm`，不是严格意义上的神经符号模型。

## 当前主流程

当前 Viewer 有两条生成入口。组会建议重点讲当前 Scenario Designs 批量路径：

```text
Scenario Designs catalog
  -> Viewer /api/scenario-designs/runs
  -> ScenarioDesignService: template_patch + compose_config_patch
  -> DesignDraft(skip_llm) + graph_template SceneContext
  -> /api/scene/jobs + SceneJobService
  -> graph-template bridge + compose_street_scene()
  -> scene_layout.json + scene.glb
  -> road-metrics 评价
  -> Viewer 加载、对比、报告和后续分析
```

Design 面板和 Branch/Pareto 仍然保留 prompt/preset 到 `/api/scene/jobs`、`/api/design/branch-runs` 的路径，但这不是当前 Viewer 场景设计批量生成的主讲线路。

对外最容易讲的入口是 `web/viewer`，后端主入口是 `web/api/main.py`，当前场景目录入口是 `ScenarioDesignService`，核心生成入口仍然复用 `compose_street_scene()`，评价主线是 road-metrics 的 `EvalEngine`。

## 阶段进展

- 交互入口已经收敛到 Viewer：支持 Scenario Designs 批量生成、设计输入、生成任务、3D 查看、评价展示、历史样本、对比和 Branch/Pareto trace。
- 当前可演示主线已经转向 scenario catalog：场景意图、功能区、surface annotation、template patch 和 compose config patch 都能被记录和复现。
- 生成链路有显式中间表示：`DesignDraft`、`SceneContext`、`StreetComposeConfig`、`StreetProgram`、`ConstraintSet`、`LayoutSolverResult`、`scene_layout.json`。其中 `StreetProgram/ConstraintSet/LayoutSolver` 属于生成内核结构，不等于已经实现完整神经符号学习框架。
- 生成模式已经覆盖 graph template、MetaUrban、OSM/reference annotation 和 template fallback，适合做不同研究场景的扩展。
- 评价闭环已经建立：walkability、safety、beauty 和 overall 由统一评价接口返回，并能反馈到 Viewer、branch run 和后续优化。
- 文档主线已明确：当前 source of truth 是框架总览、数据契约、评价契约、部署与任务边界四份文档。

## 主要亮点

- 不是黑箱从输入直接跳到 mesh，而是保留 scenario catalog、template patch、config patch、生成 trace 和 `scene_layout.json`，方便解释、调试和复现。
- 规则约束、布局求解、资产放置和 3D 输出已经接成完整 pipeline。
- Branch/Pareto trace 可以把参数、RAG evidence、patch、评分变化和保留 artifact 串起来，用于分析“为什么这个结果更好”。
- Viewer 已经从单纯 3D 查看器扩展为设计工作台，能承接生成、评价、对比、benchmark explorer 和 correlation analysis。

## 当前边界

目前更准确的表述是“街道空间与 3D 场景生成评价框架”，还不宜宣称为完整道路工程或交通仿真框架。主要缺口包括：

- lane-level connectivity、turn movement、signal/control model 还不稳定。
- NACTO/ADA/AASHTO/本地规范还没有形成可版本化规则库。
- 学习模型和 LLM/RAG 目前是可选增强；当前 Scenario Designs 批量生成不是 LLM 逐样本推导，也不是训练完成的神经符号生成器。
- 安全评价偏结构化指标和视觉/LLM，不等价于冲突仿真、延误、容量或 LOS。
- `scene_layout.json` 还缺 `schema_version`、JSON Schema 和迁移策略。
- 当前 job service 适合本地 demo 和研究原型，还不是生产级多用户任务系统。

## 下一步建议

1. 先补契约：给 `scene_layout.json` 加 schema/version，固定 API 和评价字段。
2. 收敛权威来源：preset 以 `src/roadgen3d/presets.py` 为准，Viewer 通过 API 或生成脚本同步。
3. 建 benchmark：固定场景集、随机种子、资产版本、评价配置和 golden artifacts。
4. 补道路工程层：lane movement、路口转向、信号控制、冲突点和过街暴露时间。
5. 治理生产边界：持久化 job store、artifact registry、cancel/retry/timeout 和 Viewer 本地 middleware 的生产替代方案。
