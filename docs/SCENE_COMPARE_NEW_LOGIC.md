# 3D Viewer 场景对比 - 新版交互逻辑

## 设计理念

**双场景选择器始终可见，自动分屏，Compare仅打开面板**

## UI 布局

```
┌────────────────────────────────────────────────────────────────┐
│ [☰] Viewer | 3D Road Viewer |                                  │
│ [Scene A ▼] [Scene B ▼] [✕] | [⚖️ Compare] | [Settings]      │
└────────────────────────────────────────────────────────────────┘
```

## 交互逻辑

### 1. 默认状态

- **Scene A**: 显示第一个场景（默认选中）
- **Scene B**: 显示"— Clear —"（空选项）
- **✕ 按钮**: 隐藏（因为Scene B已经是空的）
- **⚖️ Compare**: 可用

### 2. 选择 Scene A

- 从下拉列表中选择一个场景
- 3D视图显示该场景
- 单屏模式

### 3. 选择 Scene B

- 从下拉列表中选择一个**不同于Scene A**的场景
- **自动触发分屏模式**
- **✕ 按钮出现**（用于清除Scene B）
- 3D视图分为左右两个视口（TODO: 实现双视口渲染）

### 4. Scene A 和 Scene B 选择相同场景

- **自动切换为单屏模式**
- **✕ 按钮隐藏**
- 3D视图显示该场景（单屏）

### 5. 点击 ✕ 按钮

- **清除Scene B选择**（重置为"— Clear —"）
- **取消分屏**，回到单屏模式
- **✕ 按钮隐藏**

### 6. 点击 ⚖️ Compare 按钮

- **打开雷达图对比面板**（屏幕中央弹出）
- 如果Scene A和Scene B都选择了不同的场景，显示两个雷达图对比
- 如果只选择了Scene A，只显示一个雷达图
- 如果Scene A和Scene B相同，只显示一个雷达图

### 7. 关闭雷达图面板

- 点击面板右上角的 **✕** 按钮

## 分屏逻辑详解

### 单屏模式
```
条件：Scene B为空 或 Scene A == Scene B
状态：sceneCompareState.mode = "single"
渲染：单一视口，显示Scene A
```

### 分屏模式
```
条件：Scene A != Scene B
状态：sceneCompareState.mode = "dual"
渲染：左右两个视口（TODO: 实现）
  - 左侧：Scene A
  - 右侧：Scene B
```

## 雷达图面板逻辑

### 显示内容

| Scene A | Scene B | 雷达图显示 |
|---------|---------|-----------|
| 已选择 | 未选择 | 仅Scene A（蓝色） |
| 已选择 | 相同场景 | 仅Scene A（蓝色） |
| 已选择 | 不同场景 | Scene A（蓝色）+ Scene B（红色）对比 |

### 指标数据来源

- 从场景的 `manifest.json` 中的 `summary` 字段获取
- 当前使用示例数据演示功能

## 使用示例

### 示例1: 查看单个场景

1. Scene A 选择 "Final Scene"
2. Scene B 保持 "— Clear —"
3. 视图显示 "Final Scene"（单屏）
4. 点击 ⚖️ Compare 查看该场景的雷达图

### 示例2: 对比两个场景

1. Scene A 选择 "Final Scene"
2. Scene B 选择 "Alternative Scene"
3. **自动分屏**：左侧Final Scene，右侧Alternative Scene
4. **✕ 按钮出现**
5. 点击 ⚖️ Compare 查看两个场景的雷达图对比

### 示例3: 取消对比

1. 在分屏模式下
2. 点击 **✕** 按钮
3. Scene B 重置为 "— Clear —"
4. **自动回到单屏模式**

## 技术实现

### 状态管理

```typescript
type SceneCompareState = {
  mode: "single" | "dual";       // 单屏/分屏模式
  sceneA: string | null;          // Scene A的key
  sceneB: string | null;          // Scene B的key（null表示未选择）
  metricsA: SceneMetrics | null;  // Scene A的指标数据
  metricsB: SceneMetrics | null;  // Scene B的指标数据
};
```

### 核心函数

```typescript
// 填充场景选择器
populateSceneSelectors()

// 更新分屏状态（自动判断）
updateSplitView()

// 选择Scene A/B时调用
sceneASelectEl.addEventListener("change", updateSplitView)
sceneBSelectEl.addEventListener("change", updateSplitView)

// 清除Scene B
resetSceneModeBtn.addEventListener("click", () => {
  sceneBSelectEl.value = ""
  updateSplitView()
})

// 打开雷达图面板
openMetricsBtn.addEventListener("click", showRadarPanel)
```

### 自动分屏判断

```typescript
function updateSplitView() {
  const sceneA = sceneASelectEl.value
  const sceneB = sceneBSelectEl.value
  
  if (!sceneB || sceneA === sceneB) {
    // 单屏模式
    sceneCompareState.mode = "single"
    resetSceneModeBtn.hidden = true
  } else {
    // 分屏模式
    sceneCompareState.mode = "dual"
    resetSceneModeBtn.hidden = false
  }
}
```

## TODO

- [ ] 实现真正的双视口渲染（左右分屏）
- [ ] 从manifest加载真实的metrics数据
- [ ] Scene B的3D场景加载和渲染
- [ ] 分屏时的相机同步（可选）
- [ ] 拖拽调整分屏比例（可选）

## 文件位置

- **前端UI**: `web/viewer/src/app.ts`
- **雷达图**: `web/viewer/src/scene-compare-radar.ts`
- **样式**: `web/viewer/src/style-scene-compare.css`
