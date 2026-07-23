# 2D → 3D 迁移清单

本清单用于防止 Viewer 拆分或工作台整合后出现“入口仍可点击，但读取了错误 2D revision、打开 starter 场景或调用了错误生成 API”的回归。当前验收只覆盖专业工作台与后端批量生成；课程工作台已经归档，不进入本轮迁移、测试或发布门禁。

## 当前状态

- [done] Viewer 十个拆分提交已进入当前分支。证据：`69e6407..9eaa72a`，旧公共入口保留兼容导出。
- [done] 2D → 3D 静态迁移契约已建立。证据：`web/viewer/tests/two-to-three-migration-contract.test.mjs`。
- [doing] 在远程容器执行编译、契约测试和随机 OSM 批量生成。负责人：Codex；检查点：远程日志目录中的 `summary.tsv` 和批量报告。
- [plan] 若远程样本失败，按错误类别定位并修复。触发条件：任一测试非零退出或随机生成非全成功；下一动作：保留失败 seed、bbox、任务 ID 和服务日志后修复并复跑。
- [drop] 恢复 `new-ui/` 原型与 Model Input Browser 产品入口。原因：它们不属于主要 2D → 3D 流程；仅在产品负责人明确要求时重开。
- [drop] 课程工作台迁移与回归。原因：当前已归档；仅在重新启用课程产品时单独审计。

## 旧入口到新入口映射

| 原位置或职责 | 当前权威位置 | 迁移状态 | 必须保持的契约 |
|---|---|---|---|
| `scene-graph.ts` 内的 OSM/source 工作流 | `sg-source-workflow-controller.ts` | 已迁移 | source 必须 normalized，当前 revision 必须 approved |
| `scene-graph.ts::generateApprovedScene` | `createSgSourceWorkflowController().generateApprovedScene` | 已迁移 | 仅当 `sceneSourceRevision === sourceRevision` 时复用现有 3D，否则进入生成 |
| `scene-graph.ts::openGenerationConfiguration` | `sg-source-workflow-controller.ts` | 已迁移 | 配置弹窗显示当前 2D revision、道路、路口、建筑和必需家具数量 |
| Scene Graph 直接切换 Viewer | `RouteIsland.tsx` + `professional-pipeline.ts` 的一次性 target | 已迁移 | 先保存 `generate/browse` intent，再导航；intent 消费后立即删除 |
| Viewer 启动时自动找场景 | `RouteIsland.tsx` + Viewer runtime controllers | 已迁移 | 明确 handoff 优先 workflow scene；generation intent 不得打开 starter |
| `app.ts` 内 Viewer 生命周期 | `viewer-lifecycle-controller.ts`、`viewer-workspace-view-controller.ts` 等 | 已迁移 | explicit layout 和 workflow layout 优先；不得任意选择 recent scene |
| 专业工作台生成接口 | `POST /api/scene/jobs` | 保留 | request 经 `prepare_scene_generation_request` 后进入 `create_scene_job` |
| 多种 scene context 的生成分流 | `services/design_runtime.py::generate_scene_from_draft` | 保留 | OSM、reference annotation、graph template 等最终进入共享生成器 |
| 最终街景合成 | `street_layout.py::compose_street_scene` | 保留 | 必须输出 `scene_layout.json` 与 GLB；序列化使用最终 rendered paving |
| 旧 Layout A/B 抽屉 | `viewer-scenario-workbench.ts` 的 A/B/C 工作台 | 合并迁移 | 版本来源、父 revision、参数和评分可追溯 |
| 旧帮助侧栏 | `react/ShortcutModal.tsx` | 内容压缩迁移 | 主流程说明仍可从顶部 `?` 打开 |
| `new-ui/` 静态原型 | 无 | 已退役 | 不作为生产 2D → 3D 入口 |
| Model Input Browser 产品入口 | hash 重定向到 Viewer；实现仍保留 | 已退役但有死代码 | 不得影响生产导航；后续单独决定清理或恢复 |
| Course Studio | 归档代码 | 移出当前范围 | 不进入专业工作台发布门禁，也不阻塞本轮远程验证 |

## 主流程验收门

1. 2D source 已 normalized 且当前 revision 已批准。
2. 生成 intent 带着当前 workflow 上下文进入 Viewer。
3. 旧 3D revision 与当前 2D revision 不一致时，必须显示为旧结果并重新生成。
4. 生成 API 返回 job ID；任务状态必须可轮询到成功或带诊断的失败。
5. 成功结果必须同时存在 `scene_layout_path` 与 `scene_glb_path`。
6. Viewer 必须加载该次任务的 layout，不得回退到广州 starter 或任意 recent scene。
7. 随机 OSM 批量测试必须记录 seed、bbox、配置、任务 ID、耗时、结果路径和错误分类。
8. 编译、契约测试、后端主链测试、批量生成以及服务日志必须保存在同一远程验证目录。
9. Trimesh 的生产制品路径依赖 `scipy`；它是几何运行依赖，不是 AI 模型依赖。
10. `pytest` 不进入生产依赖集，仅由远程验证门禁通过
    `ops/requirements-validation.txt` 安装。

## 远程验证

容器更新后执行：

```bash
cd /workspace/RoadGen3D
ops/container/validate-2d-to-3d.sh --osm-runs 20 --workers 5 --seed 20260723
```

日志默认写入：

```text
/workspace/roadgen3d-data/validation/2d-to-3d-<UTC时间>-<提交>/
```

`summary.tsv` 是总门禁；`batch-report/` 保存逐样本 JSON/Markdown 报告；`service-logs/` 保存验证结束时的 API、worker 和 supervisor 日志尾部。
