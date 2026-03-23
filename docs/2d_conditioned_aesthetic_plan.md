# RoadGen3D 2D 条件输入与美学层改造方案

更新时间：2026-03-23

## 1. 问题重述

当前方案的主线是成立的：

```text
用户意图 / OSM / POI
  -> StreetProgram
  -> ConstraintSet
  -> LayoutSolver
  -> asset realization
  -> scene export
```

但如果继续把输入几乎都压在自然语言上，这条链路会有两个明显短板：

- 几何布局意图表达不够直接
- 美学意图没有成为上游显式控制量

这也是“纯语言方案、没有美学设计感”的根本原因。

## 2. 核心结论

### 2.1 不能把 `2D 平面图 = UrbanVerse 输入视频的一帧`

`UrbanVerse` 一类系统依赖的是：

- 多视角变化
- 跨帧一致性
- 相机轨迹
- 运动深度线索
- 遮挡变化下的几何补全

单张 2D 图，尤其是 Gemini 生成的平面图或概念图，并不具备这些条件。

因此我们不应该把问题定义成：

```text
2D image -> reconstruct real 3D street scene
```

更合理的定义应是：

```text
2D plan / concept image
  -> structured layout hints + aesthetic intent
  -> controllable 3D street scene synthesis
```

### 2.2 2D 输入的价值在于“条件”，不是“伪重建”

对 RoadGen3D 来说，2D 图最有价值的不是替代 `StreetProgram -> ConstraintSet -> LayoutSolver`，
而是给这条主链路提供两类新条件：

- 几何与语义条件
- 美学与构图条件

### 2.3 美学层必须成为显式系统层，不应只作为渲染后处理

仓库里已经存在这部分基础能力：

- [src/roadgen3d/beauty.py](/Users/shiqi/Coding/github/GIStudio/RoadGen3D/src/roadgen3d/beauty.py)
- [src/roadgen3d/types.py](/Users/shiqi/Coding/github/GIStudio/RoadGen3D/src/roadgen3d/types.py)

当前已有：

- `style_preset`
- `beauty_mode`
- presentation shaping
- style coherence / composition 评分

所以正确方向不是“重新发明一个美学系统”，而是把现有 `beauty` 层从下游默认值提升为上游显式输入。

## 3. 我们真正要做的系统定义

建议把 RoadGen3D 的这条新路线定义成：

```text
Text + 2D plan / concept image
  -> LayoutHints + AestheticIntent
  -> StreetProgram / ConstraintSet / LayoutSolver
  -> asset / material / sky realization
  -> beauty shaping + presentation rendering
```

这条定义与当前仓库主干天然一致，因为它保留了：

- `OSM + POI`
- `StreetProgram`
- `ConstraintSet`
- `LayoutSolver`
- asset backend
- beauty / presentation

## 4. 推荐的双通道输入分解

### 4.1 几何语义通道

负责回答“街道怎么组织”：

- road carriageway 在哪里
- sidewalk 在哪里
- crossing / bus stop / tree zone 在哪里
- 哪些位置适合摆放 bench / lamp / trash
- 是否存在中轴、节奏、对称或偏置关系

这一路输出建议抽象成：

```text
LayoutHints
  - corridor polygons
  - semantic masks
  - slot priors
  - alignment priors
  - cross-section hints
```

### 4.2 美学风格通道

负责回答“它应该看起来像什么”：

- 偏 civic clean 还是 lush walkable
- 是 formal / transit / green / warm / minimal 哪一类气质
- 树冠密度高不高
- 材料更偏 stone / concrete / metal / wood
- 家具节奏是稀疏、均匀还是强调节点
- 氛围更偏通勤、休闲还是展示型

这一路输出建议抽象成：

```text
AestheticIntent
  - style_preset
  - palette tags
  - material tags
  - rhythm / density hints
  - hero element preference
  - environment mood
```

## 5. 三类 2D 输入要区别对待

### 5.1 顶视平面草图

最适合提供：

- 道路与步道边界
- 功能带关系
- POI 和街具槽位先验
- 绿化和节点分布

不适合直接决定：

- 建筑立面
- 真实高度
- 真实材质
- 摄影级透视效果

### 5.2 透视概念图

最适合提供：

- 风格氛围
- 材料倾向
- 开放感与围合感
- 树木/灯具/街具的视觉节奏

不适合直接决定：

- 平面几何精度
- 尺度正确的道路横断面
- 可编辑的槽位坐标

### 5.3 混合输入

最佳做法通常不是只给一张图，而是：

- 一张顶视 plan 负责布局
- 一张 concept board 负责风格

这比单图承担全部职责稳定得多。

## 6. 与现有 RoadGen3D 主链的挂接方式

建议新增的不是“2D 直接吐 mesh”，而是一个轻量的条件前端：

```text
Gemini 2D 图 / moodboard
  -> image parser
  -> LayoutHints + AestheticIntent
  -> compose-config patch + StreetProgram patch
  -> ConstraintSet refinement
  -> LayoutSolver
  -> retrieval + realization
  -> beauty shaping
```

其中最关键的映射关系是：

- `LayoutHints -> StreetProgram / slot priors / cross-section hints`
- `AestheticIntent -> style_preset / material tags / sky tags / hero categories`

## 7. 为什么这条路比单图 3D 重建更适合我们

### 7.1 可解释

我们可以明确区分：

- 布局约束来自哪里
- 风格偏好来自哪里
- 哪些是 solver 决策
- 哪些是 asset retrieval 决策

### 7.2 可编辑

如果生成结果不满意，用户可以改：

- 2D plan
- style preset
- 某类 POI / slot prior
- solver 配置

而不是只能重新采样一个黑箱 mesh。

### 7.3 能复用现有基础设施

当前仓库中已经有可直接复用的层：

- [src/roadgen3d/street_program.py](/Users/shiqi/Coding/github/GIStudio/RoadGen3D/src/roadgen3d/street_program.py)
- [src/roadgen3d/design_rules.py](/Users/shiqi/Coding/github/GIStudio/RoadGen3D/src/roadgen3d/design_rules.py)
- [src/roadgen3d/layout_solver.py](/Users/shiqi/Coding/github/GIStudio/RoadGen3D/src/roadgen3d/layout_solver.py)
- [src/roadgen3d/beauty.py](/Users/shiqi/Coding/github/GIStudio/RoadGen3D/src/roadgen3d/beauty.py)
- [src/roadgen3d/services/design_assistant.py](/Users/shiqi/Coding/github/GIStudio/RoadGen3D/src/roadgen3d/services/design_assistant.py)

## 8. 推荐的最小可行方案

### 8.1 MVP-A：2D plan 只驱动布局

流程：

```text
Gemini 顶视平面草图
  -> 解析 road / sidewalk / crossing / bus_stop / tree_zone / furniture points
  -> 生成 slot priors + cross-section hints
  -> 接入现有 StreetProgram / LayoutSolver
  -> 输出可编辑 3D 场景
```

目标：

- 先解决“2D 能不能稳定影响布局”
- 不碰单图 3D 重建

### 8.2 MVP-B：显式引入美学意图

流程：

```text
Text + 2D concept image
  -> 抽取 style tags / palette / material mood / density rhythm
  -> 映射到 style_preset + beauty_mode + retrieval tags
  -> 进入 beauty shaping 和 asset/material/sky 选择
```

目标：

- 让美学不再只是 query 里一句模糊描述
- 让 `beauty.py` 真正成为上游受控模块

### 8.3 MVP-C：可选 coarse 3D blockout

只作为 preview：

```text
2D plan
  -> coarse height / facade mass hints
  -> blockout preview
  -> 最终仍由资产和规则链路落地
```

注意这不应成为研究主链，只适合预览和演示。

## 9. 不建议作为近期主线的方向

近期不建议把以下方向当主路径：

- 把单张 2D 图硬当 UrbanVerse 视频的一帧
- 直接做单图 `image-to-mesh`
- 让单图直接决定完整建筑立面与真实高度
- 用单图深度估计替代 `StreetProgram -> ConstraintSet -> LayoutSolver`

这些方向的问题是：

- 几何不稳
- 尺度不稳
- 可编辑性差
- 与当前系统主干不一致

## 10. 评估指标建议

新路线不能只看“像不像”，至少要看四类指标：

### 10.1 布局保真

- 2D 约束保留率
- 关键 semantic region 对齐率
- slot hit rate

### 10.2 规则可行性

- `rule_satisfaction_rate`
- `topology_validity`
- `cross_section_feasibility`

### 10.3 美学一致性

- `style_coherence`
- material consistency
- hero element consistency
- rhythm / spacing consistency

### 10.4 可编辑性

- 修改 style preset 后是否能稳定重组
- 修改 slot prior 后是否能局部重算
- 是否仍能输出清晰的 solver summary

## 11. 建议的近期代码落点

如果进入实现阶段，建议优先在以下位置扩展：

- `src/roadgen3d/services/design_types.py`
  - 让设计草案正式承载最小美学控制字段
- `src/roadgen3d/services/design_runtime.py`
  - 让确认后的 draft 能落到 `StreetComposeConfig`
- `src/roadgen3d/beauty.py`
  - 继续作为 style shaping 与 presentation 入口
- 新增一个 image-condition 解析层
  - 负责把 2D 图拆成 `LayoutHints + AestheticIntent`

## 12. 最终结论

RoadGen3D 的正确升级方向不是：

```text
2D 图 -> 假装 video-to-scene
```

而是：

```text
2D 图 -> 结构化布局条件 + 美学条件
      -> 现有可解释 3D 生成主链
```

这样做的好处是：

- 不违背当前系统的 neuralsymbolic 主干
- 可以真正把“美学设计”引入主流程
- 仍然保持可解释、可编辑、可评估
- 比单图直接 3D 重建更适合近期落地
