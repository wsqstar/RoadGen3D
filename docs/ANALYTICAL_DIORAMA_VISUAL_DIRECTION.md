# Analytical Diorama Visual Direction

## Summary

RoadGen3D 当前的视觉结果处在真实感和游戏感之间：街道资产来自真实 GLB，但建筑、地面和整体美术没有完整真实城市的细节；同时它也没有游戏项目那种统一美术管线。更合适的目标不是继续夹在两端，而是明确转向 **Analytical Diorama**：用于生成、比较、评价街道方案的可解释数字沙盘。

这个方向把“不完全真实”转化为优势。场景不是照片级复刻，也不是游戏地图，而是类似城市设计事务所的数字展示模型：比例可信、材质统一、信息层清晰、适合叠加参数和评价解释。

## Visual Positioning

| 不采用 | 原因 |
|---|---|
| Photo-real city twin | 缺少真实建筑、真实材质、真实天气/街景采样，成本高且容易显假 |
| Game art scene | 需要完整风格化资产库、角色级美术规范和统一贴图生产 |
| Pure abstract diagram | 会丢失 3D 空间判断、街道尺度和资产布置感 |

采用：

> **Analytical Diorama / 城市设计沙盘**：半真实、半图解、可解释、可比较。

## Design Principles

1. **可信比例优先于真实细节**
   - 资产尺寸、人行道宽度、车道宽度、树距、长椅间距要可信。
   - 单体建筑不追求真实门牌和品牌，作为城市背景体块处理。

2. **统一材质优先于资产原始贴图**
   - 不同来源 GLB 的贴图饱和度、粗糙度、金属度差异很大。
   - Viewer 端应提供 presentation finish，把模型统一压到同一套沙盘视觉语言里。

3. **图解地面优先于照片纹理**
   - 道路、人行道、绿带、自行车道、公交带等使用清晰色块和轻微纹理。
   - 边界线、车道线、crosswalk 保持清晰，服务设计阅读。

4. **建筑作为背景，不抢街道主体**
   - 建筑采用浅灰、暖白、蓝灰、低饱和材质。
   - 通过程序化窗格、首层透明界面、体块高度节奏补充城市感。

5. **分析叠层是视觉系统的一部分**
   - RAG evidence、参数三元组、patch、约束和评分 delta 可以直接映射到场景高亮。
   - 这不是“调试 overlay”，而是 Analytical Diorama 的核心表达。

## Target Look

### Camera

- 默认使用轴测 / 正交感强的 framing。
- 第一人称保留为检查工具，不作为主展示风格。
- 3D Pareto 和场景都应避免夸张电影镜头。

### Lighting

- 高位柔光，低雾，强 AO，弱 bloom。
- 阴影清楚但不过分戏剧化。
- 默认背景使用干净浅灰绿或暖白，而不是强天空盒。

### Materials

| Role | Direction |
|---|---|
| Road | 中性深灰，低饱和，roughness 高 |
| Sidewalk | 浅灰蓝 / 浅水泥色，边界清晰 |
| Buildings | 浅灰、暖白、低饱和蓝灰；近景可程序化立面 |
| Trees / Planting | 保留绿色，但降低荧光感，提高 roughness |
| Street Furniture | 统一金属/木材粗糙度，减少贴图冲突 |
| Analysis Highlight | 使用少量高饱和颜色，仅用于被选中或 active 的参数/约束 |

## Implementation Plan

## Execution Status

Last tracked: 2026-05-05

- [x] **Analytical Diorama v1 shipped**: generation and Viewer now support `style_preset="analytical_diorama_v1"` without adding API endpoints.
- [x] **Viewer presentation finish v1 shipped**: Viewer has `analytical_diorama` lighting/material finish and a reversible `Diorama Finish` toggle for inspecting original GLB materials.
- [x] **Procedural building background v1 shipped**: analytical preset forces background buildings through procedural fallback while preserving footprint/lots/building placement metadata.
- [x] **Diagrammatic ground system v1 shipped**: analytical palette, roughness, texture pack metadata, and per-surface role counts are written for road/sidewalk/clear path/furnishing/bike/bus/grass/crossing/marking roles.
- [x] **Analysis overlay v1 shipped**: selected Benchmark / branch active features now map to reversible scene highlights for sidewalks, trees, safety/crossings, bike, transit, furnishings, roadway, and building edges.
- [ ] **Rejected edits ghost layer not started**: ghosted “not adopted” edits remain future work.

### Phase 1: Viewer Presentation Finish

Goal: 不改生成结果，只改变 Viewer 加载后的视觉一致性。

- [x] 新增 `analytical_diorama` lighting preset。
- [x] Viewer 对 RoadGen/visual_style layout 默认启用 diorama finish，并提供 `Diorama Finish` toggle 可关闭。
- [x] 加载 GLB 后遍历 mesh material：
  - 降低过高饱和度。
  - 统一提高 roughness。
  - 限制非金属物体的 metalness。
  - 对道路、人行道、建筑、植物做轻量 category-aware tint。
- [ ] 保持透明材质 depthWrite 修正，避免 alpha 物体排序问题。

Expected outcome: 立刻减少“素材拼贴感”和“廉价游戏感”，让场景更像设计沙盘。

### Phase 2: Procedural Building Background

Goal: 解决“没有真实建筑”的空洞感。

- [x] 使用 footprint + height 生成建筑体块。
- [x] 近景建筑生成程序化窗格和首层界面。
- [x] 远景建筑简化为低饱和 massing。
- [x] 将建筑标记为 background layer，避免抢街道主体。

### Phase 3: Diagrammatic Ground System

Goal: 让地面从“贴图地面”变成“3D 设计图”。

- [x] 对 road / sidewalk / bike lane / furnishing / planting 使用稳定 role palette。
- [x] 增加轻微 texture / tile scale，避免纯色玩具感。
- [x] crosswalk、lane marking、curb edge 保持清晰。

### Phase 4: Analysis Overlay

Goal: 把 Benchmark Explorer 的解释能力投射回 3D 场景。

- [x] 点击 Pareto 点时，在场景中高亮 active features。
- [x] `sidewalk_width_m` 高亮 sidewalk bands。
- [x] `tree_count` 高亮树列。
- [x] `safety` 相关高亮 lighting / bollard / crossing。
- [ ] `rejected edits` 使用虚线或 ghost layer 表示“未采纳修改”。

## Acceptance Criteria

- 场景第一眼不再像随机资产拼贴，而像统一数字沙盘。
- 建筑不需要真实，但要形成可信城市背景。
- 地面表达能看清道路、人行道、绿化和交通功能带。
- Viewer 的评价和参数解释可以自然叠加在场景中。
- Benchmark 截图评分仍可读，不因过度风格化损害评价。

## Current Execution

Current executable state:

- [x] Add Viewer `analytical_diorama` lighting preset.
- [x] Apply a lightweight analytical diorama material finish after GLB load.
- [x] Write non-destructive `visual_style` metadata to `scene_layout.json`.
- [x] Keep source GLB files unchanged.
- [x] Next executable step: implement Analysis Overlay v1 for active features and benchmark explanation highlights.
- [ ] Next executable step: implement rejected edits ghost layer for “not adopted” branch changes.
