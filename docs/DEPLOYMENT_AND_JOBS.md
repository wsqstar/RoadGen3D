# RoadGen3D 部署与任务边界

> Status: current draft
> Last verified: 2026-07-22
> Scope: 本地研究开发、共享服务器多用户平台、Viewer middleware、FastAPI 和任务系统边界。

## 1. 当前运行组件

当前开发态通常由两个主要服务组成：

| 组件 | 默认地址 | 职责 |
| --- | --- | --- |
| FastAPI | `http://127.0.0.1:8010` | 主业务 API：draft、generation jobs、branch runs、evaluation、knowledge、diff |
| Viewer Vite | `http://127.0.0.1:4173` | Viewer 前端 + 本地开发文件 API |

`web/workbench` 已归档，不属于默认主流程。

少于 5 名用户、允许生成任务排队的共享教学服务器，当前推荐
[轻量裸机方案](TEACHING_SERVER_BARE_METAL.md)：Ubuntu 24.04、16 GB RAM、
Nginx + 单 Uvicorn API + 单 RQ worker + PostgreSQL/Redis，本地工件存储；
固定使用 `heuristic_v1 + rule + curated_rule_pool`，不安装 Torch/Transformers，
也不部署 CLIP/Shap-E。它提供持久化身份、项目级权限、任务和工件存储，
且不是把 `make dev` 暴露到公网。

[teaching-platform.md](teaching-platform.md) 保留较重的容器化平台说明，适用于明确需要
MinIO/S3 或容器编排的环境，不是本次轻量教学服务器的安装入口。

2026-07-22 的实际教学部署已改为已有内网容器，使用 25 个 RQ worker（最多 5 名用户、
每人最多 5 个 active jobs）、本地
PostgreSQL/Redis 和仓库外用户数据目录；其 source of truth 是
[TEACHING_CONTAINER_INTERNAL.md](TEACHING_CONTAINER_INTERNAL.md)。裸机文档继续作为
独立服务器购买时的备选方案。

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

### 4.3 共享服务器的多用户任务系统

教学/个人工作区 API（`/api/v1`）已具备可部署的多用户基础：

- PostgreSQL 持久化用户、会话哈希、课程/个人工作区、项目、版本、任务和审计日志；
- Bearer 会话、课程成员角色和项目级鉴权；私有项目默认仅创建者可访问，教师/管理员
  按课程权限访问；
- Redis + RQ 执行队列，PostgreSQL 保留任务状态并在 worker 重启时重新排队未完成任务；
- MinIO（S3-compatible）按 `projects/<project_id>/artifacts/...` 保存工件；
- `ROADGEN_MAX_ACTIVE_JOBS_PER_USER` 限制每位用户的 queued/running 任务数。

这意味着系统可以支持多个用户同时登录、创建隔离项目并排队执行任务；它不意味着
可以不经容量测试地任意横向扩展。当前 RQ worker 数量、GPU/CPU/LLM 配额、对象存储
生命周期和访客公共模式仍需由部署方明确治理。

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

### P4：服务器硬化与运维（部署前必须确认）

- 只向外暴露 HTTPS reverse proxy；禁止直接发布 FastAPI、PostgreSQL、Redis、MinIO
  S3 或 MinIO console 端口。
- 生产 frontend 只代理 `/api/v1` 与健康检查；旧 `/api/*` 研究端点在生产代理中返回
  `404`，因为它们没有多用户鉴权及工件隔离契约。
- 使用准确的 `ROADGEN_CORS_ORIGINS`，强随机的数据库/MinIO/bootstrap 密码，并将
  `.env.teaching` 设为仅部署账户可读。
- 以 PostgreSQL 与 MinIO 的一致性备份为恢复单元，并定期演练恢复。
- 上线前用两个测试账户验证跨项目读取、下载工件和创建任务均被拒绝；用一条真实生成
  任务验证 worker、对象存储、超时与失败可观测性。

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
