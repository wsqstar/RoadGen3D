# RoadGen3D 部署与任务边界

> Status: current draft  
> Last verified: 2026-05-03  
> Scope: 当前本地开发部署、Viewer dev middleware、FastAPI 后端和任务系统边界。

## 1. 当前运行组件

当前开发态通常由两个主要服务组成：

| 组件 | 默认地址 | 职责 |
| --- | --- | --- |
| FastAPI | `http://127.0.0.1:8010` | 主业务 API：draft、generation jobs、branch runs、evaluation、knowledge、diff |
| Viewer Vite | `http://127.0.0.1:4173` | Viewer 前端 + 本地开发文件 API |

`web/workbench` 已归档，不属于默认主流程。

## 2. FastAPI 主后端

入口：

- `web/api/main.py`

主要 API 组：

| API 组 | 路径 |
| --- | --- |
| health / metadata | `/api/health`、`/api/presets`、`/api/graph-templates`、`/api/reference-plans` |
| draft | `/api/design/draft` |
| generation | `/api/design/generate`、`/api/scene/jobs`、`/api/scene/jobs/{job_id}` |
| branch run | `/api/design/branch-runs`、`/api/design/branch-runs/{run_id}` |
| evaluation | `/api/design/evaluate`、`/api/design/evaluate/unified`、`/api/design/evaluate/compare` |
| improvement | `/api/design/improve` |
| knowledge | `/api/knowledge/*` |
| diff | `/api/scenes/diff`、`/api/scenes/diff/image` |

## 3. Viewer dev middleware

入口：

- `web/viewer/vite.config.ts`

这些 API 是 Viewer 开发态本地适配层，不应被视作生产后端：

| 路径 | 作用 |
| --- | --- |
| `/api/layout` | 读取本地 `scene_layout.json` 并生成 Viewer manifest |
| `/api/recent-layouts` | 扫描本地 artifacts 中的 recent layouts |
| `/api/file` | 读取本地 GLB/图片等文件 |
| `/api/asset-manifests` | 列出资产 manifest |
| `/api/asset-manifest` | 分页读取资产 manifest |
| `/api/asset-manifest/save` | 写回资产 manifest |
| `/api/asset-manifest/delete` | 删除 manifest 记录 |
| `/api/scenes/diff/image` | 本地生成/缓存 diff image |
| `/api/presets` | 当前仍维护一套本地 demo presets，需收敛 |

治理建议：

- 文档中明确这些 API 是 local adapter。
- 生产部署时应迁入 FastAPI 或由专门 artifact service 提供。
- 删除或收敛 Vite 中重复的 presets。

## 4. Job 系统现状

### 4.1 `SceneJobService`

当前实现：

- 单进程。
- 单后台线程。
- 内存队列。
- 内存 job store。
- 通过 `Condition` 轮询/通知状态。
- 生成后自动调用 evaluator。
- 保留最近 progress operations 和 trace。

适合：

- 本地开发。
- 单用户 demo。
- 自动化测试。
- 研究原型。

不适合：

- 多用户共享服务。
- 多实例横向扩展。
- 进程重启后恢复任务。
- 长任务并发执行。
- 严格资源隔离。

### 4.2 `BranchRunService`

当前作用：

- 基于 prompt、RAG evidence 和 graph template 生成多轮候选。
- 每个 node 调用生成和评价。
- 按 evaluation weights 排序。
- 给 Viewer 提供生长树和 best node。

边界：

- 它是设计探索服务，不是通用优化器。
- 当前 ranking 受 LLM/visual eval 可用性影响，应在 UI 中暴露评价缺失状态。

## 5. Artifact 边界

主要 artifact：

| Artifact | 说明 |
| --- | --- |
| `scene_layout.json` | 主数据输出 |
| `scene.glb` / `scene.ply` | 3D 场景输出 |
| `production_steps/` | 生产步骤快照 |
| `presentation_*` | topdown / presentation render |
| `placement_decisions.jsonl` | 放置决策日志 |
| Viewer cached layout | `artifacts/web_viewer_layouts/...` 中的可视化缓存 |

后续需要：

- artifact registry。
- artifact schema/version。
- cleanup policy。
- immutable run id。
- manifest of generated outputs。

## 6. 生产化路线

### P0：明确边界

- 在主框架文档中把 FastAPI 和 Viewer dev middleware 分开。
- Viewer 所有生产必要 API 应有 FastAPI 等价入口。
- 本地文件写操作需要明确权限边界。

### P1：持久化任务

- 引入持久化 job table。
- job 状态、progress operations、trace、result path 都落盘。
- 支持 restart 后恢复 job records。

### P2：任务控制

- cancel。
- retry。
- timeout。
- max concurrent jobs。
- worker pool。

### P3：可部署 artifact service

- 静态文件服务或对象存储。
- signed URLs / access policy。
- generated artifact index。
- old artifact cleanup。

## 7. 当前推荐开发模式

```bash
make dev
```

或者分别启动：

```bash
make api
make viewer-web
```

`make workbench-api` 是历史兼容 alias，实际入口是当前主 FastAPI。
