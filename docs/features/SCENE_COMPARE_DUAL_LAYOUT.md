# 3D Viewer 双Layout场景对比 - 正确实现

## 层级关系理解

```
Scene Layout (scene_layout.json) - 一个布局文件
  ├─ production_steps[] - 生产步骤场景
  │   ├─ Road Base
  │   ├─ Furniture Anchor  
  │   └─ ...
  └─ final_scene - 最终场景
```

## 对比目标

**对比不同的scene_layout.json中的场景：**
- Layout A (例如：20260415T082702Z/scene_layout.json) 的 Road Base
- Layout B (例如：20260416T103045Z/scene_layout.json) 的 Final Scene

## UI 布局

```
┌──────────────────────────────────────────────────────────────────────┐
│ [☰] Viewer | 3D Road Viewer |                                        │
│ [Layout A ▼] [Scene A ▼] | [Layout B ▼] [Scene B ▼] [✕] | [⚖️ Compare] │
└──────────────────────────────────────────────────────────────────────┘
```

### 控件说明

1. **Layout A**: 选择第一个layout文件
2. **Scene A**: 选择Layout A中的场景（Road Base, Final Scene等）
3. **Layout B**: 选择第二个layout文件（默认"— Clear —"）
4. **Scene B**: 选择Layout B中的场景
5. **✕**: 清除Layout B和Scene B，回到单屏
6. **⚖️ Compare**: 打开雷达图对比面板

## 交互逻辑

### 1. 默认状态（单屏）

```
[Layout A ▼] [Scene A ▼] | [— Clear —] [✕隐藏] | [⚖️ Compare]
     ↓            ↓
 已选择      已选择
```

- 显示Layout A的Scene A
- 单屏模式

### 2. 选择Layout B和Scene B（分屏）

```
[Layout A ▼] [Scene A ▼] | [Layout B ▼] [Scene B ▼] [✕显示] | [⚖️ Compare]
     ↓            ↓              ↓            ↓
  layout1     Road Base      layout2     Final Scene
```

- **自动分屏**（如果Layout A ≠ Layout B 或 Scene A ≠ Scene B）
- 左侧显示Scene A
- 右侧显示Scene B（TODO: 实现双视口）

### 3. 相同场景（单屏）

```
[Layout A ▼] [Scene A ▼] | [Layout B ▼] [Scene B ▼] [✕隐藏] | [⚖️ Compare]
     ↓            ↓              ↓            ↓
  layout1     Road Base      layout1     Road Base
```

- Layout A = Layout B 且 Scene A = Scene B
- **自动单屏模式**

### 4. 点击 ✕ 按钮

- 清除Layout B和Scene B
- Layout B重置为"— Clear —"
- Scene B清空
- **回到单屏模式**

### 5. 点击 ⚖️ Compare

- 打开雷达图对比面板
- 如果两个场景都选择了，显示对比图
- 如果只选择了一个，显示单个雷达图

## 数据流

### 加载Layout时

```typescript
1. 用户选择 Layout A
   ↓
2. 加载 manifest (loadManifest)
   ↓
3. 提取场景列表 (production_steps + final_scene)
   ↓
4. 填充 Scene A 选择器
   ↓
5. 自动加载第一个场景
```

### 分屏判断

```typescript
const isSameScene = 
  layoutA === layoutB && 
  sceneA === sceneB && 
  layoutA && sceneA;

if (!layoutB || !sceneB || isSameScene) {
  // 单屏模式
} else {
  // 分屏模式
}
```

## 场景标签示例

**Scene A:**
```
20260415T082702Z / Road Base
```

**Scene B:**
```
20260416T103045Z / Final Scene
```

## 使用示例

### 示例1: 对比不同layout的Road Base

1. Layout A: 选择 `20260415T082702Z`
2. Scene A: 选择 `Road Base`
3. Layout B: 选择 `20260416T103045Z`
4. Scene B: 选择 `Road Base`
5. **自动分屏**对比两个不同时间的Road Base

### 示例2: 对比同一layout的不同阶段

1. Layout A: 选择 `20260415T082702Z`
2. Scene A: 选择 `Road Base`
3. Layout B: 选择 `20260415T082702Z`（同一layout）
4. Scene B: 选择 `Final Scene`
5. **自动分屏**对比同一layout的不同阶段

### 示例3: 查看单个场景

1. Layout A: 选择 `20260415T082702Z`
2. Scene A: 选择 `Final Scene`
3. Layout B: 保持 `— Clear —`
4. **单屏显示** Final Scene

## 技术实现

### 数据结构

```typescript
// 存储每个layout的manifest
const layoutManifests = new Map<string, ViewerManifest>();

// 场景对比状态
const sceneCompareState: SceneCompareState = {
  mode: "single" | "dual",
  sceneA: string | null,  // 格式: "layoutPath::sceneKey"
  sceneB: string | null,
  metricsA: SceneMetrics | null,
  metricsB: SceneMetrics | null,
};
```

### 核心函数

```typescript
// 填充Layout选择器
populateLayoutSelectors()

// 加载manifest并填充Scene选择器
async loadLayoutAndPopulateScenes(layoutPath, sceneSelectEl, isLayoutA)

// 更新分屏状态
updateSplitView()

// 从manifest创建场景选项
makeSceneOptionsFromManifest(manifest, layoutPath)
```

### 场景选项生成

```typescript
function makeSceneOptionsFromManifest(manifest, layoutPath) {
  const options = [];
  
  // 添加production_steps
  for (const step of manifest.production_steps) {
    options.push({
      key: step.step_id,
      label: `${step.title} (${layout文件名})`,
      glbUrl: step.glb_url
    });
  }
  
  // 添加final_scene
  if (manifest.final_scene) {
    options.push({
      key: "final_scene",
      label: `Final Scene (${layout文件名})`,
      glbUrl: manifest.final_scene.glb_url
    });
  }
  
  return options;
}
```

## TODO

- [ ] 实现真正的双视口渲染（左右分屏显示两个场景）
- [ ] 从manifest加载真实的metrics数据
- [ ] Scene B的3D场景加载和渲染
- [ ] 分屏时的相机同步
- [ ] 拖拽调整分屏比例

## 文件位置

- **前端UI**: `web/viewer/src/app.ts`
- **雷达图**: `web/viewer/src/scene-compare-radar.ts`
- **样式**: `web/viewer/src/style-scene-compare.css`
- **类型定义**: `web/viewer/src/viewer-types.ts`
