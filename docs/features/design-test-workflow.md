# Workbench 自动化测试脚本设计

## 1. 概述

创建一个自动化测试脚本 `scripts/test_workflow.py`，用于验证整个工作流（模板选择 → 场景生成 → 评估）的端到端功能。

### 目标
- 随机选择一个预设模板
- 自动化执行完整的场景生成流程
- 获取并验证 LLM 评估结果
- 生成可读的测试报告

## 2. 技术方案

### 2.1 脚本位置
```
scripts/test_workflow.py
```

### 2.2 依赖
- `httpx` - HTTP 客户端（已有）
- `python-dotenv` - 环境变量加载
- 标准库: `json`, `time`, `random`, `pathlib`, `argparse`

### 2.3 API 端点

| 端点 | 方法 | 用途 |
|------|------|------|
| `POST /api/scene/jobs` | 创建场景生成任务 | 返回 job_id |
| `GET /api/scene/jobs/{job_id}` | 轮询任务状态 | 获取生成结果 |
| `POST /api/design/evaluate/unified` | LLM 评估 | 获取评估分数 |

### 2.4 配置

```python
# 从环境变量或 .env 读取
API_BASE = "http://127.0.0.1:8010"

# 预设模板
SCENE_PRESETS = [
    "pedestrian_friendly",      # 步行友好
    "commercial_vitality",     # 商业活力
    "transit_priority",        # 公交优先
    "park_landscape",          # 公园景观
    "quiet_residential",       # 安静居住
    "balanced_complete",        # 平衡街道
]

# 超时配置
JOB_POLL_INTERVAL = 2  # 秒
JOB_TIMEOUT = 300      # 最大等待 5 分钟
EVAL_TIMEOUT = 60       # 评估超时 60 秒
```

## 3. 执行流程

```
┌─────────────────────────────────────────┐
│ 1. 初始化                               │
│    - 加载环境变量                        │
│    - 随机选择模板                        │
│    - 打印测试配置                        │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│ 2. 创建场景生成任务                       │
│    POST /api/scene/jobs                 │
│    - 使用选中的 preset                   │
│    - 保存 job_id                        │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│ 3. 轮询任务状态                          │
│    GET /api/scene/jobs/{job_id}         │
│    - succeeded → 继续                    │
│    - failed → 报告失败                   │
│    - pending/running → 等待并重试        │
│    - 超时 → 报告超时                    │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│ 4. 调用 LLM 评估                         │
│    POST /api/design/evaluate/unified    │
│    - 传入 scene_layout_path             │
│    - 获取评估分数                        │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│ 5. 生成报告                             │
│    - Markdown 格式                      │
│    - 保存到 artifacts/test_reports/     │
│    - 打印摘要到控制台                   │
└─────────────────────────────────────────┘
```

## 4. 数据结构

### 4.1 测试结果

```python
@dataclass
class TestResult:
    preset_id: str
    preset_name: str
    job_id: str
    status: str  # "passed", "failed", "timeout"

    # Timing
    job_created_at: str
    job_completed_at: str | None
    duration_seconds: float

    # Scene generation
    scene_layout_path: str | None
    scene_glb_path: str | None
    viewer_url: str | None

    # Evaluation
    evaluation: dict | None
    error_message: str | None

    # Report path
    report_path: str
```

### 4.2 报告格式

```markdown
# Workbench 自动化测试报告

**测试时间**: 2026-04-12 15:30:00
**模板**: 步行友好 (pedestrian_friendly)
**状态**: ✅ 通过

## 执行摘要

| 指标 | 值 |
|------|-----|
| 总耗时 | 45.2 秒 |
| 任务 ID | job_abc123 |
| 评估状态 | 成功 |

## 场景生成

- **状态**: succeeded
- **布局路径**: /tmp/scene_xxx/scene_layout.json
- **GLB 路径**: /tmp/scene_xxx/scene.glb

## 评估结果

### 综合评分

| 维度 | 分数 |
|------|------|
| 步行性 (45%) | 78 |
| 安全性 (35%) | 72 |
| 美观性 (20%) | 85 |
| **综合** | **77** |

### 详细指标

| 指标 | 值 |
|------|-----|
| SID_CLR (人行道净宽) | 0.85 |
| CLEAR_CONT (净空连续性) | 0.78 |
| ... | ... |

## 原始数据

```json
{
  "walkability": 78,
  "safety": 72,
  "beauty": 85,
  "overall": 77,
  "indicators": {...}
}
```

---

*由 test_workflow.py 自动生成*
```

## 5. 命令行接口

```bash
# 基本用法（随机选择模板）
uv run python scripts/test_workflow.py

# 指定模板
uv run python scripts/test_workflow.py --preset pedestrian_friendly

# 指定 API 地址
uv run python scripts/test_workflow.py --api-base http://127.0.0.1:8010

# 完整选项
uv run python scripts/test_workflow.py \
  --preset pedestrian_friendly \
  --api-base http://127.0.0.1:8010 \
  --timeout 300 \
  --output artifacts/test_reports/
```

## 6. 错误处理

| 错误类型 | 处理方式 | 报告状态 |
|----------|----------|----------|
| API 连接失败 | 打印错误，退出码 1 | failed |
| 任务创建失败 | 打印错误，退出码 1 | failed |
| 任务超时 | 打印警告，继续 | timeout |
| 任务失败 | 记录错误信息 | failed |
| 评估失败 | 记录错误信息 | passed (with warning) |

## 7. 成功标准

测试通过条件：
1. ✅ 场景生成任务成功完成 (`status: succeeded`)
2. ✅ 返回有效的 `scene_layout_path`
3. ⚠️ 评估可选（失败时警告但不标记为失败）

## 8. 输出文件

```
artifacts/test_reports/
└── test_2026-04-12_15-30-00.md    # 测试报告
```

## 9. 扩展计划

- [ ] 支持连续运行多次测试
- [ ] 支持生成 HTML 报告
- [ ] 支持 CI/CD 集成
- [ ] 支持邮件/Slack 通知
