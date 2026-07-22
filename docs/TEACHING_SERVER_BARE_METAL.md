# RoadGen3D 轻量教学服务器（裸机、无 Torch）

> Status: ready for SSH deployment
> Last verified: 2026-07-22
> Scope: 不使用 Docker；不安装 `torch`、`transformers`；不部署 CLIP、Shap-E 或 learned checkpoint。

## 1. 购买配置

面向少于 5 名用户、允许任务排队的教学环境，建议购买：

| 项目 | 建议值 | 说明 |
| --- | --- | --- |
| 系统 | Ubuntu Server 24.04 LTS x86_64 | 文档和 systemd 单元以此为准 |
| CPU | 4 vCPU（现代共享型或计算型） | API 轻，场景生成由 1 个 worker 串行执行 |
| 内存 | 16 GB | 比 8 GB 有足够的网格处理余量；无需 32 GB |
| Swap | 8 GB | 只作为突发保护，不作为正常工作内存 |
| 磁盘 | 100–150 GB NVMe | 当前完整 `assets/` + `data/` 约 8.3 GB，还需工件、数据库和备份空间 |
| GPU | 不需要 | 本配置不加载任何模型 |
| 公网 | 1 个 IPv4、域名、HTTPS | 只开放 22/80/443 |

运行拓扑是 `Nginx → Uvicorn API → PostgreSQL/Redis`，另有恰好 1 个 RQ worker。5 名用户可以同时访问和提交任务，但生成任务按队列逐个运行。这是 16 GB 稳定运行的关键约束。

## 2. 固定运行边界

服务器环境必须保持以下值：

```text
program_generator = heuristic_v1
placement_policy = rule
ROADGEN_ASSET_RETRIEVAL_MODE = curated_rule_pool
ROADGEN_OBJECT_STORE = local
ROADGEN_JOB_MODE = rq
RQ worker 数量 = 1
```

`curated_rule_pool` 会在进入生成流程时直接跳过 `ClipTextEmbedder`，不是等模型导入失败后再降级。`scene_layout.json` 的 summary 会记录请求和实际使用的资产检索模式。

此配置不安装：

- `torch`、`transformers`、CLIP 权重、Shap-E 权重；
- `scipy`、`scikit-learn`、`networkx`（高级统计/图分析降级为可选能力）；
- OpenAI Python SDK（HTTP 评价客户端使用轻量 `httpx`）；
- `boto3`、MinIO/S3 客户端（工件写本地磁盘）。

唯一依赖入口是 [requirements-teaching-server.txt](../ops/requirements-teaching-server.txt)。不要在这台服务器上执行通用 `requirements.txt`、`requirements-api.txt` 或模型相关 requirements。

## 3. 首次 SSH 安装

以下命令中的域名、密码和 token 都是占位符。密码和私钥不得提交到 Git。

### 3.1 系统软件与账户

```bash
sudo apt update
sudo apt install -y git curl rsync nginx postgresql redis-server build-essential libgl1 libglib2.0-0
sudo adduser --system --group --home /var/lib/roadgen3d roadgen3d
sudo install -d -o roadgen3d -g roadgen3d /opt/roadgen3d /var/lib/roadgen3d/artifacts /var/lib/roadgen3d/osm-cache /var/cache/roadgen3d/matplotlib
sudo install -d -o www-data -g www-data /var/www/roadgen3d
sudo install -d -m 0750 -o root -g roadgen3d /etc/roadgen3d
```

将 `uv` 安装到系统可执行路径：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh
```

实际 SSH 配置时会先核对 `uv` 和 Python 的真实安装位置。

### 3.2 代码和 curated assets

代码放在 `/opt/roadgen3d`。私有仓库可使用服务器 deploy key 克隆，也可从工作站 rsync；完成后创建隔离的 Python 3.11 环境：

```bash
sudo -u roadgen3d git clone REPOSITORY_URL /opt/roadgen3d
sudo -u roadgen3d /usr/local/bin/uv venv --python 3.11 /opt/roadgen3d/.venv
sudo -u roadgen3d /usr/local/bin/uv pip install --python /opt/roadgen3d/.venv/bin/python -r /opt/roadgen3d/ops/requirements-teaching-server.txt
```

资产不能只假设来自 Git：部署前应从当前工作站同步已经验证过的 `assets/` 和 `data/`，以免 manifest 存在但网格文件缺失。

```bash
rsync -az --delete-delay ./assets/ USER@HOST:/tmp/roadgen3d-assets/
rsync -az --delete-delay ./data/ USER@HOST:/tmp/roadgen3d-data/
```

在服务器上将临时目录安装到 `/opt/roadgen3d/assets` 和 `/opt/roadgen3d/data`，然后把所有权设为 `roadgen3d:roadgen3d`。`--delete-delay` 只用于这两个明确的临时同步目录，不对服务器根目录或项目根执行删除。

### 3.3 PostgreSQL 与 Redis

```bash
sudo -u postgres createuser --pwprompt roadgen3d
sudo -u postgres createdb --owner roadgen3d roadgen3d
sudo systemctl enable --now postgresql redis-server
```

PostgreSQL 和 Redis 只监听本机，不开放公网端口。环境文件从 [roadgen3d-teaching.env.example](../ops/server/roadgen3d-teaching.env.example) 复制到 `/etc/roadgen3d/roadgen3d.env`，替换数据库密码、bootstrap token、域名后执行：

```bash
sudo chown root:roadgen3d /etc/roadgen3d/roadgen3d.env
sudo chmod 0640 /etc/roadgen3d/roadgen3d.env
cd /opt/roadgen3d
sudo -u roadgen3d /bin/bash -c 'set -a; source /etc/roadgen3d/roadgen3d.env; set +a; exec /opt/roadgen3d/.venv/bin/alembic upgrade head'
```

数据库密码在 URL 中必须 percent-encode；环境文件中的值不要含未转义的空格或 shell 表达式。

### 3.4 API、worker 与 Nginx

```bash
sudo install -m 0644 /opt/roadgen3d/ops/server/roadgen3d-api.service /etc/systemd/system/
sudo install -m 0644 /opt/roadgen3d/ops/server/roadgen3d-worker.service /etc/systemd/system/
sudo install -m 0644 /opt/roadgen3d/ops/server/roadgen3d-nginx.conf /etc/nginx/sites-available/roadgen3d
sudo ln -s /etc/nginx/sites-available/roadgen3d /etc/nginx/sites-enabled/roadgen3d
sudo rm /etc/nginx/sites-enabled/default
sudo systemctl daemon-reload
sudo systemctl enable --now roadgen3d-api roadgen3d-worker nginx
```

先将 [roadgen3d-nginx.conf](../ops/server/roadgen3d-nginx.conf) 中的域名替换为真实域名。前端使用 Node 22 只构建一次，不作为常驻服务。建议在工作站构建后同步：

```bash
cd web/viewer
npm ci
npm run build
rsync -az --delete-delay dist/ USER@HOST:/tmp/roadgen3d-viewer-dist/
```

再在服务器安装静态文件：

```bash
sudo rsync -a --delete /tmp/roadgen3d-viewer-dist/ /var/www/roadgen3d/
sudo chown -R www-data:www-data /var/www/roadgen3d
```

随后用 Certbot 或云厂商证书为 Nginx 启用 HTTPS。防火墙只允许 OpenSSH 和 `Nginx Full`；API 的 `8000`、PostgreSQL 的 `5432`、Redis 的 `6379` 均不得对公网开放。

## 4. 上线验收

加载正式环境后先运行 profile 检查：

```bash
set -a
. /etc/roadgen3d/roadgen3d.env
set +a
/opt/roadgen3d/.venv/bin/python /opt/roadgen3d/ops/scripts/check_teaching_server_profile.py
```

输出必须满足：

- `ok: true`；
- `program_generator: heuristic_v1`；
- `placement_policy: rule`；
- `asset_retrieval_mode: curated_rule_pool`；
- 环境中找不到 `torch` 和 `transformers`；
- 四个 curated manifest 均非空，且家具、建筑 manifest 至少各有一个可解析网格。

服务检查：

```bash
curl --fail http://127.0.0.1:8000/api/health
curl --fail https://REAL_DOMAIN/api/health
curl --fail https://REAL_DOMAIN/api/v1/auth/bootstrap-status
systemctl --no-pager --full status roadgen3d-api roadgen3d-worker
journalctl -u roadgen3d-api -u roadgen3d-worker -n 100 --no-pager
```

最后从浏览器创建一个小场景，确认任务先进入队列再完成，并检查产物 summary：

```json
{
  "program_generator_used": "heuristic_v1",
  "policy_used": "rule",
  "asset_retrieval_mode": "curated_rule_pool"
}
```

## 5. 运维边界

- 保持 1 个 worker；不要为“5 个用户”启动 5 个生成进程。
- API 和 worker 都由 systemd 拉起，机器重启后自动恢复；Redis 队列负责等待任务。
- 每日同时备份 PostgreSQL dump 和 `/var/lib/roadgen3d/artifacts`；二者必须来自同一备份窗口。
- 监控内存、磁盘和失败队列。内存长期超过 13 GB 时，先缩小场景/资产集合，不直接扩 worker。
- 部署更新顺序：停 worker → 等在跑任务结束 → 更新代码/资产 → 安装轻量 requirements → 数据库迁移 → profile 检查 → 重启 API/worker → 小场景验收。

## 6. 购买后交给 Codex 的 SSH 信息

购买完成后只需提供：

1. 公网 IP 或主机名；
2. 可 `sudo` 的 SSH 用户名和端口；
3. 已解析到服务器的域名；
4. 云厂商/机房是否限制出站下载；
5. 希望导入的初始教师、学生账号名单（可稍后提供）。

不要在聊天中发送密码或私钥。应把 SSH 公钥加入服务器，私钥保留在本机；届时我会通过本机 `ssh` 完成安装、证书、服务、验证和交接。
