# RoadGen3D 路线图

更新时间：2026-03-09

本文只描述当前建议的开发优先级，不代表所有想法都会立刻实现。

## 1. 近期目标

### 1.1 稳定 OSM + POI 主路径

目标：

- 把当前 OSM 生成链路打磨成稳定默认路径

重点：

- 继续增加 POI-aware width / slot / placement 回归测试
- 把更多失败信息写入 summary，而不是只抛异常
- 统一 prepare / compose / research 对 discovered roads 的复用语义

完成标准：

- 用户默认走 `Prepare Workspace -> Run Street` 能稳定得到可解释结果

### 1.2 强化 POI 对布局的真实影响

目标：

- 让新增 POI 类型不只是进入统计，还能更明确影响布局结构

重点：

- `crossing` 对家具避让和过街空间的影响
- `traffic_signals` 的可视性约束
- `subway_entrance` 对 transit edge / control points 的强化
- `parking_entrance` 的 access-clearance 规则

完成标准：

- 至少 4 类新增约束型 POI 在 layout 中有明确可验证效果

### 1.3 提升横断面 synthesis 的解释性

目标：

- 让“为什么这条路最终变成这个宽度”在 UI 中更容易读懂

重点：

- 把 `poi_fit_report` 渲染成更易读的 summary
- 区分“车道释放宽度”与“额外扩张宽度”
- 显示左右两侧各自的 POI 压力

完成标准：

- 用户看 summary 能直接判断：
  - 哪一侧更挤
  - 是否扩总路幅
  - 扩张是由哪些 POI 造成的

## 2. 中期目标

### 2.1 扩展街道设施 ontology

目标：

- 把当前 POI taxonomy 扩到更完整的街道设施系统

候选方向：

- bike parking
- taxi stand
- loading zone
- access control
- street cabinet / utility box
- public toilet

原则：

- 先补“对道路空间有真实约束意义”的 POI
- 后补纯展示型 POI

### 2.2 让 segment-level graph 真正参与布局

目标：

- 从单路段横断面 + 均匀 slot，走向 segment-aware 生成

重点：

- 不同 segment 上的约束差异
- junction 邻近区域的 slot 抑制
- crossing / bus stop / access 在 segment graph 上的定位

完成标准：

- solver 不再只依赖全局 band，而能理解局部路段差异

### 2.3 learned program generator 更深接入

目标：

- 让 `learned_v1` 不只是可选替换，而是逐步成为强可用后端

重点：

- 把更多 program target 纳入训练目标
- 增加和 POI / width synthesis 对齐的数据
- 提高 learned 结果对 `observed_poi_counts` 和 `cross-section bands` 的一致性

完成标准：

- learned program generator 在主要 query 上不弱于 heuristic baseline

## 3. 长期目标

### 3.1 从单路段走向小路网生成

目标：

- 支持不止一条独立道路，而是一个小型 street network

关键问题：

- 路口控制
- 多路段连通性
- 不同道路等级与街道类型的协同

### 3.2 从“资产摆放”走向“街道设计系统”

目标：

- 让系统不只是把家具摆在路边，而是能表达设计意图

方向：

- design intent templates
- 可编辑 cross-section presets
- 多目标权衡解释
- 面向研究的数据导出和可视化

### 3.3 研究闭环标准化

目标：

- 把 research 页从“能训练”变成“稳定研究工作台”

方向：

- 统一训练数据版本
- 固定评估 protocol
- 自动记录 checkpoint provenance
- 结果对比看板

## 4. 当前明确不优先做的事

这些方向不是没价值，而是目前不应抢在主路径稳定之前：

- 完整建筑几何生成
- 大规模城市级路网建模
- 全部新 POI 一次性扩齐
- 彻底去掉 heuristic / rule fallback
- 先做复杂多智能体交通仿真

## 5. 当前推荐开发顺序

建议按下面顺序推进：

1. 稳定 OSM + POI + width synthesis 主路径
2. 强化约束型 POI 对 layout 的真实影响
3. 提升 summary / UI 的解释性
4. 扩展 segment-level graph 与 solver
5. 做 learned program / policy 的更深接入
6. 最后再考虑更大尺度路网与建筑方向
