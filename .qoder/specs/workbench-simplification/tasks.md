# RoadGen3D Workbench UI 简化 - 实施任务

## 概述

本任务清单详细描述了将 RoadGen3D Workbench 从 5-Tab 复杂界面简化为 3 步流程的具体实现步骤。

---

## Phase 1: 基础设施重构

### 1.1 创建目录结构

- [ ] 1.1.1 创建 `web/workbench/src/components/` 目录
- [ ] 1.1.2 创建 `web/workbench/src/stores/` 目录
- [ ] 1.1.3 创建 `web/workbench/src/api/` 目录
- [ ] 1.1.4 创建 `web/workbench/src/types/` 目录
- [ ] 1.1.5 创建 `web/workbench/src/styles/` 目录

**文件路径**:
- `web/workbench/src/components/`
- `web/workbench/src/stores/`
- `web/workbench/src/api/`
- `web/workbench/src/types/`
- `web/workbench/src/styles/`

### 1.2 提取样式到独立文件

- [ ] 1.2.1 从 `app.ts` 提取 `style.css` 内容到 `web/workbench/src/styles/workbench.css`
- [ ] 1.2.2 添加新组件的样式类定义
- [ ] 1.2.3 添加 CSS 变量主题系统

**文件路径**: `web/workbench/src/styles/workbench.css`

**关键样式定义**:
```css
/* 模板卡片 */
.template-card { ... }
.template-card.selected { ... }

/* 方案卡片 */
.option-card { ... }
.option-card.selected { ... }

/* 评分徽章 */
.score-badge { ... }
.score-badge.walkability { ... }
.score-badge.safety { ... }
.score-badge.beauty { ... }

/* 步骤指示器 */
.step-indicator { ... }
.step-indicator .step { ... }
.step-indicator .step.active { ... }
.step-indicator .step.completed { ... }
```

### 1.3 创建类型定义

- [ ] 1.3.1 创建简化后的类型定义文件

**文件路径**: `web/workbench/src/types/workbench.ts`

```typescript
// 步骤状态
export type WorkbenchStep = 1 | 2 | 3;

// 场景模板
export interface SceneTemplate {
  id: string;
  name: string;
  nameZh: string;
  description: string;
  icon: string;
  designRuleProfile: string;
  configPatch: Record<string, string | number>;
}

// 方案状态
export type OptionStatus = 'pending' | 'generating' | 'completed' | 'failed';

// 单个方案
export interface SceneOption {
  id: string;           // 'A', 'B', 'C'
  status: OptionStatus;
  progress: number;     // 0-100
  templateId: string;
  previewImage?: string;
  threeSceneUrl?: string;
  sceneLayoutPath?: string;
  sceneGlbPath?: string;
  scores?: OptionScores;
  selected: boolean;
}

// 评分
export interface OptionScores {
  overall: number;      // 综合评分
  walkability: number;  // 步行性 0-100
  safety: number;       // 安全性 0-100
  beauty: number;       // 美观度 0-100
}

// 评估指标
export interface EvaluationIndicators {
  sid_clr: number;          // 人行道净宽
  clear_cont: number;       // 净空连续性
  furn_d: number;           // 家具密度
  light_uni: number;        // 照明均匀度
  tree_shade: number;       // 绿化遮荫率
  buffer_ratio: number;     // 缓冲带比例
  transit_prox: number;     // 公交站可达性
  cross_prov: number;       // 过街设施
  entr_dens: number;        // 入口密度
  poi_mix: number;          // POI 混合度
  micro_env: number;        // 微气候环境
}

// 完整评估结果
export interface EvaluationResult {
  sceneId: string;
  scores: OptionScores;
  indicators: EvaluationIndicators;
  pillarScores: {
    protection: number;
    comfort: number;
    delight: number;
  };
}

// 批量生成请求
export interface BatchGenerateRequest {
  templateId: string;
  count: number;
  seedOffset: number;
}

// 批量生成响应
export interface BatchGenerateResponse {
  batchId: string;
  jobs: {
    jobId: string;
    optionId: string;
  }[];
}

// 批量评估请求
export interface BatchEvaluateRequest {
  sceneIds: string[];
}

// 批量评估响应
export interface BatchEvaluateResponse {
  evaluations: EvaluationResult[];
}
```

---

## Phase 2: 状态管理

### 2.1 创建 Svelte Store

- [ ] 2.1.1 创建主工作台状态 store
- [ ] 2.1.2 创建评估结果 store

**文件路径**: `web/workbench/src/stores/workbench.ts`

```typescript
import { writable, derived } from 'svelte/store';

// 当前步骤
export const currentStep = writable<WorkbenchStep>(1);

// 选中的模板
export const selectedTemplate = writable<SceneTemplate | null>(null);

// 方案列表
export const sceneOptions = writable<SceneOption[]>([
  { id: 'A', status: 'pending', progress: 0, templateId: '', selected: false },
  { id: 'B', status: 'pending', progress: 0, templateId: '', selected: false },
  { id: 'C', status: 'pending', progress: 0, templateId: '', selected: false },
]);

// 批量生成 ID
export const batchId = writable<string | null>(null);

// 派生的：选中的方案
export const selectedOption = derived(
  sceneOptions,
  $options => $options.find(o => o.selected) || null
);

// 派生的：是否有方案生成完成
export const hasCompletedOptions = derived(
  sceneOptions,
  $options => $options.some(o => o.status === 'completed')
);

// 重置状态
export function resetWorkbench() {
  currentStep.set(1);
  selectedTemplate.set(null);
  sceneOptions.set([
    { id: 'A', status: 'pending', progress: 0, templateId: '', selected: false },
    { id: 'B', status: 'pending', progress: 0, templateId: '', selected: false },
    { id: 'C', status: 'pending', progress: 0, templateId: '', selected: false },
  ]);
  batchId.set(null);
}
```

**文件路径**: `web/workbench/src/stores/evaluation.ts`

```typescript
import { writable, derived } from 'svelte/store';
import type { EvaluationResult } from '../types/workbench';

// 评估结果列表
export const evaluations = writable<EvaluationResult[]>([]);

// 派生的：最佳方案
export const bestOption = derived(
  evaluations,
  $evals => $evals.reduce((best, current) =>
    current.scores.overall > (best?.scores.overall ?? 0) ? current : best,
    null as EvaluationResult | null
  )
);

// 清空评估结果
export function clearEvaluations() {
  evaluations.set([]);
}
```

---

## Phase 3: 核心组件实现

### 3.1 StepIndicator (步骤指示器)

- [ ] 3.1.1 创建组件文件
- [ ] 3.1.2 实现步骤切换逻辑
- [ ] 3.1.3 添加过渡动画

**文件路径**: `web/workbench/src/components/StepIndicator.svelte`

```svelte
<script lang="ts">
  import type { WorkbenchStep } from '../types/workbench';

  export let current: WorkbenchStep = 1;

  const steps = [
    { num: 1, label: '选择模板' },
    { num: 2, label: '对比方案' },
    { num: 3, label: '评估结果' },
  ];

  function canNavigateTo(step: number): boolean {
    return step < current; // 只允许回退
  }
</script>

<div class="step-indicator">
  {#each steps as step}
    <button
      class="step"
      class:active={step.num === current}
      class:completed={step.num < current}
      disabled={!canNavigateTo(step.num)}
      on:click={() => canNavigateTo(step.num) && (current = step.num as WorkbenchStep)}
    >
      <span class="step-icon">
        {#if step.num < current}
          ✓
        {:else}
          {step.num}
        {/if}
      </span>
      <span class="step-label">{step.label}</span>
    </button>
    {#if step.num < steps.length}
      <div class="step-connector" class:active={step.num < current}></div>
    {/if}
  {/each}
</div>
```

### 3.2 TemplateSelector (模板选择器)

- [ ] 3.2.1 创建模板数据定义
- [ ] 3.2.2 创建 TemplateSelector 组件
- [ ] 3.2.3 实现卡片选择逻辑

**文件路径**: `web/workbench/src/components/TemplateSelector.svelte`

```svelte
<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import type { SceneTemplate } from '../types/workbench';
  import TemplateCard from './TemplateCard.svelte';

  export let onSelect: (template: SceneTemplate) => void;

  const templates: SceneTemplate[] = [
    {
      id: 'pedestrian',
      name: 'Pedestrian',
      nameZh: '步行友好',
      description: '安静、安全、全龄友好',
      icon: '🚶',
      designRuleProfile: 'pedestrian_priority_v1',
      configPatch: { density: 0.3, ped_demand_level: 'high' },
    },
    {
      id: 'transit',
      name: 'Transit',
      nameZh: '公交优先',
      description: '公交可达、换乘便利',
      icon: '🚌',
      designRuleProfile: 'transit_priority_v1',
      configPatch: { density: 0.85, transit_demand_level: 'high' },
    },
    {
      id: 'commercial',
      name: 'Commercial',
      nameZh: '商业活力',
      description: '商业活跃、客流密集',
      icon: '🛍️',
      designRuleProfile: 'balanced_complete_street_v1',
      configPatch: { density: 0.8, ped_demand_level: 'high' },
    },
    {
      id: 'park',
      name: 'Park View',
      nameZh: '公园景观',
      description: '绿化丰富、自然生态',
      icon: '🌳',
      designRuleProfile: 'pedestrian_priority_v1',
      configPatch: { density: 0.2, ped_demand_level: 'medium' },
    },
    {
      id: 'urban_core',
      name: 'Urban Core',
      nameZh: '城市核心',
      description: '高密度开发、混合功能',
      icon: '🏙️',
      designRuleProfile: 'balanced_complete_street_v1',
      configPatch: { density: 0.9, ped_demand_level: 'high' },
    },
    {
      id: 'waterfront',
      name: 'Waterfront',
      nameZh: '滨水休闲',
      description: '景观步道、休闲座椅',
      icon: '🌊',
      designRuleProfile: 'pedestrian_priority_v1',
      configPatch: { density: 0.5, ped_demand_level: 'medium' },
    },
  ];

  let selectedId: string | null = null;

  function handleSelect(template: SceneTemplate) {
    selectedId = template.id;
  }

  function handleConfirm() {
    const template = templates.find(t => t.id === selectedId);
    if (template) {
      onSelect(template);
    }
  }
</script>

<div class="template-selector">
  <h2>选择街道场景模板</h2>

  <div class="template-grid">
    {#each templates as template}
      <TemplateCard
        {template}
        selected={template.id === selectedId}
        on:click={() => handleSelect(template)}
      />
    {/each}
  </div>

  <div class="actions">
    <button
      class="btn btn-secondary"
      on:click={() => showAdvancedMode = true}
    >
      ⚙️ 高级模式
    </button>

    <button
      class="btn btn-primary"
      disabled={!selectedId}
      on:click={handleConfirm}
    >
      下一步 → 生成方案
    </button>
  </div>
</div>
```

### 3.3 TemplateCard (模板卡片)

- [ ] 3.3.1 创建卡片组件
- [ ] 3.3.2 添加悬停和选中状态样式

**文件路径**: `web/workbench/src/components/TemplateCard.svelte`

```svelte
<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import type { SceneTemplate } from '../types/workbench';

  export let template: SceneTemplate;
  export let selected: boolean = false;
</script>

<button
  class="template-card"
  class:selected
  on:click
>
  <div class="card-icon">{template.icon}</div>
  <h3 class="card-title">{template.nameZh}</h3>
  <p class="card-subtitle">{template.name}</p>
  <p class="card-desc">{template.description}</p>

  {#if selected}
    <div class="selected-badge">✓</div>
  {/if}
</button>
```

### 3.4 ComparisonGrid (方案对比区)

- [ ] 3.4.1 创建方案对比网格组件
- [ ] 3.4.2 实现进度显示
- [ ] 3.4.3 集成 OptionCard 组件

**文件路径**: `web/workbench/src/components/ComparisonGrid.svelte`

```svelte
<script lang="ts">
  import { createEventDispatcher, onMount, onDestroy } from 'svelte';
  import type { SceneOption } from '../types/workbench';
  import OptionCard from './OptionCard.svelte';

  export let options: SceneOption[] = [];
  export let onSelect: (option: SceneOption) => void;

  let pollInterval: number | null = null;

  onMount(() => {
    // 开始轮询进度
    startPolling();
  });

  onDestroy(() => {
    stopPolling();
  });

  function startPolling() {
    pollInterval = setInterval(async () => {
      // TODO: 调用 API 获取进度
      // await refreshProgress();
    }, 1200);
  }

  function stopPolling() {
    if (pollInterval) {
      clearInterval(pollInterval);
      pollInterval = null;
    }
  }

  $: allCompleted = options.every(o => o.status === 'completed');
</script>

<div class="comparison-grid">
  <h2>方案对比</h2>

  {#if !allCompleted}
    <div class="status-bar">
      正在生成 {options.length} 个方案，请稍候...
    </div>
  {/if}

  <div class="options-row">
    {#each options as option}
      <OptionCard
        {option}
        on:click={() => onSelect(option)}
      />
    {/each}
  </div>
</div>
```

### 3.5 OptionCard (方案卡片)

- [ ] 3.5.1 创建方案卡片组件
- [ ] 3.5.2 集成 Three.js 预览
- [ ] 3.5.3 显示评分信息

**文件路径**: `web/workbench/src/components/OptionCard.svelte`

```svelte
<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import type { SceneOption } from '../types/workbench';
  import ThreePreview from './ThreePreview.svelte';

  export let option: SceneOption;
  export let rank: number | null = null;  // 排名，如 1 表示最佳
</script>

<div
  class="option-card"
  class:selected={option.selected}
  class:generating={option.status === 'generating'}
>
  {#if rank === 1}
    <div class="best-badge">⭐ 最佳方案</div>
  {/if}

  <div class="preview-container">
    {#if option.status === 'completed' && option.previewImage}
      <ThreePreview
        sceneUrl={option.threeSceneUrl}
        fallbackImage={option.previewImage}
      />
    {:else}
      <div class="preview-placeholder">
        {#if option.status === 'generating'}
          <div class="progress-ring">
            <svg viewBox="0 0 36 36">
              <path
                class="progress-bg"
                d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
              />
              <path
                class="progress-bar"
                stroke-dasharray="{option.progress}, 100"
                d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
              />
            </svg>
            <span class="progress-text">{option.progress}%</span>
          </div>
          <p>生成中...</p>
        {:else}
          <p>等待生成</p>
        {/if}
      </div>
    {/if}
  </div>

  <div class="card-body">
    <h3 class="option-id">方案 {option.id}</h3>

    {#if option.scores && option.status === 'completed'}
      <div class="scores">
        <div class="score-row main">
          <span>综合</span>
          <span class="score-value">{option.scores.overall}</span>
        </div>
        <div class="score-row">
          <span class="walkability">步行性</span>
          <span>{option.scores.walkability}</span>
        </div>
        <div class="score-row">
          <span class="safety">安全性</span>
          <span>{option.scores.safety}</span>
        </div>
        <div class="score-row">
          <span class="beauty">美观度</span>
          <span>{option.scores.beauty}</span>
        </div>
      </div>

      <button
        class="btn-select"
        class:selected={option.selected}
        on:click
      >
        {option.selected ? '✓ 已选择' : '选择此方案'}
      </button>
    {:else if option.status === 'generating'}
      <p class="generating-text">生成中 {option.progress}%</p>
    {:else}
      <p class="pending-text">等待中...</p>
    {/if}
  </div>
</div>
```

### 3.6 ThreePreview (3D 预览组件)

- [ ] 3.6.1 创建 Three.js 预览组件
- [ ] 3.6.2 实现懒加载
- [ ] 3.6.3 添加降级处理

**文件路径**: `web/workbench/src/components/ThreePreview.svelte`

```svelte
<script lang="ts">
  import { onMount } from 'svelte';

  export let sceneUrl: string | undefined = undefined;
  export let fallbackImage: string | undefined = undefined;

  let container: HTMLDivElement;
  let canvas: HTMLCanvasElement;
  let loaded = false;
  let useFallback = false;

  onMount(async () => {
    if (!sceneUrl) {
      useFallback = true;
      return;
    }

    try {
      // 懒加载 Three.js
      const THREE = await import('three');
      const { OrbitControls } = await import('three/examples/jsm/controls/OrbitControls.js');

      // 初始化场景
      const scene = new THREE.Scene();
      scene.background = new THREE.Color(0x1a1a2e);

      // 相机
      const camera = new THREE.PerspectiveCamera(
        75,
        container.clientWidth / container.clientHeight,
        0.1,
        1000
      );
      camera.position.set(5, 5, 5);

      // 渲染器
      const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
      renderer.setSize(container.clientWidth, container.clientHeight);

      // 控制器
      const controls = new OrbitControls(camera, renderer.domElement);

      // TODO: 加载 GLTF 模型
      // const gltf = await new THREE.GLTFLoader().loadAsync(sceneUrl);
      // scene.add(gltf.scene);

      // 渲染循环
      function animate() {
        requestAnimationFrame(animate);
        controls.update();
        renderer.render(scene, camera);
      }
      animate();

      loaded = true;
    } catch (error) {
      console.warn('Three.js 加载失败，使用降级图片', error);
      useFallback = true;
    }
  });
</script>

<div class="three-preview" bind:this={container}>
  {#if useFallback && fallbackImage}
    <img src={fallbackImage} alt="场景预览" class="fallback-image" />
  {:else if !useFallback}
    <canvas bind:this={canvas}></canvas>
  {/if}
</div>
```

### 3.7 EvaluationPanel (评估可视化)

- [ ] 3.7.1 创建评估面板组件
- [ ] 3.7.2 集成雷达图
- [ ] 3.7.3 集成柱状图
- [ ] 3.7.4 实现指标表格

**文件路径**: `web/workbench/src/components/EvaluationPanel.svelte`

```svelte
<script lang="ts">
  import { onMount } from 'svelte';
  import type { EvaluationResult, OptionScores } from '../types/workbench';
  import RadarChart from './RadarChart.svelte';
  import BarChart from './BarChart.svelte';
  import IndicatorTable from './IndicatorTable.svelte';

  export let evaluations: EvaluationResult[] = [];
  export let selectedId: string | null = null;

  let radarCanvas: HTMLCanvasElement;
  let barCanvas: HTMLCanvasElement;

  $: selectedEval = evaluations.find(e => e.sceneId === selectedId);
  $: bestEval = evaluations.reduce((best, current) =>
    current.scores.overall > (best?.scores.overall ?? 0) ? current : best,
    null as EvaluationResult | null
  );

  onMount(async () => {
    await renderCharts();
  });

  $: if (evaluations.length > 0) {
    renderCharts();
  }

  async function renderCharts() {
    const { Chart, registerables } = await import('chart.js');
    Chart.register(...registerables);

    // 雷达图
    if (radarCanvas) {
      new Chart(radarCanvas, {
        type: 'radar',
        data: {
          labels: ['步行性', '安全性', '美观度'],
          datasets: evaluations.map((e, i) => ({
            label: `方案 ${['A', 'B', 'C'][i]}`,
            data: [
              e.scores.walkability,
              e.scores.safety,
              e.scores.beauty,
            ],
            borderColor: ['#3B82F6', '#10B981', '#F59E0B'][i],
            backgroundColor: ['#3B82F620', '#10B98120', '#F59E0B20'][i],
          })),
        },
        options: {
          scales: { r: { min: 0, max: 100 } },
          plugins: { legend: { position: 'bottom' } },
        },
      });
    }

    // 柱状图
    if (barCanvas) {
      new Chart(barCanvas, {
        type: 'bar',
        data: {
          labels: ['步行性', '安全性', '美观度'],
          datasets: evaluations.map((e, i) => ({
            label: `方案 ${['A', 'B', 'C'][i]}`,
            data: [
              e.scores.walkability,
              e.scores.safety,
              e.scores.beauty,
            ],
            backgroundColor: ['#3B82F6', '#10B981', '#F59E0B'][i],
          })),
        },
        options: {
          scales: { y: { min: 0, max: 100 } },
          plugins: { legend: { position: 'bottom' } },
        },
      });
    }
  }
</script>

<div class="evaluation-panel">
  <h2>评估结果</h2>

  <div class="panel-header">
    <div class="selected-info">
      {#if selectedEval}
        已选择: 方案 {selectedEval.sceneId}
        (综合评分: {selectedEval.scores.overall})
      {:else}
        请选择一个方案
      {/if}
    </div>
    <div class="actions">
      <button class="btn btn-secondary">导出报告</button>
      <button class="btn btn-secondary">重新生成</button>
    </div>
  </div>

  <div class="charts-row">
    <div class="chart-card">
      <h3>综合评分: {bestEval?.scores.overall ?? '-'}</h3>
      <canvas bind:this={radarCanvas}></canvas>
      <p class="chart-legend">权重说明: 步行性 45% + 安全性 35% + 美观度 20%</p>
    </div>

    <div class="weight-info">
      <h4>评分权重</h4>
      <div class="weight-item">
        <div class="weight-bar" style="--width: 45%"></div>
        <span>步行性: 45%</span>
      </div>
      <div class="weight-item">
        <div class="weight-bar" style="--width: 35%"></div>
        <span>安全性: 35%</span>
      </div>
      <div class="weight-item">
        <div class="weight-bar" style="--width: 20%"></div>
        <span>美观度: 20%</span>
      </div>

      <div class="formula">
        <h4>计算公式</h4>
        <code>S = 0.45×W + 0.35×S + 0.20×B</code>
      </div>
    </div>
  </div>

  <div class="comparison-section">
    <h3>指标对比</h3>
    <canvas bind:this={barCanvas}></canvas>
  </div>

  <div class="details-section">
    <h3>详细指标</h3>
    {#if selectedEval}
      <IndicatorTable indicators={selectedEval.indicators} />
    {:else}
      <p class="no-selection">请选择一个方案查看详细指标</p>
    {/if}
  </div>

  <div class="panel-footer">
    <button class="btn btn-primary">导出 3D 场景</button>
    <button class="btn btn-secondary">修改方案</button>
  </div>
</div>
```

### 3.8 RadarChart (雷达图组件)

- [ ] 3.8.1 创建雷达图组件（可独立使用）
- [ ] 3.8.2 支持多数据集对比

**文件路径**: `web/workbench/src/components/RadarChart.svelte`

### 3.9 BarChart (柱状图组件)

- [ ] 3.9.1 创建柱状图组件
- [ ] 3.9.2 支持分组柱状图

**文件路径**: `web/workbench/src/components/BarChart.svelte`

### 3.10 IndicatorTable (指标表格)

- [ ] 3.10.1 创建指标表格组件
- [ ] 3.10.2 实现指标分类显示

**文件路径**: `web/workbench/src/components/IndicatorTable.svelte`

```svelte
<script lang="ts">
  import type { EvaluationIndicators } from '../types/workbench';

  export let indicators: EvaluationIndicators;

  const categories = [
    {
      name: '保护性 (Protection)',
      items: [
        { key: 'light_uni', label: '照明均匀度', ref: 80 },
        { key: 'buffer_ratio', label: '缓冲带比例', ref: 50 },
        { key: 'cross_prov', label: '过街设施', ref: 80 },
      ]
    },
    {
      name: '舒适性 (Comfort)',
      items: [
        { key: 'sid_clr', label: '人行道净宽', ref: 90 },
        { key: 'clear_cont', label: '净空连续性', ref: 80 },
        { key: 'tree_shade', label: '绿化遮荫率', ref: 30 },
        { key: 'micro_env', label: '微气候环境', ref: 60 },
      ]
    },
    {
      name: '愉悦性 (Delight)',
      items: [
        { key: 'furn_d', label: '家具密度', ref: 80 },
        { key: 'transit_prox', label: '公交可达', ref: 75 },
        { key: 'entr_dens', label: '入口密度', ref: 40 },
        { key: 'poi_mix', label: 'POI 混合度', ref: 70 },
      ]
    },
  ];

  function formatValue(key: string): string {
    return ((indicators as any)[key] * 100).toFixed(0);
  }

  function getStatus(key: string, ref: number): 'good' | 'warning' | 'bad' {
    const value = (indicators as any)[key] * 100;
    if (value >= ref) return 'good';
    if (value >= ref * 0.8) return 'warning';
    return 'bad';
  }
</script>

<table class="indicator-table">
  <thead>
    <tr>
      <th>指标名称</th>
      <th>当前值</th>
      <th>参考值</th>
      <th>状态</th>
    </tr>
  </thead>
  <tbody>
    {#each categories as category}
      <tr class="category-header">
        <td colspan="4">{category.name}</td>
      </tr>
      {#each category.items as item}
        <tr>
          <td>{item.label}</td>
          <td class="value">{formatValue(item.key)}</td>
          <td class="ref">{item.ref}</td>
          <td>
            <span class="status-badge {getStatus(item.key, item.ref)}">
              {#if getStatus(item.key, item.ref) === 'good'}✓{:else if getStatus(item.key, item.ref) === 'warning'}~{:else}✗{/if}
            </span>
          </td>
        </tr>
      {/each}
    {/each}
  </tbody>
</table>
```

---

## Phase 4: API 集成

### 4.1 批量生成 API

- [ ] 4.1.1 创建 `web/workbench/src/api/generate.ts`
- [ ] 4.1.2 实现批量生成请求
- [ ] 4.1.3 实现进度轮询

**文件路径**: `web/workbench/src/api/generate.ts`

```typescript
import { postJson, getJson } from './api';
import type { BatchGenerateRequest, BatchGenerateResponse } from '../types/workbench';

const API_BASE = import.meta.env.VITE_ROADGEN_API_BASE || 'http://127.0.0.1:8010';

export async function batchGenerate(request: BatchGenerateRequest): Promise<BatchGenerateResponse> {
  return postJson<BatchGenerateResponse>(`${API_BASE}/api/generate/batch`, request);
}

export async function getBatchProgress(batchId: string): Promise<{
  status: 'running' | 'completed' | 'failed';
  jobs: { optionId: string; status: string; progress: number; result?: any }[];
}> {
  return getJson(`${API_BASE}/api/generate/batch/${batchId}/progress`);
}

export async function getJobStatus(jobId: string): Promise<any> {
  return getJson(`${API_BASE}/api/scene/jobs/${jobId}`);
}
```

### 4.2 批量评估 API

- [ ] 4.2.1 创建 `web/workbench/src/api/evaluate.ts`
- [ ] 4.2.2 实现批量评估请求

**文件路径**: `web/workbench/src/api/evaluate.ts`

```typescript
import { postJson } from './api';
import type { BatchEvaluateRequest, BatchEvaluateResponse } from '../types/workbench';

const API_BASE = import.meta.env.VITE_ROADGEN_API_BASE || 'http://127.0.0.1:8010';

export async function batchEvaluate(request: BatchEvaluateRequest): Promise<BatchEvaluateResponse> {
  return postJson<BatchEvaluateResponse>(`${API_BASE}/api/evaluate/batch`, request);
}
```

### 4.3 现有 API 适配

- [ ] 4.3.1 更新 `api.ts` 导出
- [ ] 4.3.2 保持向后兼容

---

## Phase 5: 主应用集成

### 5.1 重构 app.ts

- [ ] 5.1.1 重构主应用文件
- [ ] 5.1.2 集成步骤流程
- [ ] 5.1.3 连接 Store 和组件

**文件路径**: `web/workbench/src/app.ts`

```typescript
import { mountWorkbench } from './components/WorkbenchApp.svelte';

export function mountWorkbench(app: HTMLDivElement): void {
  // 导入并挂载 Svelte 应用
  const WorkbenchApp = (await import('./components/WorkbenchApp.svelte')).default;
  new WorkbenchApp({ target: app });
}
```

**文件路径**: `web/workbench/src/components/WorkbenchApp.svelte`

```svelte
<script lang="ts">
  import { currentStep, sceneOptions, selectedTemplate, selectedOption } from '../stores/workbench';
  import { evaluations } from '../stores/evaluation';
  import StepIndicator from './StepIndicator.svelte';
  import TemplateSelector from './TemplateSelector.svelte';
  import ComparisonGrid from './ComparisonGrid.svelte';
  import EvaluationPanel from './EvaluationPanel.svelte';
  import { batchGenerate, getBatchProgress, getJobStatus } from '../api/generate';
  import { batchEvaluate } from '../api/evaluate';

  const VIEWER_BASE = import.meta.env.VITE_ROADGEN_VIEWER_BASE || 'http://127.0.0.1:4173';

  async function handleTemplateSelect(template) {
    selectedTemplate.set(template);

    // 更新方案列表
    sceneOptions.set([
      { id: 'A', status: 'generating', progress: 0, templateId: template.id, selected: false },
      { id: 'B', status: 'generating', progress: 0, templateId: template.id, selected: false },
      { id: 'C', status: 'generating', progress: 0, templateId: template.id, selected: false },
    ]);

    // 开始生成
    await startBatchGenerate(template);

    // 切换到步骤 2
    currentStep.set(2);
  }

  async function startBatchGenerate(template) {
    try {
      const response = await batchGenerate({
        templateId: template.id,
        count: 3,
        seedOffset: 0,
      });

      // 轮询进度
      const pollInterval = setInterval(async () => {
        const progress = await getBatchProgress(response.batchId);

        sceneOptions.update(options => {
          return options.map(opt => {
            const job = progress.jobs.find(j => j.optionId === opt.id);
            if (job) {
              return {
                ...opt,
                status: job.status === 'completed' ? 'completed' : 'generating',
                progress: job.progress,
                ...(job.result ? {
                  previewImage: job.result.scene_glb_path,
                  sceneLayoutPath: job.result.scene_layout_path,
                  threeSceneUrl: job.result.viewer_url,
                } : {}),
              };
            }
            return opt;
          });
        });

        if (progress.status === 'completed') {
          clearInterval(pollInterval);
          await runBatchEvaluate(response.jobs.map(j => j.jobId));
        }
      }, 1200);
    } catch (error) {
      console.error('批量生成失败:', error);
      sceneOptions.update(options =>
        options.map(opt => ({ ...opt, status: 'failed' }))
      );
    }
  }

  async function runBatchEvaluate(sceneIds) {
    try {
      const result = await batchEvaluate({ sceneIds });

      evaluations.set(result.evaluations);

      // 更新方案评分
      sceneOptions.update(options => {
        return options.map((opt, i) => {
          const eval = result.evaluations[i];
          return {
            ...opt,
            scores: eval?.scores,
          };
        });
      });

      // 自动选择最佳方案
      const best = result.evaluations.reduce((best, current, idx, arr) =>
        current.scores.overall > (arr[best].scores.overall ?? 0) ? idx : best,
        0
      );

      sceneOptions.update(options => {
        return options.map((opt, i) => ({
          ...opt,
          selected: i === best,
        }));
      });

      // 切换到步骤 3
      currentStep.set(3);
    } catch (error) {
      console.error('批量评估失败:', error);
    }
  }

  function handleOptionSelect(option) {
    sceneOptions.update(options =>
      options.map(opt => ({
        ...opt,
        selected: opt.id === option.id,
      }))
    );
  }
</script>

<div class="workbench">
  <header class="header">
    <h1>RoadGen3D Workbench</h1>
    <StepIndicator current={$currentStep} />
    <a href={VIEWER_BASE} target="_blank" rel="noreferrer">打开独立 Viewer</a>
  </header>

  <main class="content">
    {#if $currentStep === 1}
      <TemplateSelector onSelect={handleTemplateSelect} />
    {:else if $currentStep === 2}
      <ComparisonGrid options={$sceneOptions} onSelect={handleOptionSelect} />
    {:else if $currentStep === 3}
      <EvaluationPanel
        evaluations={$evaluations}
        selectedId={$selectedOption?.id ?? null}
      />
    {/if}
  </main>
</div>
```

---

## Phase 6: 后端 API 实现

### 6.1 批量生成端点

- [ ] 6.1.1 创建 `/api/generate/batch` 端点
- [ ] 6.1.2 实现多方案并行生成

**文件路径**: `src/roadgen3d/api/workbench.py` (新增)

```python
@router.post("/api/generate/batch")
async def batch_generate(request: BatchGenerateRequest):
    """批量生成多个方案"""
    batch_id = str(uuid.uuid4())

    # 并行创建多个任务
    jobs = []
    for i in range(request.count):
        job = await create_scene_job(
            template_id=request.template_id,
            seed=request.seed_offset + i
        )
        jobs.append({
            "job_id": job["job_id"],
            "option_id": ["A", "B", "C"][i],
        })

    return BatchGenerateResponse(
        batch_id=batch_id,
        jobs=jobs,
    )
```

### 6.2 批量评估端点

- [ ] 6.2.1 创建 `/api/evaluate/batch` 端点
- [ ] 6.2.2 调用 eval_quality.py 计算评分

**文件路径**: `src/roadgen3d/api/workbench.py` (新增)

```python
@router.post("/api/evaluate/batch")
async def batch_evaluate(request: BatchEvaluateRequest):
    """批量评估多个场景"""
    from roadgen3d.eval_quality import (
        compute_walkability_indicators,
        compute_structured_safety_report,
        compute_structured_beauty_report,
    )

    evaluations = []
    for scene_id in request.scene_ids:
        layout = load_scene_layout(scene_id)

        walkability = compute_walkability_indicators(layout)
        safety = compute_structured_safety_report(layout, walkability)
        beauty = compute_structured_beauty_report(layout)

        # 综合评分
        overall = round(
            0.45 * walkability.walkability_index +
            0.35 * safety["final_score"] +
            0.20 * beauty["final_score"],
            2
        ) * 100

        evaluations.append({
            "scene_id": scene_id,
            "scores": {
                "walkability": round(walkability.walkability_index * 100, 1),
                "safety": round(safety["final_score"] * 100, 1),
                "beauty": round(beauty["final_score"] * 100, 1),
                "overall": overall,
            },
            "indicators": {
                "sid_clr": walkability.indicators["SID_CLR"],
                "clear_cont": walkability.indicators["CLEAR_CONT"],
                # ... 其他指标
            },
        })

    return BatchEvaluateResponse(evaluations=evaluations)
```

---

## 成功标准

- [ ] 3 步流程清晰可见，用户可顺畅完成整个工作流
- [ ] 6 个预设模板卡片直观展示，用户一眼可识别场景类型
- [ ] 3 个方案并行生成，进度实时可见
- [ ] 雷达图 + 柱状图清晰展示评分对比
- [ ] 指标表格支持展开查看详细数据
- [ ] 3D 预览内嵌在方案卡片中，无需跳转
- [ ] 高级模式入口保留专业用户调参能力
- [ ] 无破坏性变更，现有功能保持可用

---

## 文件清单

### 新增文件
```
web/workbench/src/components/StepIndicator.svelte
web/workbench/src/components/TemplateSelector.svelte
web/workbench/src/components/TemplateCard.svelte
web/workbench/src/components/ComparisonGrid.svelte
web/workbench/src/components/OptionCard.svelte
web/workbench/src/components/ThreePreview.svelte
web/workbench/src/components/EvaluationPanel.svelte
web/workbench/src/components/RadarChart.svelte
web/workbench/src/components/BarChart.svelte
web/workbench/src/components/IndicatorTable.svelte
web/workbench/src/components/WorkbenchApp.svelte
web/workbench/src/stores/workbench.ts
web/workbench/src/stores/evaluation.ts
web/workbench/src/api/generate.ts
web/workbench/src/api/evaluate.ts
web/workbench/src/types/workbench.ts
web/workbench/src/styles/workbench.css
src/roadgen3d/api/workbench.py (后端新增)
```

### 修改文件
```
web/workbench/src/app.ts
web/workbench/src/types.ts
web/workbench/src/api.ts
web/workbench/src/style.css
```
