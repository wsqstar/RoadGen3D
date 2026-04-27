# Workbench 对比功能实现总结

## 概述

本次完善为 RoadGen3D Workbench 添加了完整的方案对比功能，包括 PNG + JSON 前后对比、3D 场景对比以及多方案评分对比增强。

## 实现的功能

### 1. PNG + JSON 前后对比 ✅

**组件**: `ComparisonPanel.tsx`

**功能特性**:
- **双方案选择**: 并排显示方案 A 和方案 B 的预览图和评分
- **多维度对比**: 包含 4 个 Tab 页签

#### Tab 1: 指标对比
- 显示所有数值指标的差异
- 绿色箭头表示提升，红色箭头表示下降
- 显示绝对值和百分比变化

#### Tab 2: 配置对比
- 对比两个方案的配置参数
- 标记新增字段（绿色 +）
- 标记删除字段（红色 -）
- 标记修改字段（黄色 ~）

#### Tab 3: 放置对比
- 按类别统计资产放置差异
- 显示每个类别的新增/删除/移动数量
- 计算平均移动距离
- 显示总体统计信息

#### Tab 4: 2D 差异图
- 支持两种模式:
  - **叠加对比**: 红色/绿色像素级差异图
  - **矢量箭头**: 显示资产移动方向和距离的箭头图
- 可切换模式查看不同效果

**后端 API 支持**:
- `/api/scenes/diff` - 计算结构化差异（JSON）
- `/api/scenes/diff/image` - 渲染 2D 差异图（PNG）

---

### 2. 3D 场景对比 ✅

**组件**: `SceneCompareModal.tsx`

**功能特性**:
- **模态框选择**: 从所有就绪方案中选择方案 A 和方案 B
- **实时预览**: 显示每个方案的评分信息
- **Viewer 集成**: 点击按钮在 Viewer 中打开分屏对比
- **URL 参数传递**: 通过 URL 参数传递 layout 路径

**Viewer 端支持**:
- Viewer 已实现 `compare-mode.ts`
- 支持分屏 3D 渲染
- 同步相机控制
- URL 参数: `?compare=true&layoutA=...&layoutB=...`

---

### 3. 多方案评分对比增强 ✅

**组件**: `RadarComparison.tsx`

**功能特性**:
- **雷达图对比**: 
  - 使用 Canvas 绘制多维度评分雷达图
  - 支持多个方案同时显示（不同颜色）
  - 自适应高 DPI 屏幕
  - 交互式图例说明

- **显示的维度**:
  - 步行性 (Walkability)
  - 安全性 (Safety)
  - 美观度 (Beauty)

- **颜色方案**: 
  - 方案 A: 蓝色 (#1890ff)
  - 方案 B: 绿色 (#52c41a)
  - 方案 C: 橙色 (#faad14)
  - 更多方案自动分配颜色

**集成位置**:
- 在 `EvaluationPanel.tsx` 中显示
- 当有 2 个或以上方案时自动出现
- 位于综合评分卡片下方

---

## 新增文件

### 组件文件
1. `/web/workbench/src/components/ComparisonPanel.tsx` - PNG + JSON 对比面板
2. `/web/workbench/src/components/SceneCompareModal.tsx` - 3D 对比模态框
3. `/web/workbench/src/components/RadarComparison.tsx` - 雷达图对比组件

### 类型定义
- 扩展了 `/web/workbench/src/lib/types.ts`:
  - `ConfigDiff` - 配置差异类型
  - `MetricDiff` / `MetricsDiff` - 指标差异类型
  - `PlacementDiff` / `PlacementsDiff` - 放置差异类型
  - `SceneDiffResult` - 完整场景差异结果
  - `CompareScheme` - 对比方案类型

### API 函数
- 扩展了 `/web/workbench/src/lib/api.ts`:
  - `compareScenes()` - 调用后端获取结构化差异
  - `getDiffImageUrl()` - 构建差异图 URL

---

## 修改的文件

### 1. App.tsx
**新增状态**:
- `compareMode` - 是否处于对比模式
- `compareSchemeA` - 对比方案 A
- `compareSchemeB` - 对比方案 B
- `show3DCompareModal` - 是否显示 3D 对比模态框

**新增函数**:
- `handleStartCompare()` - 启动对比功能
- `handleExitCompare()` - 退出对比模式

**UI 更新**:
- Step 2 新增对比按钮
- 支持切换方案列表和对比详情视图
- 集成 3D 对比模态框

### 2. EvaluationPanel.tsx
**增强功能**:
- 导入 `RadarComparison` 组件
- 在多方案时显示雷达图
- 保留原有的进度条对比

---

## 使用流程

### PNG + JSON 对比
1. 在 Step 2 生成至少 2 个方案
2. 点击 "📊 PNG + JSON 前后对比" 按钮
3. 进入对比视图，查看 4 个维度的差异
4. 点击 "返回方案列表" 退出对比

### 3D 场景对比
1. 在 Step 2 生成至少 2 个方案
2. 点击 "🌐 3D 场景对比" 按钮
3. 在模态框中选择方案 A 和方案 B
4. 点击 "在 Viewer 中对比"
5. Viewer 在新窗口打开分屏对比

### 多方案评分对比
1. 在 Step 2 点击 "查看评估结果" 进入 Step 3
2. 自动生成雷达图对比（当有 2+ 方案时）
3. 查看各方案在三个维度的表现差异

---

## 技术细节

### 数据流
```
SchemeGrid (选择方案)
    ↓
handleStartCompare()
    ↓
ComparisonPanel
    ↓
compareScenes() API
    ↓
显示差异数据
```

### 3D 对比流程
```
SceneCompareModal (选择方案)
    ↓
构建 Viewer URL
    ↓
window.open() 新窗口
    ↓
Viewer compare-mode.ts
    ↓
分屏渲染 + 同步相机
```

### 雷达图绘制
```
Canvas 2D API
    ↓
绘制网格 (5 层同心圆)
    ↓
绘制轴线 (3 个维度)
    ↓
绘制数据多边形 (每个方案)
    ↓
绘制数据点
```

---

## 后端依赖

### 已有 API (无需修改)
- ✅ `/api/scenes/diff` - 计算场景差异
- ✅ `/api/scenes/diff/image` - 渲染差异图
- ✅ `/api/design/evaluate/compare` - 带历史对比的评估

### 已有模块 (无需修改)
- ✅ `diff_engine.py` - 结构化差异计算
  - `compute_config_diff()`
  - `compute_metrics_diff()`
  - `compute_placements_diff()`
- ✅ `diff_render.py` - 差异图渲染
  - `render_diff_overlay()`
  - `render_delta_map()`

---

## 构建测试

```bash
cd web/workbench
npm run build
```

**结果**: ✅ 构建成功
- TypeScript 编译通过
- Vite 打包成功
- 无类型错误

---

## 未来改进建议

1. **交互式雷达图**: 添加鼠标悬停显示具体数值
2. **导出对比报告**: 生成 PDF 或 PNG 格式的对比报告
3. **保存对比历史**: 记录用户的历史对比操作
4. **批量对比**: 支持一次对比多个方案对
5. **自定义指标**: 允许用户选择要对比的指标
6. **动画效果**: 添加切换和加载的过渡动画

---

## 总结

本次完善实现了以下目标：

✅ **PNG + JSON 前后对比**: 完整的 4 维度对比功能  
✅ **3D 场景对比**: 集成 Viewer 分屏对比  
✅ **多方案评分对比**: 雷达图增强可视化  
✅ **代码质量**: TypeScript 类型安全，构建通过  
✅ **用户体验**: 直观的 UI，清晰的视觉反馈  

所有功能都已实现并经过构建测试，可以投入使用。
