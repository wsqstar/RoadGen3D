# RoadGen3D 架构决策记录

更新时间：2026-03-09

本文不是完整设计文档，而是记录当前系统已经做出的关键架构决策，以及这些决策背后的原因。

## ADR-001: UI 采用 `准备 -> 生成 -> 研究` 三段式工作台

结论：

- 顶层 UI 不再按 milestone 脚本拆分
- 改按真实工作流组织为：
  - `准备`
  - `生成`
  - `研究`

原因：

- 用户真实操作顺序不是 `M1 -> M2 -> M3 -> M4 -> M5 -> M6`
- 他们更关心“先能跑，再能看结果，再能训练改进”
- 这也让 OSM、POI、solver、研究训练共享同一套配置与入口

影响：

- milestone 文档仍保留
- UI 语义以工作流为主，不再以 milestone 命名为主

## ADR-002: OSM 模式作为街道生成默认主路径

结论：

- `layout_mode="osm"` 是当前主路径
- `template` 继续保留，但主要用于兼容、对照和调试

原因：

- 当前系统的核心价值已经从“直路模板摆资产”转到“真实道路 + 真实 POI + 可解释生成”
- 许多关键能力只在 OSM 模式下有意义：
  - discovered roads
  - POI-rich road selection
  - POI-aware cross-section synthesis
  - OSM-based placement context

影响：

- 新能力优先对 OSM 模式实现
- template 模式尽量保持稳定，但不承担主要扩展压力

## ADR-003: `StreetProgram -> ConstraintSet -> LayoutSolver` 作为中间主干

结论：

- 不再走“query 直接推 slot 再摆放”的黑箱路径
- 改为显式中间表示：

```text
query/context
  -> StreetProgram
  -> ConstraintSet
  -> LayoutSolver
  -> asset realization
```

原因：

- 需要可解释、可测试、可编辑
- 需要让设计规则和 POI 约束进入结构化中间层
- 需要为 learned program generator 保留清晰训练目标

影响：

- `scene_layout.json` 中保留 `street_program`、`constraint_set`、`solver`
- UI summary 也围绕这三层组织

## ADR-004: discovered roads 采用自动选路，而不是手工点选

结论：

- `discovered roads` 表默认是只读展示
- `Run Street` 自动从 discovered roads 中选择候选路
- 候选顺序由 `seed` 稳定随机

原因：

- 用户并不想手动点表来选路
- 真正想要的是“自动找到有 POI 的路并生成”
- 交互越简单，端到端行为越容易稳定

影响：

- 选路逻辑必须可复现
- compose 前必须对候选路再做真实过滤，而不是只信 discovery 缓存

## ADR-005: POI 是生成硬输入，不只是可视化附属信息

结论：

- POI 必须进入：
  - road selection
  - StreetProgram
  - slot planning
  - placement
  - summary / visualization

原因：

- 如果 POI 只做 marker，系统会出现“发现阶段有 POI，最终生成里没了”的假阳性
- 这会破坏系统可信度

当前语义：

- asset-backed POI 必须绑定到需求和 anchored slots
- 若缺少对应资产类别，流程直接失败
- 不接受静默降级为 `poi=0`

## ADR-006: POI taxonomy 采用统一规范化映射

结论：

- 引入统一 POI taxonomy，而不是在各模块里各写一套 tag 判断

当前规范化 POI：

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

原因：

- OSM ingest、discovery、summary、UI、solver 必须共享同一套类型系统
- 否则命名和行为会分裂

影响：

- summary 和可视化必须动态读取 taxonomy
- discovery 不再只围绕固定三类 POI

## ADR-007: 选路必须使用 compose 真实口径复核

结论：

- road discovery 的结果只是候选集
- `Run Street` 在真正 compose 前，必须重新做一次有效 POI 探测

原因：

- discovery 和 compose 的空间口径可能不同
- 不做复核会出现：
  - discovered road 看起来 POI 很多
  - compose 真实过滤后 POI 掉空

影响：

- 候选路选择逻辑不只看 `poi_count`
- 还看：
  - `effective_poi_count / poi_score`
  - `poi_fit_feasible`

## ADR-008: 步行带宽度由 POI 驱动，而不是固定输入

结论：

- `road_width_m` 继续表示基准车行带宽
- `sidewalk_width_m` 只作为 OSM 模式下的初始种子值
- 最终横断面宽度以 synthesis 结果为准

原因：

- 固定宽度无法解释“为什么这条路装不下这些 POI”
- 当用户限制车道数时，释放出的宽度应该优先分配给步行带

影响：

- 当前系统支持：
  - 左右独立步行带宽度
  - 车道缩减后的宽度重分配
  - 必要时扩总路幅
  - `poi_fit_feasible` 失败直接换路或失败

## ADR-009: 已落在车行带内的 POI 视为已被道路 corridor 容纳

结论：

- `POI must be on road or sidewalk`
- 不再强制要求所有 POI 都必须已经位于 sidewalk/furnishing 内

原因：

- 一些真实 OSM 点位天然就在车行带边界或车行带内侧
- 如果把这类点全部判成失败，会把许多实际可用道路误杀

影响：

- 当前 containment 语义是“必须被整条道路 corridor 容纳”
- 但仍会用 POI 去驱动步行带扩张，以提升布局可解释性

## ADR-010: solver 允许 fallback，但不能丢失 POI 锚点

结论：

- `milp_template_v1` 可以 fallback
- 但 fallback 后必须保留 anchored POI slots

原因：

- 某些 solver 后端不支持新的锚点语义
- 直接继续执行会误报“POI-backed slot 丢失”或真的把 POI 丢掉

影响：

- 当前带 anchored slots 的场景会自动 fallback 到可保锚点的 banded solver
- fallback 原因必须记录到 summary

## ADR-011: learned 后端目前是增强层，不是唯一依赖

结论：

- `learned_v1` program generator 和 `learned` layout policy 都是增强层
- 当前仍需稳定保留 heuristic / rule fallback

原因：

- 当前系统更重视可运行性与可解释性
- 训练链路仍在建设中，不能让主功能被 checkpoint 可用性绑定

影响：

- 缺 checkpoint 时必须自动回退
- fallback 原因要可见

## ADR-012: 文档采用“总览 + 决策 + 路线图”三件套

结论：

- 当前维护三类文档最合适：
  - `current_system_review.md`
  - `architecture_decisions.md`
  - `roadmap.md`

原因：

- milestone 文档更多是阶段性成果记录
- 现在需要面向“当前系统状态”的稳定文档，而不是只靠 milestone 叙述

影响：

- 后续新功能应优先更新这三份文档，而不是只改 README
