# RoadGen3D API Guide

## Overview

RoadGen3D v2.0 重构后的 API 架构专注于为 web viewer 提供直接的场景生成服务，不再依赖 Gradio 或 LLM workflow。

### 核心变化

- ✅ **独立生成 API** - 直接调用 scene generation，无需 LLM 中转
- ✅ **RESTful 设计** - 标准的 HTTP 接口，易于集成
- ✅ **异步任务** - 后台生成，轮询查询状态
- ✅ **LLM 可选** - LLM/RAG 作为上游服务保留，但不阻塞主流程

## Quick Start

### 1. 启动 API 服务器

```bash
cd /path/to/RoadGen3D
python3 -m uvicorn ui.api:app --host 127.0.0.1 --port 8000
```

访问 http://localhost:8000/docs 查看 Swagger UI 文档。

### 2. 生成 MetaUrban 场景

```bash
curl -X POST http://localhost:8000/api/designs/metaurban \
  -H "Content-Type: application/json" \
  -d '{
    "reference_plan_id": "hkust_gz_gate",
    "lane_count": 2,
    "lane_width_m": 3.5,
    "sidewalk_width_m": 2.5,
    "block_sequence": "SXSOXS",
    "seed": 42
  }'
```

响应：
```json
{
  "job_id": "mu_abc12345",
  "status": "completed"
}
```

### 3. 查询生成状态

```bash
curl http://localhost:8000/api/designs/mu_abc12345/status
```

响应：
```json
{
  "job_id": "mu_abc12345",
  "status": "completed",
  "created_at": "2026-03-31T16:00:00+00:00",
  "finished_at": "2026-03-31T16:00:30+00:00",
  "result": {
    "scene_layout_path": "/path/to/scene_layout.json",
    "scene_glb_path": "/path/to/scene.glb",
    "viewer_url": "http://localhost:4173/?layout=/path/to/scene_layout.json"
  }
}
```

### 4. 在 Viewer 中查看

打开浏览器访问 `viewer_url` 字段中的 URL。

---

## API Endpoints

### Generation APIs

#### POST /api/designs/metaurban

创建一个新的 MetaUrban 风格街道设计。

**Request Body:**
```json
{
  "reference_plan_id": "hkust_gz_gate",  // 必填：参考方案 ID
  "lane_count": 2,                       // 可选：车道数 (默认 2)
  "lane_width_m": 3.5,                   // 可选：车道宽度 (默认 3.5m)
  "sidewalk_width_m": 2.5,               // 可选：人行道宽度 (默认 2.5m)
  "road_width_m": null,                  // 可选：道路总宽 (null=自动计算)
  "segment_length_m": 12.0,              // 可选：路段长度 (默认 12m)
  "start_heading_deg": 0.0,              // 可选：起始角度 (默认 0°)
  "block_sequence": "SXSOXS",            // 可选：块序列 (S/Straight, C/Curve, X/Intersection, T/T-junction, O/Roundabout)
  "block_count": 6,                      // 可选：块数量 (当 block_sequence 为空时使用)
  "seed": 42                             // 可选：随机种子
}
```

**Response:**
```json
{
  "job_id": "mu_abc12345",
  "status": "completed"  // 或 "queued", "processing", "failed"
}
```

#### POST /api/designs/template

使用预定义的 graph template 生成街道设计。

**Request Body:**
```json
{
  "template_id": "hkust_gz_gate",
  "lane_count": 2,
  "lane_width_m": 3.5,
  "sidewalk_width_m": 2.5,
  "road_width_m": 7.0,
  "length_m": 80.0,
  "seed": 42
}
```

#### POST /api/designs/osm

基于 OpenStreetMap 数据生成街道设计（暂未实现）。

#### GET /api/designs/{job_id}/status

查询设计生成任务的状态。

**Response (completed):**
```json
{
  "job_id": "mu_abc12345",
  "status": "completed",
  "created_at": "2026-03-31T16:00:00+00:00",
  "finished_at": "2026-03-31T16:00:30+00:00",
  "result": {
    "job_id": "mu_abc12345",
    "status": "completed",
    "compose_config": {...},
    "summary": {...},
    "scene_layout_path": "/path/to/scene_layout.json",
    "scene_glb_path": "/path/to/scene.glb",
    "scene_ply_path": "/path/to/scene.ply",
    "viewer_url": "http://localhost:4173/?layout=/path/to/scene_layout.json"
  },
  "error": ""
}
```

**Response (failed):**
```json
{
  "job_id": "mu_abc12345",
  "status": "failed",
  "error": "Reference plan not found: invalid_plan_id"
}
```

#### GET /api/scenes/{job_id}

获取已完成场景生成的完整结果。

---

### Utility Endpoints

#### GET /api/health

健康检查端点。

**Response:**
```json
{
  "status": "healthy"
}
```

#### GET /

API 根路径，返回服务信息和可用端点列表。

---

## Architecture

### 模块结构

```
src/roadgen3d/
├── services/
│   ├── generation_core.py      # 纯生成逻辑（无 LLM 依赖）
│   ├── generation_api.py       # FastAPI 路由
│   ├── design_runtime.py       # 保留给 LLM upstream 使用
│   ├── design_types.py         # 数据类型定义
│   └── scene_jobs.py           # 任务队列服务
├── llm/                        # LLM 相关模块（可选）
│   ├── __init__.py
│   ├── glm_client.py
│   ├── prompts.py
│   └── design_workflow.py      # LLM design assistant workflow
└── knowledge/                  # RAG 实现（可选）
```

### 数据流

```
Web Viewer
    ↓
POST /api/designs/metaurban
    ↓
generation_core.generate_metaurban_scene()
    ↓
build_metaurban_scene_bridge()  # 构建 MetaUrban graph
    ↓
compose_street_scene()          # 放置 assets
    ↓
cache_scene_layout_for_viewer() # 缓存到 viewer 可读格式
    ↓
返回 viewer_url
```

---

## Python SDK Example

```python
import requests
import time

API_BASE = "http://localhost:8000"

def generate_metaurban_design():
    # Step 1: Create design
    response = requests.post(
        f"{API_BASE}/api/designs/metaurban",
        json={
            "reference_plan_id": "hkust_gz_gate",
            "lane_count": 2,
            "seed": 42,
        }
    )
    job_id = response.json()["job_id"]
    print(f"Job created: {job_id}")
    
    # Step 2: Poll for status
    while True:
        response = requests.get(f"{API_BASE}/api/designs/{job_id}/status")
        data = response.json()
        
        if data["status"] == "completed":
            print("Generation completed!")
            print(f"Viewer URL: {data['result']['viewer_url']}")
            break
        elif data["status"] == "failed":
            print(f"Generation failed: {data['error']}")
            break
        else:
            print(f"Status: {data['status']}")
            time.sleep(2)

generate_metaurban_design()
```

---

## Migration from v1.x

### Breaking Changes

- ❌ Gradio UI 已删除
- ❌ `/api/chat` 端点已移除（移到 `/api/llm/chat`，可选）
- ✅ Web viewer 保持不变
- ✅ 生成逻辑保持不变

### Upgrade Steps

1. 更新依赖：
   ```bash
   pip install -r requirements-ui.txt  # 不再需要 gradio
   ```

2. 修改客户端代码：
   ```python
   # Old v1.x (via LLM chat)
   POST /api/chat {"message": "生成一条商业街"}
   
   # New v2.0 (direct API)
   POST /api/designs/metaurban {"reference_plan_id": "hkust_gz_gate", ...}
   ```

3. 如果需要使用 LLM 辅助设计，启用可选的 LLM 模块：
   ```python
   # ui/api/__init__.py
   from roadgen3d.llm.api import router as llm_router
   app.include_router(llm_router, prefix="/api/llm", tags=["llm"])
   ```

---

## Troubleshooting

### Job 状态一直是 "queued"

当前实现是同步执行，如果出现 "queued" 可能是：
- 第一次运行需要加载模型（较慢）
- 资源不足导致阻塞

解决方案：等待更长时间，或检查服务器日志。

### "Reference plan not found"

确保 `reference_plan_id` 是有效的。内置的方案有：
- `hkust_gz_gate` (默认)

可以在 `src/roadgen3d/metaurban_procedural.py` 中查看更多内置方案。

### torch 未安装错误

场景生成需要 PyTorch。安装依赖：

```bash
pip install torch  # 或使用项目 requirements
```

---

## Future Work

- [ ] 实现真正的异步任务队列（Celery/RQ）
- [ ] 添加 OSM-based generation
- [ ] 支持 cross-section 自定义配置
- [ ] 添加 junction policy 配置
- [ ] 持久化 job 存储（目前存在内存中，重启丢失）
