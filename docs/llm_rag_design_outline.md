# RoadGen3D LLM + RAG 设计大纲与优先级

更新时间：2026-03-23

本文结合以下两份文档：

- `docs/compare.md`
- `docs/urbanverse_adoption_plan.md`

并结合我们当前已经落地的 `LLM + RAG workbench` 原型，整理出下一阶段的：

- 产品定位
- 目标工作流
- 系统分层
- 改造重点
- 开发优先级

## 1. 一句话定位

RoadGen3D 的下一阶段目标不是变成 UrbanVerse 那样的 video-to-scene 系统，
而是做一个：

```text
用户设计意图
  -> LLM 澄清目标
  -> RAG 检索设计规范
  -> LLM 生成可解释设计草案
  -> OSM / POI / StreetProgram / LayoutSolver
  -> CLIP / asset backend 做资产落地
  -> 输出 3D 街道场景
```

也就是说，我们要做的是：

- 一个 `设计驱动型` 街道生成系统
- 而不是 `现实世界视频复原型` 仿真系统

## 2. 设计原则

### 2.1 保留我们自己的主线

我们必须保留当前最有价值的主干：

- `OSM + POI`
- `StreetProgram`
- `ConstraintSet`
- `LayoutSolver`
- `text-to-scene`

这部分是我们区别于 UrbanVerse 的核心竞争力。

### 2.2 借鉴 UrbanVerse 的下半层

我们主要借鉴 UrbanVerse 的：

- object asset database 思路
- ground material database 思路
- sky database 思路
- 多阶段 retrieval 思路

不照搬它的：

- video distillation 主链路
- IsaacSim / UrbanSim 全栈仿真目标

### 2.3 LLM 不直接负责资产编号

LLM 可以理解风格、规范和设计意图，但不适合稳定记忆具体资产 ID。

因此必须明确分工：

- LLM 负责：设计理解、规范归纳、参数建议、生成场景描述
- CLIP / retrieval backend 负责：把语言映射为具体 asset candidates 和 asset IDs

## 3. 目标工作流

我们期望的完整工作流如下。

### 3.1 第一步：用户与 LLM 确认设计目标

用户输入自然语言，例如：

- 步行安全街道
- 全龄友好街道
- 儿童和老人都方便通行的社区慢行街

LLM 的任务是先做“设计澄清”，识别：

- 用户目标
- 风格偏好
- 安全优先级
- 是否偏 pedestrian / transit / mixed-use
- 是否需要继续追问

这一层对应当前系统中的 `DesignIntent`。

### 3.2 第二步：LLM 生成 RAG 检索请求

LLM 不直接设计，而是先把用户语言拆成可检索的问题。

例如：

- pedestrian safety sidewalk width
- all ages street crossing design
- transit stop furnishing clearance
- lane reduction for walkability

这一层输出的是：

- `rag_queries`
- follow-up questions
- 初步安全和风格标签

### 3.3 第三步：RAG 检索设计规范文档

RAG 根据 LLM 生成的查询去检索知识库，例如：

- `Complete streets design guide.pdf`

输出的是：

- 证据片段
- 页码
- 章节名
- 参数提示

这一层对应当前系统中的 `RagEvidence`。

### 3.4 第四步：LLM 参考 RAG 证据生成设计草案

LLM 基于：

- 用户原始需求
- 对话历史
- RAG 证据
- 当前可编辑参数

生成 `DesignDraft`，核心内容包括：

- `normalized_scene_query`
- `compose_config_patch`
- `citations_by_field`
- `design_summary`
- `risk_notes`

这一步的重点不是直接输出完整场景，而是生成：

- 一个可解释、可编辑、带引用的设计草案

例如：

- 道路宽度建议
- 步道宽度建议
- 车道数建议
- transit / bike / vehicle 需求等级
- rule profile 建议
- `style_preset` 与 `beauty_mode` 建议

### 3.5 第五步：CLIP 把语言转成资产候选

这一步必须和 LLM 分开。

LLM 会给出：

- 归一化场景描述
- 风格描述
- 类别偏好
- 设施布局语义

但真正把这些语言映射成：

- `bench_xxx`
- `lamp_xxx`
- `trash_xxx`
- `mailbox_xxx`

这样的具体资产 ID，应由 CLIP + retrieval backend 完成。

因此这一步的本质是：

```text
LLM 负责说“我要什么”
CLIP 负责说“库里哪一个最像它”
```

### 3.6 第六步：现有布局主链路继续执行

后续继续使用我们现有系统：

- OSM / POI context
- StreetProgram
- ConstraintSet
- LayoutSolver
- asset realization
- scene export

也就是说，LLM + RAG 不是替代现有系统，而是给现有系统提供更高质量、
更可解释的 `StreetComposeConfig` 输入与 style / asset retrieval 条件。

其中美学相关输入不应只停留在 `style_preferences` 文本标签，
而应尽量落成显式字段，例如：

- `style_preset`
- `beauty_mode`

## 4. 系统分层

建议把整个系统明确拆成六层。

### 4.1 对话与意图层

职责：

- 和用户确认目标
- 理解风格、场景和优先级
- 生成 RAG 查询

当前对应：

- `src/roadgen3d/services/design_assistant.py`
- `src/roadgen3d/services/design_types.py`
- `ui/api/main.py`

核心对象：

- `ChatMessage`
- `DesignIntent`

这一层现在已经能抽取 `style_preferences`，下一步应继续把美学偏好沉淀为
可执行配置，而不是只保留在自然语言摘要里。

### 4.2 规范知识层

职责：

- 管理 PDF 知识库
- 进行 chunk 检索
- 输出规范证据

当前对应：

- `src/roadgen3d/knowledge/pdf_rag.py`
- `scripts/knowledge/build_pdf_knowledge_base.py`

核心对象：

- `RagEvidence`

### 4.3 设计草案层

职责：

- 把用户需求和 RAG 证据变成结构化设计草案
- 给出参数建议与字段引用

当前对应：

- `DesignDraft`
- `/api/design/draft`

这层是新系统最关键的“可解释中间层”。

### 4.4 街道方案层

职责：

- 继续使用现有设计和布局主链

包括：

- `StreetComposeConfig`
- `StreetProgram`
- `ConstraintSet`
- `LayoutSolver`

这层原则上近期不做大改。

### 4.5 资产与环境 backend 层

职责：

- object asset backend
- ground material backend
- sky backend
- retrieval backend

这层是借鉴 UrbanVerse 的主要落点。

### 4.6 场景输出层

职责：

- 资产摆放
- 材质应用
- 场景导出
- viewer 展示

## 5. 关键分工

为了避免系统职责混乱，必须明确下面这几个边界。

### 5.1 LLM 负责什么

LLM 负责：

- 设计意图理解
- 用户目标澄清
- 规范问题拆解
- RAG 查询生成
- 规范证据整合
- 参数建议
- 风格和场景语义描述

### 5.2 RAG 负责什么

RAG 负责：

- 找规范证据
- 返回文档片段、章节和页码
- 提供参数提示

RAG 不负责：

- 直接选资产
- 直接出场景

### 5.3 CLIP / retrieval backend 负责什么

CLIP / retrieval backend 负责：

- 文本到资产候选
- 资产类别内排序
- 未来支持尺寸过滤和外观重排

它不负责：

- 解释设计规范
- 做布局求解

### 5.4 Layout 系统负责什么

现有 layout 主链负责：

- 横断面生成
- 规则约束
- 空间求解
- 资产落位

它不负责：

- 做规范检索
- 做对话理解

## 6. 我们的近期系统蓝图

建议把近期系统蓝图明确成下面这条链路：

```text
User
  -> LLM intent clarification
  -> DesignIntent
  -> RAG query generation
  -> PDF knowledge retrieval
  -> RagEvidence
  -> LLM design drafting
  -> DesignDraft
  -> StreetComposeConfig patch
  -> OSM / POI / StreetProgram / LayoutSolver
  -> CLIP / asset backend retrieval
  -> scene composition
  -> viewer
```

其中：

- 上半层核心是 `DesignIntent + RagEvidence + DesignDraft`
- 中段核心是 `StreetComposeConfig + StreetProgram + LayoutSolver`
- 下半层核心是 `Object / Material / Sky backend + retrieval`

## 7. 优先级

下面给出建议优先级。

### P0：先把 LLM + RAG 设计闭环跑稳

这是当前最高优先级。

目标：

- 用户输入设计意图
- LLM 生成 `DesignIntent`
- RAG 返回证据
- LLM 产出 `DesignDraft`
- 用户可确认和编辑参数
- 系统能生成场景

为什么是最高优先级：

- 这是新产品体验的入口
- 也是我们区别于传统 text-to-3D 的关键卖点
- 它能先验证“规范驱动设计”是否真正有价值

当前已经有原型基础，因此应该优先补强：

- prompt 稳定性
- citations_by_field 质量
- 空证据和异常处理
- UI 参数编辑体验

### P1：接入 UrbanVerse object assets

这是第一阶段最值的第一个工程项。

目标：

- 把 UrbanVerse object assets 映射进我们的 manifest
- 优先接 street furniture 重合类别
- 不改主 layout 链路

为什么优先：

- 能最快提升场景真实感和资产多样性
- 对现有 runtime 最友好
- 对用户可见效果最直接

首批建议类别：

- bench
- lamp
- trash
- mailbox
- tree

### P2：建立 ground material 与 sky manifest

这是第一阶段最值的第二个工程项。

目标：

- 建立 `ground_material_manifest`
- 建立 `sky_manifest`
- 把 road / sidewalk / HDRI 从硬编码资源包改为数据层

为什么优先级紧跟 P1：

- 能显著提升氛围和 realism
- 与 object asset backend 一起构成完整的下半层增强
- 对后续多风格场景生成非常关键

### P3：抽象 asset / material / sky backend

这是第一阶段最值的第三个工程项。

目标：

- 不动 `OSM + POI + StreetProgram + LayoutSolver`
- 只替换底层 backend

为什么这一步重要：

- 它决定后续接 UrbanVerse 数据库是不是可持续
- 也决定未来是不是还能并行支持旧资产库

这一步完成后，系统会从“硬编码资源路径”转成“可替换 backend”。

### P4：升级多阶段 retrieval

这是第二阶段优先级最高的内容。

目标：

- 从当前 `纯文本 CLIP 检索`
- 升级到 `语义 + 尺寸 + 外观`

优先顺序建议：

1. 语义检索保留
2. 增加尺寸过滤
3. 最后再加 appearance rerank

原因是：

- 尺寸过滤和当前 solver / slot context 最匹配
- 外观重排价值很高，但依赖 thumbnail 和 appearance embedding 数据准备

### P5：扩展 RAG 知识域

在主闭环稳定后，再扩展知识库来源。

例如未来可以补：

- 其他街道设计规范
- 无障碍设计规范
- 慢行交通与公交站设计规范
- 地方城市设计导则

但近期不应过早扩知识源，否则会先增加系统复杂度和噪声。

### P6：视频分支不是近期优先项

即使 UrbanVerse 很强，这一项也不应进入近期主优先级。

原因：

- 它会改变系统目标
- 工程量极大
- 会冲淡我们当前最清晰的“设计驱动生成”路线

如果未来做，也应该作为独立研究支线，而不是主线替代。

## 8. 推荐开发顺序

建议按下面顺序推进：

1. 稳定 `LLM -> RAG -> DesignDraft -> Generate` 闭环
2. 接入 UrbanVerse object assets
3. 建立 `ground_material_manifest` 与 `sky_manifest`
4. 抽象 object / material / sky backend
5. 升级 retrieval 为 `语义 + 尺寸 + 外观`
6. 逐步扩展规范知识库

## 9. 最终建议

RoadGen3D 下一阶段最合理的定位是：

```text
一个由 LLM + RAG 驱动的、规范支持的、可解释的街道设计生成系统
```

它的核心不是“模仿 UrbanVerse”，而是：

- 保留我们的设计主线
- 借鉴他们的数据库和 retrieval 思路
- 用 LLM + RAG 增强设计质量
- 用 CLIP / backend 保证资产落地质量

因此，我们的清晰分工应当是：

- `LLM`：理解与设计
- `RAG`：规范证据
- `CLIP / retrieval backend`：资产映射
- `OSM + POI + StreetProgram + LayoutSolver`：空间方案与场景生成

这条路线既能保持项目独特性，也能最大化吸收 UrbanVerse 对下半层的启发。
