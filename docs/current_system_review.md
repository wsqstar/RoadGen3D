# RoadGen3D 当前系统总览

更新时间：2026-03-09

## 1. 这套系统现在是什么

RoadGen3D 现在已经不是单纯的 `text -> 3D asset` demo，也不只是一个“神经符号街道生成”原型。

当前更准确的定位是：

- 一个面向街道场景的 `准备 -> 生成 -> 研究` 工作台
- 以 `OSM + POI + StreetProgram + ConstraintSet + LayoutSolver` 为核心的街道生成系统
- 同时保留资产检索、研究训练、评估与可视化能力

所以它的系统边界已经包含三层：

1. 数据与环境准备
2. 街道生成与场景导出
3. 研究、蒸馏、训练与回放

## 2. 当前 UI 的三大页到底在做什么

### 2.1 准备

职责：

- 校验 manifest、latents、FAISS index 是否齐备
- 预热 OSM cache
- 发现 POI-rich roads

输入：

- 资产 manifest / mesh / latent 路径
- CLIP / Shape-E 相关目录
- AOI bbox 或城市

中间算法：

- readiness inspection
- latent encoding
- index building
- OSM fetch/cache
- POI-rich road discovery

输出：

- workspace readiness
- prepare steps
- discovered roads 表

### 2.2 生成街道

职责：

- 用当前 query 与 AOI 自动选路
- 推理街道程序
- 做约束求解
- 生成最终场景

输入：

- 文本 query
- 长度、车道数、道路宽度、步行带宽度、密度、seed
- layout mode / constraint mode
- AOI bbox / city
- design rule profile / program generator / layout solver

中间算法：

- 自动选取 POI-rich road
- OSM 解析与 POI 抽取
- POI-aware cross-section synthesis
- StreetProgram generation
- ConstraintSet compilation
- LayoutSolver
- 资产检索、anchor-slot 绑定、实例摆放、GLB/PLY 导出

输出：

- `scene.glb` / `scene.ply`
- `scene_layout.json`
- StreetProgram Summary
- Solver Summary
- POI / Spatial analysis

### 2.3 研究与训练

职责：

- 训练 layout policy
- 训练 program generator
- 蒸馏数据
- 回放当前 best model

输入：

- query 集
- 蒸馏 seed 范围
- policy/program 超参数
- checkpoint
- 当前生成页街道配置

中间算法：

- collect distilled scenes
- policy/program training
- eval
- run best model replay

输出：

- train/eval json
- ckpt
- 曲线
- best model 生成结果

## 3. 当前生成链路的真实主干

当前默认街道生成入口已经是下面这条链路：

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

这意味着“神经符号街道生成”仍然是核心，但已经只是整个工作台中的一段，而不是全部。

## 4. 当前系统已经具备的关键能力

### 4.1 OSM + POI 驱动

系统现在支持的规范化 POI 类型：

- `entrance`
- `bus_stop`
- `fire_hydrant`
- `crossing`
- `traffic_signals`
- `parking_entrance`
- `subway_entrance`
- `post_box`
- `waste_basket`
- `bollard`

这些 POI 已经参与：

- road discovery
- compose 前有效性筛选
- StreetProgram 统计与家具需求
- solver anchored slots
- POI Analysis / GLB marker / summary

### 4.2 POI 强绑定

当前系统已经不是“POI 只做 marker”。

现在的语义是：

- 选中的 road 必须在 compose 真实口径下保留有效 POI
- 资产型 POI 会进入需求和 anchored slots
- 如果缺失必要资产类别，会直接失败
- 不再接受“发现时有 POI，最终结果变 0”这种静默退化

### 4.3 POI 驱动横断面

OSM 模式下已经引入：

- 左右独立步行带宽度
- 车道缩减后的宽度再分配
- 必要时扩总路幅
- `poi_fit_feasible` 检查

这一层解决的是：

- 当限制车道数时，如何把释放的空间让给步行带
- 如何让 POI 仍然被道路 corridor 容纳

## 5. 当前标题为什么不够准确

原标题：

- `RoadGen3D 神经符号街道生成`

这个标题的问题不是错，而是偏窄：

- 它只强调“生成”
- 没体现“准备”和“研究”
- 没体现 OSM / POI 驱动
- 没体现这已经是一个工作台，而不是单页 demo

所以更合适的命名应该偏向：

- `RoadGen3D 街道生成与研究工作台`
- 或 `RoadGen3D POI驱动街道生成工作台`

本轮 UI 采用的是第二种，因为它更能反映当前系统最重要的增量：POI-aware OSM street generation。

## 6. 当前系统仍然存在的边界

- template 模式仍然保留，但能力明显弱于 OSM 模式
- learned program generator / learned policy 仍然不是唯一主路径，heuristic 与 banded fallback 还很重要
- 路网层面仍以单路段 / 单 AOI 组合为主，不是完整城市级多路网建模
- 新增 POI taxonomy 已经不少，但还没有扩到“完整街道设施 ontology”
- M8 距离特征目前没有全面扩到所有新增 POI

## 7. 我建议接下来持续维护的文档

如果后续继续迭代，建议固定维护三份文档：

1. `docs/current_system_review.md`
   - 讲当前系统是什么、有什么、边界在哪里
2. `docs/architecture_decisions.md`
   - 记录关键架构决策和为什么这样做
3. `docs/roadmap.md`
   - 记录下一阶段目标和未完成项

本文件先承担第 1 类职责。
