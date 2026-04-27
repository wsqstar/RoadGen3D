# 3D Viewer 场景对比功能

## 功能概述

3D Road Viewer 现在支持**双场景对比模式**，可以同时查看和比较两个不同场景的指标数据。

## 使用方式

### 1. 默认模式 - 单场景

- 页面加载后，默认显示单个场景
- 使用场景选择器选择一个场景查看

### 2. 启用双场景对比

**步骤：**

1. 点击标题栏右侧的 **⚖️ Compare** 按钮
2. 标题栏会显示两个场景选择器（Scene A 和 Scene B）
3. 页面中央会弹出雷达图对比面板

### 3. 选择场景进行对比

- 在 **Scene A** 下拉框中选择第一个场景
- 在 **Scene B** 下拉框中选择第二个场景
- 雷达图会自动更新显示两个场景的指标对比

### 4. 返回单场景模式

- 点击场景选择器旁边的 **✕** 按钮
- 或者刷新页面

## 雷达图指标

对比面板显示以下10个核心指标：

1. **Spacing Uniformity** - 间距均匀度
2. **Style Consistency** - 风格一致性
3. **Balance Score** - 平衡分数
4. **Curvature Smoothness** - 曲率平滑度
5. **Width Compliance** - 宽度合规度
6. **Pedestrian Accessibility** - 步行可达性
7. **Safety Score** - 安全分数
8. **Aesthetics Score** - 美观度
9. **Connectivity** - 连通性
10. **Overall Quality** - 总体质量

## UI 布局

### 单场景模式
```
[☰] Viewer | 3D Road Viewer | [场景选择器] | [Settings]
```

### 双场景模式
```
[☰] Viewer | 3D Road Viewer | [Scene A▼] [Scene B▼] [✕] | [Settings]
```

### 雷达图面板
```
┌─────────────────────────────────────┐
│  Metrics Comparison            [✕]  │
├─────────────────┬───────────────────┤
│   Scene A       │                   │
│   (雷达图)      │    (雷达图)       │
│                 │                   │
│   Scene B       │                   │
└─────────────────┴───────────────────┘
```

## 技术实现

### 文件结构

- **前端UI**: `web/viewer/src/app.ts`
- **雷达图绘制**: `web/viewer/src/scene-compare-radar.ts`
- **样式文件**: `web/viewer/src/style-scene-compare.css`
- **路由**: `web/viewer/src/main.ts`

### 状态管理

```typescript
type SceneCompareState = {
  mode: "single" | "dual";          // 单场景/双场景模式
  sceneA: string | null;             // 场景A的ID
  sceneB: string | null;             // 场景B的ID
  metricsA: SceneMetrics | null;     // 场景A的指标数据
  metricsB: SceneMetrics | null;     // 场景B的指标数据
};
```

### 核心API

```typescript
// 创建雷达图
createRadarChart(
  canvas: HTMLCanvasElement,
  metrics: SceneMetrics,
  label: string,
  color: string
)

// 调整画布大小
resizeRadarCanvas(canvas: HTMLCanvasElement)
```

## 当前状态

✅ **已完成：**
- 双场景模式切换UI
- 场景选择器（Scene A / Scene B）
- 雷达图对比面板
- 10项核心指标可视化
- Reset按钮返回单场景

⏳ **TODO：**
- 从后端动态加载场景manifest获取真实metrics数据
- 与现有场景选择器集成
- 支持更多指标维度
- 导出对比结果

## 示例数据

当前使用示例数据演示功能，每个场景的指标为：

**Scene A:**
```json
{
  "spacing_uniformity": 0.85,
  "style_consistency": 0.92,
  "balance_score": 0.78,
  ...
}
```

**Scene B:**
```json
{
  "spacing_uniformity": 0.78,
  "style_consistency": 0.85,
  "balance_score": 0.82,
  ...
}
```

## 快捷操作

- **⚖️ Compare** - 启用双场景对比
- **✕** (场景选择器旁) - 重置为单场景模式
- **✕** (雷达图右上角) - 关闭雷达图面板

## 颜色方案

- **Scene A**: 蓝色 (#3b82f6)
- **Scene B**: 红色 (#ef4444)

## 响应式设计

雷达图面板会根据窗口大小自动调整：
- 位置：屏幕中央
- 大小：最小600px，最大900px
- 画布：自适应父容器尺寸
