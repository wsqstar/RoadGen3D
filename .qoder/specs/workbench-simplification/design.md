# RoadGen3D Workbench UI 简化设计文档

## 概述

本文档描述 RoadGen3D Workbench UI 的简化设计方案，将现有的 5-Tab 复杂界面简化为清晰的 **3 步流程**：
1. **模板选择** → 2. **方案对比** → 3. **评估可视化**

### 设计目标

- **降低认知负担**：用户无需理解复杂参数系统，即可完成街道生成
- **提升决策效率**：多方案对比 + 量化评分，让最佳方案一目了然
- **保留专业入口**：简化流程不牺牲专业用户的高级配置能力

### 技术栈

| 组件 | 技术选型 |
|------|----------|
| 前端框架 | Svelte 4 + Vite 5 + TypeScript |
| 样式方案 | Tailwind CSS (现有) |
| 图表库 | Chart.js 4.x (雷达图、柱状图) |
| 3D 预览 | Three.js (集成 WebGL 渲染) |

---

## 当前状态分析

### 现有问题

| 问题 | 现状 | 影响 |
|------|------|------|
| Tab 过多 | 5 个 Tab (Conversation, Scene Setup, Evidence, Design Draft, Scene Jobs) | 用户需要频繁切换，难以形成连贯工作流 |
| 参数复杂 | 14+ 个可编辑参数 | 非专业用户望而却步 |
| Layout 模式混乱 | OSM, MetaUrban, GraphTemplate, Template 4 种模式 | 增加选择难度，代码维护成本高 |
| 预设过多 | 6 个预设卡片混在一起 | 视觉疲劳，难以快速决策 |
| RAG 复杂 | 证据引用系统层次深 | 用户不理解为何需要这些信息 |
| 流程断裂 | 参数确认 → 生成任务 → 查看结果 | 缺少方案对比和评估环节 |

### 现有架构

```
app.ts (59.4 KB)
├── State Management (内联状态对象)
├── Tab System (5 个 Tab)
├── Scene Presets (6 个预设)
├── Parameter Form (14+ 字段)
├── RAG Evidence System
└── Job Polling System
```

---

## 简化后架构

### 3 步流程设计

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Workbench 主界面                              │
├─────────────────────────────────────────────────────────────────────┤
│  [1. 模板选择] ──→ [2. 方案对比] ──→ [3. 评估可视化]                   │
│                                                                      │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐            │
│  │ 步骤指示器    │   │ 步骤指示器    │   │ 步骤指示器    │            │
│  │ ● ○ ○        │   │ ○ ● ○        │   │ ○ ○ ●        │            │
│  └──────────────┘   └──────────────┘   └──────────────┘            │
└─────────────────────────────────────────────────────────────────────┘
```

### 核心组件

| 组件 | 职责 | 位置 |
|------|------|------|
| `StepIndicator` | 3 步流程指示器 | 全局 Header |
| `TemplateSelector` | 可视化预设场景卡片选择 | Step 1 |
| `ComparisonGrid` | 多方案 (A/B/C) 并排展示 | Step 2 |
| `EvaluationPanel` | 雷达图 + 柱状图 + 详细指标 | Step 3 |
| `GenerationStatus` | 生成进度状态显示 | Step 2-3 |
| `ThreePreview` | 内嵌 3D 场景预览 | ComparisonGrid 卡片内 |

---

## 组件详细设计

### 1. StepIndicator (步骤指示器)

**位置**: 全局 Header 区域

**视觉设计**:
```
┌──────────────────────────────────────────────────────────────┐
│  RoadGen3D Workbench                                         │
│  ─────────────────────────────────────────────────────────  │
│  [●] 1. 选择模板    [○] 2. 对比方案    [○] 3. 评估结果       │
└──────────────────────────────────────────────────────────────┘
```

**状态**:
- `idle`: 当前步骤（实心圆 ●）
- `completed`: 已完成步骤（✓ 图标）
- `pending`: 待完成步骤（空心圆 ○）

**交互**:
- 点击已完成步骤可回退
- 当前步骤不可点击
- 切换时带有平滑过渡动画

---

### 2. TemplateSelector (模板选择器)

**位置**: Step 1 区域

**视觉设计**:
```
┌──────────────────────────────────────────────────────────────┐
│  选择街道场景模板                                             │
│  ─────────────────────────────────────────────────────────  │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐ │
│  │   [图标]       │  │   [图标]       │  │   [图标]       │ │
│  │                │  │                │  │                │ │
│  │ 步行友好       │  │ 公交优先       │  │ 商业活力       │ │
│  │ Pedestrian     │  │ Transit        │  │ Commercial     │ │
│  │                │  │                │  │                │ │
│  │ 安静、安全、   │  │ 公交可达、     │  │ 商业活跃、     │ │
│  │ 全龄友好       │  │ 换乘便利       │  │ 客流密集       │ │
│  └────────────────┘  └────────────────┘  └────────────────┘ │
│                                                              │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐ │
│  │   [图标]       │  │   [图标]       │  │   [图标]       │ │
│  │                │  │                │  │                │ │
│  │ 公园景观       │  │ 城市核心       │  │ 滨水休闲       │ │
│  │ Park View      │  │ Urban Core     │  │ Waterfront     │ │
│  │                │  │                │  │                │ │
│  │ 绿化丰富、     │  │ 高密度开发、   │  │ 景观步道、     │ │
│  │ 自然生态       │  │ 混合功能       │  │ 休闲座椅       │ │
│  └────────────────┘  └────────────────┘  └────────────────┘ │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  [⚙️ 高级模式]                                         │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│                            [下一步 → 生成方案]                │
└──────────────────────────────────────────────────────────────┘
```

**预设卡片设计**:

| ID | 名称 | 图标 | 描述 | 设计规则 |
|----|------|------|------|----------|
| `pedestrian` | 步行友好 | 🚶 | 安静、安全、全龄友好 | pedestrian_priority_v1 |
| `transit` | 公交优先 | 🚌 | 公交可达、换乘便利 | transit_priority_v1 |
| `commercial` | 商业活力 | 🛍️ | 商业活跃、客流密集 | balanced_complete_street_v1 |
| `park` | 公园景观 | 🌳 | 绿化丰富、自然生态 | pedestrian_priority_v1 |
| `urban_core` | 城市核心 | 🏙️ | 高密度开发、混合功能 | balanced_complete_street_v1 |
| `waterfront` | 滨水休闲 | 🌊 | 景观步道、休闲座椅 | pedestrian_priority_v1 |

**交互**:
- 悬停：卡片微微上浮 + 阴影加深
- 选中：蓝色边框 + 勾选标记
- 点击：平滑过渡到 Step 2

**高级模式入口**:
- 点击后展开完整参数配置面板
- 默认隐藏，保持主流程简洁
- 可保存自定义预设（localStorage）

---

### 3. ComparisonGrid (方案对比区)

**位置**: Step 2 区域

**视觉设计**:
```
┌──────────────────────────────────────────────────────────────┐
│  方案对比                                                    │
│  ─────────────────────────────────────────────────────────  │
│  正在生成 3 个方案，请稍候...                                  │
│                                                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐          │
│  │  [3D 预览]  │  │  [3D 预览]  │  │  [3D 预览]  │          │
│  │   加载中   │  │   加载中   │  │   加载中   │          │
│  │   ████░░  │  │   ██░░░░  │  │   ░░░░░░  │          │
│  └─────────────┘  └─────────────┘  └─────────────┘          │
│                                                              │
│  方案 A        │  方案 B        │  方案 C                     │
│  生成中...     │  生成中...     │  等待中...                  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**生成完成后的设计**:
```
┌──────────────────────────────────────────────────────────────┐
│  方案对比                                    [查看详细指标]   │
│  ─────────────────────────────────────────────────────────  │
│                                                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐          │
│  │  [3D 预览]  │  │  [3D 预览]  │  │  [3D 预览]  │          │
│  │             │  │             │  │             │          │
│  │   [渲染图]  │  │   [渲染图]  │  │   [渲染图]  │          │
│  └─────────────┘  └─────────────┘  └─────────────┘          │
│                                                              │
│  方案 A        │  方案 B ⭐      │  方案 C                     │
│  ⭐ 推荐       │  最佳综合      │                              │
│  ──────────    │  ──────────    │  ──────────                │
│  综合: 78     │  综合: 85     │  综合: 72                   │
│  步行: 82     │  步行: 88     │  步行: 75                   │
│  安全: 75     │  安全: 82     │  安全: 70                   │
│  美观: 78     │  美观: 85     │  美观: 72                   │
│  ──────────    │  ──────────    │  ──────────                │
│  [选择此方案]  │  [选择此方案]  │  [选择此方案]                │
│              │  ✓ 已选择     │                              │
└──────────────────────────────────────────────────────────────┘
```

**方案卡片结构**:
```typescript
interface SceneOption {
  id: string;              // "A", "B", "C"
  status: "generating" | "completed" | "failed";
  progress?: number;        // 0-100
  previewImage?: string;    // 渲染图 URL
  threeSceneUrl?: string;   // Three.js 场景 URL
  scores?: {
    overall: number;        // 综合评分
    walkability: number;    // 步行性
    safety: number;        // 安全性
    beauty: number;         // 美观度
  };
  selected?: boolean;
}
```

**交互**:
- 3D 预览：卡片内嵌 Three.js 渲染
- 悬停：预览图放大
- 点击"查看详情"：展开参数详情面板
- 点击"选择此方案"：高亮选中，标记为推荐

---

### 4. EvaluationPanel (评估可视化)

**位置**: Step 3 区域

**视觉设计**:
```
┌──────────────────────────────────────────────────────────────┐
│  评估结果                              [导出报告] [重新生成] │
│  ─────────────────────────────────────────────────────────  │
│                                                              │
│  已选择: 方案 B                                             │
│  ─────────────────────────────────────────────────────────  │
│                                                              │
│  ┌─────────────────────────────┐  ┌───────────────────────┐│
│  │      综合评分: 85            │  │   权重说明              ││
│  │         ╭───╮               │  │   ─────────────         ││
│  │        ╱     ╲              │  │   步行性: 45%           ││
│  │       ╱   ★    ╲             │  │   安全性: 35%           ││
│  │      ╱    │     ╲            │  │   美观度: 20%           ││
│  │     ──────┼──────            │  │                         ││
│  │      步行   安   美          │  │   计算公式:              ││
│  │              │              │  │   S = 0.45×W +          ││
│  │             全              │  │     0.35×S + 0.20×B    ││
│  │                             │  │                         ││
│  │      [雷达图]               │  │                         ││
│  └─────────────────────────────┘  └───────────────────────┘│
│                                                              │
│  指标对比                                                    │
│  ─────────────────────────────────────────────────────────  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  指标名称        方案A   方案B   方案C   参考值         │  │
│  │  ─────────────────────────────────────────────────   │  │
│  │  人行道净宽      85      92      78      90           │  │
│  │  照明均匀度      72      88      65      80           │  │
│  │  绿化遮荫率      68      75      82      70           │  │
│  │  公交站可达性    80      85      60      75           │  │
│  │  ...                                                │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌────────────────┐                                        │
│  │ 柱状图对比     │                                        │
│  │  ████          │                                        │
│  │  ████  ████    │                                        │
│  │  ████  ████  ██│                                        │
│  │  ────────────  │                                        │
│  │  步行  安全  美 │                                        │
│  └────────────────┘                                        │
│                                                              │
│                            [导出 3D 场景] [修改方案]         │
└──────────────────────────────────────────────────────────────┘
```

**雷达图设计**:

| 维度 | 指标 | 权重 |
|------|------|------|
| 步行性 (W) | 11 个指标综合 | 0.45 |
| 安全性 (S) | 速度合规、照明、安全感知 | 0.35 |
| 美观度 (B) | 美学、绿化、街道家具 | 0.20 |

**综合评分计算**:
```
综合评分 = 0.45 × 步行性 + 0.35 × 安全性 + 0.20 × 美观度
```

**Chart.js 配置**:
```typescript
// 雷达图配置
{
  type: 'radar',
  data: {
    labels: ['步行性', '安全性', '美观度'],
    datasets: [
      { label: '方案 A', data: [82, 75, 78], borderColor: '#3B82F6' },
      { label: '方案 B', data: [88, 82, 85], borderColor: '#10B981' },
      { label: '方案 C', data: [75, 70, 72], borderColor: '#F59E0B' },
    ]
  },
  options: {
    scales: { r: { min: 0, max: 100 } },
    plugins: { legend: { position: 'bottom' } }
  }
}
```

---

## 数据流设计

### 简化后的 API 调用流程

```
用户选择模板
     ↓
POST /api/generate/batch
{
  "template_id": "pedestrian",
  "count": 3,
  "seed_offset": 0
}
     ↓
[异步生成 3 个方案]
     ↓
GET /api/scene/jobs/{job_id}  (轮询)
     ↓
方案生成完成
     ↓
POST /api/evaluate/batch
{
  "scene_ids": ["job_a", "job_b", "job_c"]
}
     ↓
返回评估结果
{
  "evaluations": [
    {
      "scene_id": "job_a",
      "scores": { "walkability": 0.82, "safety": 0.75, "beauty": 0.78 },
      "overall": 78,
      "indicators": { ... }
    },
    ...
  ]
}
```

### 现有 API 复用

| API 端点 | 用途 | 复用方式 |
|----------|------|----------|
| `POST /api/design/draft` | 生成设计草案 | 保留，高级模式使用 |
| `POST /api/scene/jobs` | 创建生成任务 | 合并为批量生成 |
| `GET /api/scene/jobs/{id}` | 查询任务状态 | 保留，轮询进度 |
| `/api/graph-templates` | 加载模板 | 保留 |

### 新增 API

| 端点 | 方法 | 描述 |
|------|------|------|
| `/api/generate/batch` | POST | 批量生成多个方案 |
| `/api/evaluate/batch` | POST | 批量评估多个场景 |
| `/api/generate/progress` | GET | 获取批量生成进度 |

---

## 评估模块集成

### 数据来源

评估模块位于 `src/roadgen3d/eval_quality.py`，提供以下评估能力：

**步行性评估** (Walkability):
```python
indicators = {
    "SID_CLR": clear_width_score,      # 人行道净宽
    "CLEAR_CONT": clear_continuity,   # 净空连续性
    "FURN_D": furnishing_density,      # 街道家具密度
    "LIGHT_UNI": lamp_uniformity,      # 照明均匀度
    "TREE_SHADE": tree_shade,          # 绿化遮荫率
    "BUFFER_RATIO": buffer_ratio,     # 缓冲带比例
    "TRANSIT_PROX": transit_proximity, # 公交站可达性
    "CROSS_PROV": crossing_provision,  # 过街设施
    "ENTR_DENS": entrance_density,    # 入口密度
    "POI_MIX": poi_mix,                # POI 混合度
    "MICRO_ENV": micro_environment,   # 微气候环境
}
```

**安全性评估** (Safety):
```python
structural_score = (
    0.15 * cross_prov +
    0.15 * light_uni +
    0.10 * buffer_ratio +
    0.10 * bollard_density +
    0.10 * visibility_penalty
)
```

**美观度评估** (Beauty):
```python
structural_score = (
    0.40 * presentation_score +
    0.10 * active_front_ratio +
    0.10 * anchor_poi +
    0.10 * (1.0 - visual_clutter)
)
```

### 评估结果可视化映射

| 评估维度 | 雷达图标签 | 柱状图颜色 |
|----------|------------|------------|
| 步行性 | W (Walkability) | #3B82F6 (蓝色) |
| 安全性 | S (Safety) | #EF4444 (红色) |
| 美观度 | B (Beauty) | #10B981 (绿色) |

---

## UI 布局设计

### 响应式断点

| 断点 | 宽度 | 布局变化 |
|------|------|----------|
| Mobile | < 640px | 单列堆叠，3D 预览占满宽度 |
| Tablet | 640-1024px | 2 列网格 |
| Desktop | > 1024px | 3 列网格，评估面板侧边显示 |

### 主布局结构

```html
<div class="workbench">
  <!-- Header -->
  <header class="header">
    <h1>RoadGen3D Workbench</h1>
    <StepIndicator current={step} />
    <a href={viewerUrl} target="_blank">打开独立 Viewer</a>
  </header>

  <!-- Main Content -->
  <main class="content">
    {#if step === 1}
      <TemplateSelector on:select={handleTemplateSelect} />
    {:else if step === 2}
      <ComparisonGrid options={sceneOptions} on:select={handleOptionSelect} />
    {:else if step === 3}
      <EvaluationPanel selected={selectedOption} evaluations={evaluations} />
    {/if}
  </main>

  <!-- Footer Actions -->
  <footer class="footer">
    {#if step > 1}
      <button on:click={() => step--}>← 上一步</button>
    {/if}
    {#if step < 3}
      <button on:click={() => step++}>下一步 →</button>
    {:else}
      <button on:click={exportScene}>导出 3D 场景</button>
    {/if}
  </footer>
</div>
```

---

## 样式设计

### 颜色系统

```css
:root {
  /* 主色 */
  --primary: #3B82F6;
  --primary-hover: #2563EB;

  /* 评分色 */
  --walkability: #3B82F6;
  --safety: #EF4444;
  --beauty: #10B981;

  /* 状态色 */
  --success: #10B981;
  --warning: #F59E0B;
  --error: #EF4444;

  /* 背景 */
  --bg-primary: #FFFFFF;
  --bg-secondary: #F3F4F6;
  --bg-card: #FFFFFF;

  /* 文字 */
  --text-primary: #111827;
  --text-secondary: #6B7280;
}
```

### 组件样式

```css
/* 模板卡片 */
.template-card {
  @apply relative bg-white rounded-xl p-6 shadow-sm
         border-2 border-transparent cursor-pointer
         transition-all duration-200 hover:shadow-md hover:-translate-y-1;
}

.template-card.selected {
  @apply border-blue-500 ring-2 ring-blue-200;
}

/* 方案卡片 */
.option-card {
  @apply bg-white rounded-xl overflow-hidden shadow-sm
         transition-all duration-200 hover:shadow-lg;
}

.option-card.selected {
  @apply ring-4 ring-green-400;
}

/* 评分标签 */
.score-badge {
  @apply inline-flex items-center px-3 py-1 rounded-full
         text-sm font-medium;
}

.score-badge.walkability {
  @apply bg-blue-100 text-blue-800;
}

.score-badge.safety {
  @apply bg-red-100 text-red-800;
}

.score-badge.beauty {
  @apply bg-green-100 text-green-800;
}
```

---

## 文件结构

### 新增文件

```
web/workbench/src/
├── app.ts                      # 现有主文件 (重构)
├── components/
│   ├── StepIndicator.svelte    # 步骤指示器
│   ├── TemplateSelector.svelte # 模板选择器
│   ├── TemplateCard.svelte     # 模板卡片组件
│   ├── ComparisonGrid.svelte   # 方案对比网格
│   ├── OptionCard.svelte        # 方案卡片组件
│   ├── EvaluationPanel.svelte  # 评估可视化面板
│   ├── RadarChart.svelte       # 雷达图组件
│   ├── BarChart.svelte         # 柱状图组件
│   ├── IndicatorTable.svelte   # 指标表格
│   └── ThreePreview.svelte      # 3D 预览组件
├── stores/
│   ├── workbench.ts            # 主状态管理
│   └── evaluation.ts           # 评估结果状态
├── api/
│   ├── generate.ts             # 批量生成 API
│   └── evaluate.ts             # 批量评估 API
├── types/
│   └── workbench.ts            # 简化后的类型定义
└── styles/
    └── workbench.css           # 组件样式
```

### 重构文件

| 文件 | 变更 |
|------|------|
| `app.ts` | 重构为组件化架构，移除内联 HTML |
| `types.ts` | 添加简化后的类型定义 |
| `api.ts` | 添加批量生成/评估 API |
| `style.css` | 添加新组件样式 |

---

## 实现注意事项

### 1. 向后兼容

- 保留现有 API 端点
- 高级模式入口允许访问完整功能
- localStorage 保存用户偏好

### 2. 性能优化

- Three.js 预览使用懒加载
- Chart.js 按需引入
- 批量生成使用 Web Worker
- 评估结果缓存

### 3. 错误处理

- 生成失败：显示重试按钮
- 评估超时：允许跳过
- 3D 预览失败：显示静态图兜底

### 4. 辅助功能

- 键盘导航支持
- 屏幕阅读器标签
- 高对比度模式支持

---

## 实施计划

### Phase 1: 基础设施
- [ ] 创建组件目录结构
- [ ] 迁移样式到独立 CSS 文件
- [ ] 实现 StepIndicator 组件

### Phase 2: 模板选择
- [ ] 实现 TemplateSelector 组件
- [ ] 实现 TemplateCard 组件
- [ ] 添加动画过渡效果

### Phase 3: 方案对比
- [ ] 实现 ComparisonGrid 组件
- [ ] 集成 Three.js 预览
- [ ] 添加生成进度显示

### Phase 4: 评估可视化
- [ ] 实现 EvaluationPanel 组件
- [ ] 集成 Chart.js 图表
- [ ] 实现指标表格

### Phase 5: API 集成
- [ ] 添加批量生成 API
- [ ] 添加批量评估 API
- [ ] 实现进度轮询

### Phase 6: 测试与优化
- [ ] 端到端测试
- [ ] 性能优化
- [ ] 文档更新

---

## 附录

### A. 评估指标详解

| 指标 | 英文名 | 说明 | 理想值 |
|------|--------|------|--------|
| 人行道净宽 | SID_CLR | 人行道可用宽度 | ≥ 3.0m |
| 净空连续性 | CLEAR_CONT | 无障碍通道连续性 | ≥ 0.8 |
| 街道家具密度 | FURN_D | 座椅、垃圾桶等 | 0.1-0.2/m |
| 照明均匀度 | LIGHT_UNI | 路灯分布均匀性 | ≥ 0.8 |
| 绿化遮荫率 | TREE_SHADE | 树冠遮荫比例 | ≥ 0.3 |
| 缓冲带比例 | BUFFER_RATIO | 人车之间的缓冲 | ≥ 0.5 |
| 公交站可达性 | TRANSIT_PROX | 到公交站距离 | ≤ 30m |
| 过街设施 | CROSS_PROV | 人行横道密度 | ≥ 1/80m |
| 入口密度 | ENTR_DENS | 临街入口数量 | ≥ 4/100m |
| POI 混合度 | POI_MIX | 业态多样性 | ≥ 0.7 |
| 微气候环境 | MICRO_ENV | 舒适度综合 | ≥ 0.6 |

### B. 设计参考

- [Figma 设计稿链接] (待补充)
- [用户流程图] (待补充)
