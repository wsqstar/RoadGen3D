# RoadGen3D 内网教学容器部署

> Status: deployed
> Last verified: 2026-07-22
> Target: `exp-0722` / Ubuntu 22.04 / `/workspace/RoadGen3D`

## 当前拓扑

| 组件 | 容器端口/位置 | 说明 |
| --- | --- | --- |
| Nginx + Viewer | `0.0.0.0:4173` | 静态前端，并将 `/api/*` 反代到 API |
| FastAPI | `0.0.0.0:8010` | 单 API 进程 |
| RQ | 25 workers | 最多 5 名用户 × 每人最多 5 个 active generation jobs |
| PostgreSQL | `127.0.0.1:5432` | 用户、项目、任务元数据 |
| Redis | `127.0.0.1:6379` | 队列，仅容器内可访问 |
| 用户工件 | `/workspace/roadgen3d-data/artifacts` | 与代码同步边界分离 |

容器端口映射声明为 `50031:22`、`32768:4173`、`32769:8010`。2026-07-22 已从同宿主内网容器验证 Viewer 和 API 均返回 HTTP 200。当前开发电脑到 `32768/32769` 的路径可能受网络策略限制；这不影响同内网访问，若其他用户仍超时，再由宿主防火墙放行端口。

## 运行约束

```text
ROADGEN_ASSET_RETRIEVAL_MODE=curated_rule_pool
program_generator=heuristic_v1
placement_policy=rule
ROADGEN_MAX_ACTIVE_JOBS_PER_USER=5
RQ worker count=25
OMP/BLAS threads per process=1
```

虚拟环境不安装 `torch`、`transformers`，也不同步 `models/`、Shap-E cache、latent 或 learned checkpoint。

## 数据保护边界

代码位于 `/workspace/RoadGen3D`；以下状态全部位于仓库外，后续代码同步不得覆盖或删除：

- `/workspace/roadgen3d-data/roadgen3d.env`
- `/workspace/roadgen3d-data/artifacts`
- `/workspace/roadgen3d-data/osm-cache`
- `/workspace/roadgen3d-data/backups`
- `/workspace/roadgen3d-data/logs`
- PostgreSQL 数据目录 `/var/lib/postgresql/14/main`

同步必须使用 Git 跟踪文件白名单以及完整非模型 `assets/`、`data/` 数据；禁止对 `/workspace` 或 `/workspace/roadgen3d-data` 使用 `rsync --delete`。同步规则仅排除缓存、冗余压缩包、latent 和模型权重。本机 `.env`、`.claude`、`.qwen`、`.uv-python`、`.venv`、`models` 和 `artifacts` 不得上传。

后续更新使用仓库内的安全同步脚本。它同步 Git 跟踪代码、完整非模型 `assets/`/`data/`、Viewer、road-metrics 子模块和部署脚本，不使用 `--delete`：

```bash
ROADGEN_SYNC_SSH_KEY=/Users/shiqi/.ssh/docker_container_key \
  ./ops/container/sync-teaching-container.sh
```

容器完成 Git checkout 初始化后，日常代码更新优先使用 Git 工作流。脚本会拒绝覆盖远端 tracked-file 修改，并按“备份、fast-forward、子模块、依赖、前端构建、迁移、重启、健康检查”的顺序执行：

```bash
/workspace/RoadGen3D/ops/container/update-from-git.sh
```

`assets/`、`data/` 采用 sparse-checkout 排除并作为教学数据保留在工作区；用户状态仍在 `/workspace/roadgen3d-data`，两者都不受 `git pull` 覆盖。新增或修改大型教学数据时仍使用上面的安全 rsync 脚本。

## 服务管理

```bash
# 启动或安全重启全部应用进程
/workspace/RoadGen3D/ops/container/start-teaching.sh

# 查看状态
/workspace/RoadGen3D/.venv/bin/supervisorctl \
  -c /workspace/RoadGen3D/ops/container/teaching-supervisord.conf status

# 停止应用进程（不删除数据库或用户文件）
/workspace/RoadGen3D/ops/container/stop-teaching.sh

# 同一时间窗口备份 PostgreSQL 和用户 artifacts
/workspace/RoadGen3D/ops/container/backup-teaching.sh
```

容器没有 systemd 作为 PID 1。`/root/.ssh/rc` 只在 Supervisor 未运行时异步执行启动脚本，因此容器重启后的第一次 SSH 连接会恢复应用服务；若平台支持容器 startup command，应直接设置为：

```bash
/workspace/RoadGen3D/ops/container/start-teaching.sh
```

## 验收

```bash
set -a
source /workspace/roadgen3d-data/roadgen3d.env
set +a

/workspace/RoadGen3D/.venv/bin/python \
  /workspace/RoadGen3D/ops/scripts/check_teaching_server_profile.py
curl --fail http://127.0.0.1:8010/api/health
curl --fail http://127.0.0.1:4173/api/health
redis-cli --scan --pattern 'rq:worker:*' | wc -l
```

预期 profile 为 `heuristic_v1 + rule + curated_rule_pool`，worker 数为 25；全局最多执行 25 个生成任务，每位用户的 queued+running 配额仍为 5。部署 smoke scene 位于 `/workspace/roadgen3d-data/artifacts/deploy-smoke`。

管理员尚未 bootstrap 时，状态接口返回 `{"initialized":false}`。bootstrap token 只保存在权限为 `0600` 的 `/workspace/roadgen3d-data/roadgen3d.env`，不写入文档或聊天记录。
