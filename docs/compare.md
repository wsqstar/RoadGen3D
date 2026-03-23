# RoadGen3D 与 UrbanVerse 对比

更新时间：2026-03-23

本文用于向导师汇报：RoadGen3D 当前系统与论文
`UrbanVerse: Scaling Urban Simulation by Watching City-Tour Videos`
之间的差异、当前系统的独特价值，以及可借鉴但不应照搬的部分。

参考论文：

- `docs/references/Liu 等 - 2026 - UrbanVerse Scaling Urban Simulation by Watching City-Tour Videos.pdf`

## 1. 一句话结论

RoadGen3D 和 UrbanVerse 都面向城市街景场景生成，但它们解决的问题并不相同。

- UrbanVerse 更像一个 `真实世界视频 -> 仿真场景` 的 real-to-sim 平台。
- RoadGen3D 更像一个 `文本/AOI/OSM -> 可控街道设计方案 -> 3D 场景` 的设计驱动型生成系统。

因此，两者不是简单的“谁更完整”关系，而是各自站在不同系统层级上：

- UrbanVerse 强在真实世界还原、仿真资产规模、物理属性和 scene realism。
- RoadGen3D 强在设计意图表达、规则约束、OSM/POI 结合、显式中间表示和可编辑性。

配套改造计划见：

- `docs/urbanverse_adoption_plan.md`

## 2. UrbanVerse 在做什么

根据论文主文第 3-6 页与附录第 17 页，UrbanVerse 的核心由两部分组成：

### 2.1 UrbanVerse-100K

一个面向 urban simulation 的大规模资产数据库，包含：

- `102,444` 个 object assets
- `659` 个 object categories
- `288` 个 ground materials
- `306` 个 sky maps

其数据库不只是 3D 模型集合，还强调：

- true metric scale
- semantic / physical / affordance annotations
- road / sidewalk ground appearance
- HDRI sky illumination

### 2.2 UrbanVerse-Gen

一个从城市漫游视频自动生成仿真场景的 real-to-sim pipeline，主干大致是：

```text
City-tour video
  -> object / ground / sky parsing
  -> 3D lifting and scene distillation
  -> urban scene graph
  -> asset / material / sky retrieval
  -> scene materialization in IsaacSim / UrbanSim
```

用户侧既可以：

- 输入 raw video 自动生成场景
- 直接使用他们内置的场景库与资产库

## 3. RoadGen3D 当前系统在做什么

RoadGen3D 当前主干不是视频重建，而是街道设计与生成工作台。

其真实主链路是：

```text
AOI / query
  -> OSM fetch + parse
  -> road discovery / auto road selection
  -> POI extraction
  -> POI-aware cross-section synthesis
  -> StreetProgram
  -> ConstraintSet
  -> LayoutSolver
  -> asset retrieval + realization
  -> scene export + analysis
```

当前系统的关键特征是：

- 以 `OSM + POI + StreetProgram + ConstraintSet + LayoutSolver` 为核心
- 以显式、可编辑的中间对象组织设计意图
- 面向街道设计和研究工作台，而不是只做资产重建
- 资产库、检索、导出、分析和训练能力并存

## 4. 核心相同点

虽然项目目标不同，但两者仍有一些重要共性：

### 4.1 都是 urban scene generation 系统

两者都不是单体资产 demo，而是要生成完整的街景或街道场景。

### 4.2 都依赖资产数据库和检索

两者都需要：

- object asset database
- text or semantic retrieval
- scene assembly / materialization

### 4.3 都有显式“场景蓝图”层

- UrbanVerse 用的是从视频蒸馏出的 urban scene graph
- RoadGen3D 用的是 `StreetComposeConfig -> StreetProgram -> ConstraintSet -> LayoutSolverResult`

两者都不是“纯端到端黑箱输出场景”。

## 5. 核心差异

### 5.1 输入模态不同

UrbanVerse 的主要输入是：

- city-tour videos
- RGB frame folders

RoadGen3D 的主要输入是：

- 文本 query
- AOI bbox / city
- OSM / POI context
- 设计配置参数

这意味着：

- UrbanVerse 首先解决“如何理解现实世界场景”
- RoadGen3D 首先解决“如何根据设计意图构造街道方案”

### 5.2 目标不同

UrbanVerse 的目标更偏：

- real-to-sim
- layout realism
- physics-aware simulation
- embodied AI training

RoadGen3D 的目标更偏：

- text-to-scene
- 设计约束下的街道生成
- 可解释 layout synthesis
- research / analysis / rule-driven generation

### 5.3 中间表示不同

UrbanVerse 以 `scene graph` 为核心，里面是：

- object nodes
- ground nodes
- sky node

RoadGen3D 以以下层级为核心：

- `StreetComposeConfig`
- `StreetProgram`
- `ConstraintSet`
- `LayoutSolverResult`

RoadGen3D 的中间表示更偏“设计方案”，UrbanVerse 的中间表示更偏“现实场景复原”。

### 5.4 资产库规模差异很大

当前 RoadGen3D 本地主资产库规模大约为：

- `127` 个资产
- `8` 个类别

这里的统计口径基于当前仓库本地 manifest，而不是论文式理想目标。

类别主要包括：

- bench
- bollard
- bus_stop
- hydrant
- lamp
- mailbox
- trash
- tree

此外，项目已经有少量 Objaverse 导入缓存，但仍属于很早期的小规模接入。

相比之下，UrbanVerse 的资产层优势非常明显：

- object 类别更多
- object 数量更多
- ground material 更完整
- sky map 更完整
- 物理属性和 affordance 标注更标准

### 5.5 检索链路不同

UrbanVerse 的论文方案是多阶段 retrieval：

```text
semantic matching
  -> geometry filtering
  -> appearance selection
```

也就是：

- 先做语义匹配
- 再做尺寸/几何过滤
- 再做外观重排

RoadGen3D 当前以 `CLIP text embedding + FAISS` 为主，重点是：

- 用文本描述检索资产
- 把资产放到 solver 给出的 slot 中

所以我们现在的检索更强于“文字到类别/资产”的可控落地，但弱于“与真实观察外观一致”的 cousin retrieval。

### 5.6 地面与天空层差异明显

UrbanVerse 资产库内明确包含：

- road materials
- sidewalk materials
- HDRI sky maps

RoadGen3D 当前已经支持 scene texture，但仍然主要依赖一小组内置贴图和固定纹理包，
本质上还是工程化默认资源，而不是数据库级 environment layer。

### 5.7 物理仿真定位不同

UrbanVerse 最终运行在 IsaacSim / UrbanSim 语境中，强调：

- rigid-body dynamics
- robot training
- dynamic agents

RoadGen3D 当前更偏：

- geometry generation
- scene export
- layout evaluation
- visual analysis

它不是一个完整机器人仿真平台。

## 6. 我们和他们的“差距”到底在哪里

如果把系统能力拆成上下两层，差距会更清楚。

### 6.1 下半层：我们和他们差距较大

这里指的是：

- 大规模数据库
- 物理属性标准化
- ground / sky 数据库
- 基于视频的 scene distillation
- real-to-sim scene reconstruction
- IsaacSim 级别的 physics-aware execution

这部分是 UrbanVerse 的主要优势区。

### 6.2 上半层：我们反而有自己的优势

这里指的是：

- 文本设计意图理解
- OSM / POI 结合
- 显式街道方案表达
- 规则集与约束求解
- 设计可解释性
- 参数可编辑性

这部分是 RoadGen3D 当前最特别、也最值得持续强化的地方。

## 7. RoadGen3D 当前最特别的地方

### 7.1 我们是“设计驱动”，不是“复原驱动”

UrbanVerse 更像是在回答：

- “现实世界这条街是什么样？”

RoadGen3D 更像是在回答：

- “用户想要一条什么样的街，我们如何把它设计出来？”

这是两个很不一样的问题。

### 7.2 我们有规则、约束和解释层

RoadGen3D 当前系统最有价值的地方，不只是能出 3D，而是能把设计过程拆成：

- 需求
- 空间上下文
- StreetProgram
- ConstraintSet
- solver edits / conflicts

这使得它天然适合：

- LLM + RAG 设计助手
- 规范驱动街道方案生成
- 面向研究的可解释实验平台

### 7.3 我们和真实城市 GIS / OSM 语境结合更紧

当前系统直接把：

- OSM 道路
- POI 类型
- 空间宽度
- segment-level context

引入到主链路中，这使得它更接近“可操作的街道设计系统”，而不是“从视觉输入恢复 3D 街景”。

### 7.4 我们保留文本到场景的主路线

这点非常重要。

RoadGen3D 后续借鉴 UrbanVerse 时，不应该把系统目标切成“做一个视频重建平台”，而应继续坚持：

- `文本 / 设计意图 -> 方案 -> 3D 场景`

UrbanVerse 值得借鉴的是：

- 数据层
- 环境层
- retrieval 设计

而不是把项目目标整体改写。

## 8. 哪些部分值得借鉴

### 8.1 最值得借鉴：数据库层

最直接也最有价值的是：

- UrbanVerse object assets
- UrbanVerse road / sidewalk materials
- UrbanVerse sky maps

这三类资源可以直接补强我们当前最薄弱的下半层。

### 8.2 第二值得借鉴：资产标注方式

UrbanVerse 的资产不只是 mesh，还带有更丰富的 metadata。

这启发我们未来的 manifest 不应只停留在：

- category
- text_desc
- mesh_path

而应逐步扩展为支持：

- metric bbox
- canonical front
- mass / friction 等 physical attributes
- affordance tags
- thumbnail / preview
- appearance embedding

### 8.3 第三值得借鉴：多阶段检索

UrbanVerse 的语义 + 尺寸 + 外观检索框架，对我们很有参考价值。

但 RoadGen3D 不需要照搬“视频 cousin retrieval”。

更适合我们的升级方式是：

- 保留文本驱动查询
- 在文本检索之后增加尺寸过滤
- 在有参考图或 style image 的场景下增加 appearance rerank

## 9. 哪些部分不应照搬

### 9.1 不应把主任务改成 video-to-scene

这会直接偏离 RoadGen3D 当前最有价值的系统定位。

### 9.2 不应复制完整 IsaacSim / UrbanSim 路线作为近期主目标

这会把工程复杂度一下子推到完全不同量级，也会冲淡当前最清晰的设计生成主线。

### 9.3 不应放弃 OSM + POI + StreetProgram + LayoutSolver

这是当前系统最有辨识度的部分，也是后续接 LLM + RAG 设计工作台最自然的支点。

## 10. 总体建议

RoadGen3D 借鉴 UrbanVerse 的正确方式，不是“做得和它一样”，而是：

```text
保留我们自己的上半层
  文本 / 设计意图 / OSM / POI / StreetProgram / ConstraintSet / LayoutSolver

借鉴并替换下半层
  object assets / ground materials / sky maps / retrieval backend
```

因此，建议把后续改造原则定为：

1. 不改变项目核心目标，继续坚持文本到场景的生成主线。
2. 借鉴 UrbanVerse 的数据库和 retrieval 设计，而不是复制它的视频重建主链路。
3. 用新的 asset / material / sky backend 强化当前生成结果的真实性与多样性。

## 11. 结论

UrbanVerse 和 RoadGen3D 面向的是相邻但不同的问题域。

- UrbanVerse 更强在 `现实世界重建 + 仿真数据库 + physics-aware scene creation`
- RoadGen3D 更强在 `设计驱动生成 + OSM/POI 结合 + 显式规则约束 + 可解释街道方案`

对我们来说，最优策略不是变成另一个 UrbanVerse，而是把 UrbanVerse 当作一个
高质量下游数据库与 retrieval 设计参考，用来增强我们自己的设计型生成系统。
