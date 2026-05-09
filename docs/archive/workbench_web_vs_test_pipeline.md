# Workbench Web 与 Test-Pipeline 实现差异比较

## 概述

RoadGen3D 项目中有两套完整的场景生成流程：
1. **Workbench Web** - 面向用户的 React 前端界面
2. **Test-Pipeline** - 面向自动化测试的脚本流程

两者都基于相同的底层 API，但实现方式、用户体验和功能侧重存在显著差异。

---

## 1. 架构对比

### Workbench Web
```
┌─────────────────────────────────────────────────────────────────┐
│                     React Workbench (Port 4174)                  │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────┐ │
│  │ PresetGrid│→│  SchemeGrid │→│EvaluationPanel│→│ 3D View │ │
│  └──────────┘  └──────────────┘  └──────────────┘  └─────────┘ │
│        1              2                3              结果      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
              ┌───────────────────────────────┐
              │    FastAPI Backend (Port 8010) │
              │  /api/scene/jobs              │
              │  /api/design/evaluate/unified │
              └───────────────────────────────┘
```

### Test-Pipeline
```
┌─────────────────────────────────────────────────────────────────┐
│                    Makefile (test-pipeline)                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │workbench-api │→│  viewer-web  │→│test_workflow │        │
│  │  (启动服务)   │  │  (启动服务)   │  │  (执行测试)   │        │
│  └──────────────┘  └──────────────┘  └──────────────┘        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   scripts/test_workflow.py                       │
│  1. 创建场景任务 (create_scene_job)                              │
│  2. 轮询任务状态 (pollJobCompletion)                            │
│  3. 调用LLM评估 (evaluate_scene)                               │
│  4. 生成测试报告 (generate_report)                              │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 核心差异对比

| 维度 | Workbench Web | Test-Pipeline |
|------|---------------|----------------|
| **入口** | 用户交互 (点击按钮) | 命令行脚本 (`make test-pipeline`) |
| **预设选择** | 用户界面选择，支持 6 种预设 | 随机选择或通过 `--preset` 指定 |
| **方案生成** | 串行生成 3 个方案 (A/B/C) | 单次运行生成 1 个方案 |
| **评估触发** | 用户手动点击"查看评估结果" | 自动在场景生成完成后调用 |
| **进度展示** | 模拟进度条 (假的 0-100% 动画) | 真实进度条 + ETA 倒计时 |
| **错误处理** | Toast 提示 | 详细日志 + 超时控制 |
| **输出** | 可视化界面 | Markdown 测试报告 |

---

## 3. 预设配置对比

### Workbench Web (`web/workbench/src/lib/types.ts`)

```typescript
export const SCENE_PRESETS: ScenePreset[] = [
  {
    id: "pedestrian_friendly",
    name: "步行友好",
    nameEn: "Pedestrian Friendly",
    description: "行人优先，安全舒适",
    icon: "🚶",
    color: "#4CAF50",
    prompt: "步行安全，全龄友好的完整街道，安静、安全、舒适",
    configPatch: {
      design_rule_profile: "pedestrian_priority_v1",
      objective_profile: "balanced",
      density: 0.5,
      ped_demand_level: "high",
      bike_demand_level: "medium",
      transit_demand_level: "medium",
      vehicle_demand_level: "low",
    },
  },
  // ... 5 more presets
];
```

**特点**：
- 包含 `nameEn`、`description`、`icon`、`color` 等 UI 展示属性
- `configPatch` 包含 8 个参数

### Test-Pipeline (`scripts/test_workflow.py`)

```python
SCENE_PRESETS = [
    {
        "id": "pedestrian_friendly",
        "name": "步行友好",
        "name_en": "Pedestrian Friendly",
        "prompt": "步行安全，全龄友好的完整街道，安静、安全、舒适",
        "config_patch": {
            "design_rule_profile": "pedestrian_priority_v1",
            "objective_profile": "balanced",
            "density": 0.5,
            "ped_demand_level": "high",
            "bike_demand_level": "medium",
            "transit_demand_level": "medium",
            "vehicle_demand_level": "low",
        },
    },
    # ... 5 more presets
]
```

**特点**：
- 仅包含后端需要的业务参数
- 使用 snake_case 命名 (`name_en`, `config_patch`)

### 预设列表差异

| ID | Workbench Web | Test-Pipeline |
|----|---------------|---------------|
| pedestrian_friendly | ✅ | ✅ |
| commercial_vitality | ✅ | ✅ |
| transit_priority | ✅ | ✅ |
| park_landscape | ✅ | ✅ |
| quiet_residential | ✅ | ✅ |
| balanced_complete | ✅ | ✅ |

两者预设列表完全一致，但数据结构不同。

---

## 4. 场景生成流程对比

### Workbench Web (`useGeneration.ts`)

```typescript
async function generateSchemes(selectedPreset: ScenePreset) {
  const schemeIds = ["A", "B", "C"];

  // 串行生成 3 个方案
  for (let i = 0; i < updatedSchemes.length; i++) {
    const scheme = updatedSchemes[i];

    // 假的进度动画 (0% -> 20% -> 40% -> ... -> 100%)
    for (let p = 0; p <= 100; p += 20) {
      scheme.progress = p;
      setGenerationState({ type: "generating", schemes: [...updatedSchemes] });
      await sleep(200);  // 200ms * 5 = 1s 假动画
    }

    // 创建场景任务
    const result = await createSceneJob(selectedPreset, scheme.id);

    // 评估
    const evalResult = await evaluateScene(scheme.layoutPath);
    scheme.evaluation = evalResult.scores;

    scheme.status = "ready";
  }
}
```

**问题**：
1. **假进度**：进度条是前端模拟的，与实际生成进度无关
2. **串行处理**：方案 A 完成后才开始方案 B
3. **进度信息丢失**：没有真实的子任务进度

### Test-Pipeline (`test_workflow.py`)

```python
def run_test(client, preset, poll_interval=2.0, timeout=300.0):
    # Step 1: 创建任务
    job_response = client.create_scene_job(preset)
    job_id = job_response["job_id"]

    # Step 2: 轮询等待
    while elapsed < timeout:
        status = client.get_job_status(job_id)

        if status["status"] == "succeeded":
            result = status["result"]
            break
        elif status["status"] == "failed":
            raise Exception("Job failed")

        # 真实进度: 显示当前操作信息
        operations = status.get("operations", [])
        if operations:
            current_op = operations[-1]
            print(f"\r  {spinner} [{bar}] {progress*100:5.1f}% | {op_info}")

        time.sleep(poll_interval)

    # Step 3: 评估
    result.evaluation = client.evaluate_scene(result.scene_layout_path)
```

**优点**：
1. **真实进度**：轮询 API 获取真实状态
2. **超时控制**：可配置的最大等待时间
3. **状态跟踪**：记录每个状态变更

---

## 5. 评估流程对比

### Workbench Web

评估在方案生成后自动触发：

```typescript
// useGeneration.ts L56-68
try {
  onStatusChange(`正在评估方案 ${scheme.id}...`);
  const evalResult = await evaluateScene(scheme.layoutPath);
  if (evalResult) {
    scheme.evaluation = evalResult.scores;
    scheme.indicators = evalResult.indicators || scheme.indicators;
    scheme.evaluationText = evalResult.evaluation;
    scheme.suggestions = evalResult.suggestions;
  }
} catch (evalError) {
  scheme.evaluation = { walkability: -1, safety: -1, beauty: -1, overall: -1 };
}
```

然后在 Step 3 (EvaluationPanel) 展示：

```typescript
// App.tsx L42-69
const handleShowEvaluation = useCallback(() => {
  const readySchemes = displaySchemes.filter((s) => s.status === "ready");
  const newEvaluations: EvaluationResult[] = readySchemes.map((scheme) => ({
    sceneId: scheme.id,
    scores: scheme.evaluation,
    indicators: scheme.indicators || { /* default */ },
    pillarScores: {
      Protection: scheme.evaluation.safety,    // safety -> Protection
      Comfort: scheme.evaluation.walkability,   // walkability -> Comfort
      Delight: scheme.evaluation.beauty,         // beauty -> Delight
    },
  }));
  setEvaluations(newEvaluations);
  setCurrentStep(3);
}, [displaySchemes]);
```

**注意**：Web 版本的评分映射关系：
- `walkability` → `Comfort` (舒适)
- `safety` → `Protection` (安全)
- `beauty` → `Delight` (愉悦)

### Test-Pipeline

评估结果验证：

```python
# test_workflow.py L726-735
validator = MetricsValidator()
if all(k in eval_data for k in ["walkability", "safety", "beauty", "overall"]):
    formula_valid = validator.validate_formula(
        eval_data["walkability"],
        eval_data["safety"],
        eval_data["beauty"],
        eval_data["overall"]
    )
    print(f"  公式验证: {'✓ 通过' if formula_valid else '✗ 失败'}")
```

**评分公式验证**：
```
overall = walkability * 0.45 + safety * 0.35 + beauty * 0.20
```

---

## 6. API 请求对比

### Workbench Web

```typescript
// useGeneration.ts L88-118
async function createSceneJob(preset: ScenePreset, seedSuffix: string) {
  const response = await postJson("/api/scene/jobs", {
    draft: {
      normalized_scene_query: preset.prompt,
      compose_config_patch: preset.configPatch,
      // ...
    },
    scene_context: {
      layout_mode: "graph_template",
      graph_template_id: DEFAULT_GRAPH_TEMPLATE_ID,
    },
    generation_options: { preset_id: preset.id },
  }, 60000);  // 60s timeout

  return await pollJobCompletion(response.job_id);
}
```

### Test-Pipeline

```python
# test_workflow.py L430-454
def create_scene_job(self, preset: dict) -> dict:
    payload = {
        "draft": {
            "normalized_scene_query": preset["prompt"],
            "compose_config_patch": preset["config_patch"],
            "citations_by_field": {},
            "design_summary": preset["prompt"],
            "risk_notes": [],
            "parameter_sources_by_field": {},
        },
        "scene_context": {
            "layout_mode": "graph_template",
            "aoi_bbox": None,
            "city_name_en": None,
            "reference_plan_id": None,
            "graph_template_id": self.graph_template_id,
        },
        "patch_overrides": {},
        "generation_options": {"preset_id": preset["id"]},
    }

    response = self.client.post(f"{self.base_url}/api/scene/jobs", json=payload)
    response.raise_for_status()
    return response.json()
```

### 请求 Payload 差异

| 字段 | Workbench Web | Test-Pipeline |
|------|---------------|---------------|
| `draft.citations_by_field` | ❌ 不发送 | ✅ 空 dict |
| `draft.design_summary` | ❌ 不发送 | ✅ 使用 prompt |
| `draft.risk_notes` | ❌ 不发送 | ✅ 空 list |
| `draft.parameter_sources_by_field` | ❌ 不发送 | ✅ 空 dict |
| `scene_context.aoi_bbox` | ❌ 不发送 | ✅ null |
| `scene_context.city_name_en` | ❌ 不发送 | ✅ null |
| `scene_context.reference_plan_id` | ❌ 不发送 | ✅ null |
| `patch_overrides` | ❌ 不发送 | ✅ 空 dict |

---

## 7. 轮询机制对比

### Workbench Web

```typescript
// useGeneration.ts L120-149
async function pollJobCompletion(jobId: string) {
  for (let i = 0; i < MAX_GENERATION_ATTEMPTS; i++) {  // MAX_GENERATION_ATTEMPTS = 120
    const status = await getJson(`/api/scene/jobs/${jobId}`, 10000);

    if (status.status === "succeeded" && status.result) {
      return status.result;
    }
    if (status.status === "failed") {
      throw new Error("Job failed");
    }
    await sleep(POLL_INTERVAL_MS);  // POLL_INTERVAL_MS = 1500 (1.5s)
  }
  throw new Error("Job timed out");
}
```

- **轮询间隔**：1.5 秒
- **最大尝试次数**：120 次
- **最大等待时间**：120 × 1.5s = 180 秒

### Test-Pipeline

```python
# test_workflow.py L592-679
elapsed = 0.0
timeout = 300.0  # 可配置，默认 600s
poll_interval = 2.0

while elapsed < timeout:
    status = client.get_job_status(result.job_id)
    status = status_response.get("status", "")

    if status == "succeeded":
        # 完成
        break
    elif status == "running" or status == "processing":
        # 显示详细进度
        progress = elapsed / timeout
        eta = (timeout - elapsed) if timeout > elapsed else 0
        print(f"\r  {spinner} [{bar}] {progress*100:5.1f}% | ETA: {eta_str}{op_info}")

    time.sleep(poll_interval)
    elapsed = time.time() - start_time
else:
    # 超时处理
    result.status = "timeout"
```

- **轮询间隔**：2 秒
- **默认超时**：300 秒（可配置，最长 600 秒）
- **更详细的进度信息**：包含 ETA、当前操作

---

## 8. 错误处理对比

### Workbench Web

```typescript
// useGeneration.ts L74-79
} catch (error) {
  console.error(`方案 ${scheme.id} 生成失败:`, error);
  scheme.status = "failed";
  scheme.progress = 0;
}
```

- 错误仅记录到 console
- UI 显示 "failed" 状态
- 不影响其他方案继续生成

### Test-Pipeline

```python
# test_workflow.py L629-637
elif status == "failed":
    result.error_message = status_response.get("error", "Job failed")
    print()
    print(f"{'='*60}")
    print(f"  ✗ 场景生成失败!")
    print(f"  错误: {result.error_message}")
    print(f"{'='*60}")
    print()
    return result  # 立即返回，不继续
```

- 详细错误信息输出到终端
- 测试报告记录错误
- 立即终止执行

### API 连接错误处理

**Workbench Web**：
```typescript
} catch (evalError) {
  scheme.evaluation = { walkability: -1, safety: -1, beauty: -1, overall: -1 };
}
```
评估失败时使用 `-1` 作为占位符。

**Test-Pipeline**：
```python
except httpx.ConnectError as e:
    result.error_message = f"Connection error: {e}"
    print(f"\n❌ 连接 API 失败: {e}")
```
更详细的错误分类和处理。

---

## 9. 输出对比

### Workbench Web

- **实时 UI 反馈**：进度条、状态文字
- **最终展示**：雷达图、柱状图、3D Viewer 链接
- **无持久化**：不保存历史记录

### Test-Pipeline

- **详细日志**：每一步都输出到终端
- **Markdown 报告**：`artifacts/test_reports/test_YYYY-MM-DD_HH-MM-SS.md`
- **汇总报告**：`artifacts/test_reports/SUMMARY.md`

报告内容示例：
```markdown
# Workbench 自动化测试报告

**测试时间**: 2024-01-15 14:30:00
**模板**: 步行友好 (`pedestrian_friendly`)
**状态**: ✅ PASSED

## 执行摘要

| 指标 | 值 |
|------|-----|
| 总耗时 | 45.2 秒 |
| 任务 ID | `job_abc123` |
| 评估状态 | 成功 |

## 评估结果

| 维度 | 分数 |
|------|------|
| 步行性 | 85.5 |
| 安全性 | 78.2 |
| 美观性 | 72.0 |
| **综合** | 79.8 |
```

---

## 10. 可重复性验证

### Test-Pipeline 独有功能

```python
# test_workflow.py L774-835
def run_verify_repeatability(client, preset, timeout=300.0):
    # 运行两次
    run1 = run_test(client, preset, timeout=timeout)
    run2 = run_test(client, preset, timeout=timeout)

    # 对比结果
    validator = MetricsValidator()
    repeatability_passed, metric_differences = validator.validate_repeatability(
        run1.evaluation or {},
        run2.evaluation or {}
    )

    # 输出对比表
    for key, diff in metric_differences.items():
        print(f"  {key}: {diff['run1']:.2f} vs {diff['run2']:.2f} (差值: {diff['difference']:.6f})")
```

**用途**：验证系统在相同输入下产生一致的结果，用于科研实验的可重复性保证。

---

## 11. 随机种子管理

### Test-Pipeline

```python
# test_workflow.py L149-176
def set_global_seed(seed: int) -> None:
    random.seed(seed)
    try:
        import numpy as np
        np.random.seed(seed)
    except ImportError:
        pass
    try:
        import torch
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except ImportError:
        pass
```

- 支持 Python、NumPy、PyTorch 随机种子
- 默认种子：42
- 可通过 `--seed` 参数指定

### Workbench Web

❌ 无随机种子管理（使用系统时间作为种子）

---

## 12. 超时控制对比

### Test-Pipeline

```python
# 使用 SIGALRM 实现系统级超时
@contextlib.contextmanager
def timeout_context(seconds: float, task_name: str = "任务"):
    def timeout_handler(signum, frame):
        raise TimeoutError(f"{task_name} 超时 ({seconds}秒)")

    old_handler = signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(int(seconds))
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)
```

### Workbench Web

- ❌ 无系统级超时控制
- 依赖浏览器 fetch 的超时（默认较长）
- MAX_GENERATION_ATTEMPTS (120) × POLL_INTERVAL_MS (1500ms) = 180s 软上限

---

## 13. 关键代码路径

### Workbench Web

| 文件 | 职责 |
|------|------|
| `web/workbench/src/App.tsx` | 主应用，3-step 工作流 |
| `web/workbench/src/hooks/useGeneration.ts` | 生成逻辑，API 调用 |
| `web/workbench/src/lib/types.ts` | 类型定义，预设配置 |
| `web/workbench/src/components/PresetGrid.tsx` | 预设选择 UI |
| `web/workbench/src/components/SchemeGrid.tsx` | 方案展示 UI |
| `web/workbench/src/components/EvaluationPanel.tsx` | 评估结果图表 |
| `web/api/main.py` | FastAPI 后端入口 |

### Test-Pipeline

| 文件 | 职责 |
|------|------|
| `Makefile` | `test-pipeline` 目标定义 |
| `scripts/test_workflow.py` | 测试主逻辑 |
| `scripts/test_pipeline.py` | 报告汇总脚本 |

---

## 14. 总结建议

### Test-Pipeline 优于 Web 的方面

1. **可重复性**：随机种子控制
2. **超时保护**：系统级 SIGALRM
3. **验证机制**：评分公式验证
4. **详细日志**：每步状态输出
5. **报告生成**：Markdown 持久化
6. **错误追踪**：详细异常分类

### Web 优于 Test-Pipeline 的方面

1. **用户体验**：可视化界面
2. **交互性**：可选择不同方案对比
3. **实时反馈**：动态进度更新
4. **多维度展示**：雷达图、柱状图

### 建议整合方向

1. **统一预设配置**：抽取到共享的 JSON/YAML 文件
2. **增强 Web 进度**：使用真实的 API 轮询替代假进度
3. **添加报告导出**：Web 端也应支持生成 Markdown 报告
4. **种子控制**：为 Web 添加可配置的随机种子
5. **统一 API Payload**：消除两端请求结构的差异

---

## 附录：环境变量

### Workbench Web
```bash
VITE_ROADGEN_API_BASE=http://127.0.0.1:8010
VITE_ROADGEN_VIEWER_BASE=http://127.0.0.1:4173
```

### Test-Pipeline
使用 `.env` 文件或系统环境变量配置 LLM API。
