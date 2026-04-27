# RoadGen3D 当前项目架构分析报告

> 生成时间：2026-04-19  
> 分析范围：当前仓库源码实现，重点覆盖两个前端（`web/workbench`、`web/viewer`）与一个后端（`web/api`），并追踪到其所依赖的核心引擎层。

## 1. 结论摘要

当前项目已经形成了比较清晰的“三层式”结构：

1. `web/workbench` 是面向设计流程的主前端，负责输入、生成、评估和优化闭环。
2. `web/viewer` 是面向结果消费和资产操作的第二前端，负责 Three.js 可视化、注释、资产编辑和对比查看。
3. `web/api` 是唯一的主业务后端，负责把 LLM/RAG、场景生成、评估、知识库与任务队列编排起来。

但从“实现现状”看，项目并不是一个完全标准的“两个纯前端 + 一个纯后端”结构，而是：

- 主业务后端只有一个，即 `web/api/main.py`。
- `web/viewer` 在开发态通过 `vite.config.ts` 自带了一层本地中间件式 API，用来读取布局文件、列举最近场景、编辑资产清单、生成 diff 图等。
- 因此，系统在运行时实际上表现为“一个主后端 + 一个 viewer 开发侧辅助服务 + 两个前端应用”。

整体上，这是一套“研究型产品架构”而不是“企业型平台架构”：功能链完整、迭代速度快、代码复用度高，但边界有几处开始变得模糊，尤其是 Workbench 的前端编排逻辑、Viewer 的本地 API、以及后端单体式服务对象已经出现明显的增长压力。

## 2. 总体架构

### 2.1 目录级结构

从仓库结构和 `Makefile` 看，开发环境的主入口是 `make dev`，会同时启动三个服务：

- FastAPI：`http://127.0.0.1:8010`
- Workbench：`http://127.0.0.1:4174`
- Viewer：`http://127.0.0.1:4173`

对应关系如下：

```text
用户
  │
  ├── Workbench (React + Vite)
  │      │
  │      └── 调用 FastAPI /api/*
  │
  ├── Viewer (Three.js + Vite)
  │      ├── 调用自身 Vite middleware 提供的 /api/layout、/api/file、/api/recent-layouts 等
  │      └── 部分功能期望调用主后端评估接口
  │
  └── FastAPI (主业务后端)
         │
         ├── DesignAssistantService
         ├── SceneJobService
         ├── generate_scene_from_draft
         ├── graph_template / metaurban / osm 场景桥接
         ├── road-metrics 评估子模块
         └── street_layout.compose_street_scene 核心生成引擎
```

### 2.2 主干调用链

当前最核心的业务链是：

```text
Workbench 输入
  -> /api/design/draft
  -> /api/scene/jobs
  -> /api/scene/jobs/{job_id}
  -> /api/design/evaluate/unified
  -> /api/design/improve
  -> 再次 /api/scene/jobs
```

对应源码主线：

- API 入口：[`web/api/main.py`](../web/api/main.py)
- 业务编排：[`src/roadgen3d/llm/design_workflow.py`](../src/roadgen3d/llm/design_workflow.py)
- 生成运行时：[`src/roadgen3d/services/design_runtime.py`](../src/roadgen3d/services/design_runtime.py)
- 任务队列：[`src/roadgen3d/services/scene_jobs.py`](../src/roadgen3d/services/scene_jobs.py)
- 最终生成核心：`src/roadgen3d/street_layout.py` 中的 `compose_street_scene`

## 3. 前端一：Workbench 架构分析

### 3.1 定位

`web/workbench` 是主操作台，负责承接“输入 -> 方案生成 -> 评估 -> 优化”的完整设计闭环。它不是通用组件化后台，而是一个明确围绕街道设计工作流组织的单页应用。

核心入口：

- 应用入口：[`web/workbench/src/App.tsx`](../web/workbench/src/App.tsx)
- 主业务 hook：[`web/workbench/src/hooks/useGeneration.ts`](../web/workbench/src/hooks/useGeneration.ts)
- API 封装：[`web/workbench/src/lib/api.ts`](../web/workbench/src/lib/api.ts)

### 3.2 页面结构

Workbench 的状态机非常直接，主要就是三步：

1. 输入阶段：预设模板或自由文本。
2. 生成阶段：生成 A/B/C 三个方案。
3. 评估阶段：查看评分，并可一键优化。

这一点在 `App.tsx` 中非常明显，`currentStep` 直接驱动三段主界面切换，属于“流程驱动 UI”，而不是“路由驱动 UI”。

### 3.3 数据流

#### 预设模式

- 预设模板从 `/api/presets` 获取。
- 图模板从 `/api/graph-templates` 获取。
- 选择预设后，通过 `/api/scene/jobs` 创建生成任务。
- 之后轮询 `/api/scene/jobs/{job_id}`。
- 生成完成后，再调用 `/api/design/evaluate/unified` 获取统一评估结果。

#### 自由描述模式

- 通过 `FreeTextInput` 向 `/api/design/draft` 发请求。
- 如果后端认为信息不足，返回 `clarification_required`，前端进入最多 3 轮补问。
- 超过轮数后可以 `force=true` 强制生成草案。
- 拿到 draft 后，进入与预设模式相同的生成流程。

### 3.4 实现特征

Workbench 的优点很明显：

- 业务流程清晰，用户路径短。
- 状态对象少，容易快速迭代。
- 生成、评估、优化的用户反馈链是闭环的。
- `useGeneration` 将主要异步流程集中封装，降低了组件层的复杂度。

但它也有一些结构性问题：

#### 问题 1：前端编排逻辑偏重

`App.tsx` 与 `useGeneration.ts` 合起来承担了过多业务语义：

- 方案 A/B/C 的变体策略
- 轮询逻辑
- 进度状态映射
- 评估后的状态回填
- 一键优化后的再生成

这使得 Workbench 更像“前端 orchestrator”，而不只是“视图层”。

#### 问题 2：A/B/C 方案是串行生成

在 `useGeneration.ts` 里，三个方案通过 `for` 循环依次创建和轮询，而不是并发提交。这意味着总时延近似等于三个方案耗时之和，交互体验上会被明显拉长。

这不是 bug，但属于当前架构的性能瓶颈。

#### 问题 3：前后端评估指标契约已经出现漂移

前端类型 `WalkabilityIndicators` 期待的是 `SID_CLR`、`TREE_SHADE` 等 11 项数值指标；但当前后端 `_extract_indicators()` 返回的是 `sidewalk_adequacy`、`tree_shading_rate`、`rule_satisfaction` 这类重新包装后的字段。

这说明：

- 类型定义仍保留着旧接口认知。
- 当前 UI 暂时没有完全依赖这组字段，因此问题被掩盖了。
- 一旦后续做更细的雷达图或指标明细，这里会成为实际兼容性风险。

### 3.5 架构判断

Workbench 当前处于“功能完整、结构可用，但需要开始分层”的阶段。它最适合下一步拆成：

- 纯 UI 组件层
- 工作流状态机/command 层
- API gateway 层

否则后续继续加入更多模式（OSM、MetaUrban、批量测试、历史对比）时，复杂度会继续堆到 `App + hook` 上。

## 4. 前端二：Viewer 架构分析

### 4.1 定位

`web/viewer` 名义上是 3D 查看器，但从当前代码看，它已经演化成一个多功能前端工具箱，至少包含三种子页面：

- `viewer`：3D 场景浏览
- `scene-graph`：注释/标注页面
- `asset-editor`：资产编辑器

路由入口在 [`web/viewer/src/main.ts`](../web/viewer/src/main.ts)，通过 hash route 在三种页面之间切换。

### 4.2 运行模式

Viewer 的架构与 Workbench 有一个本质差别：

- Workbench 是“纯前端，主要依赖主后端 API”。
- Viewer 是“前端 + 本地 dev middleware 组合体”。

`web/viewer/vite.config.ts` 不只是 Vite 配置，而是提供了大量运行时 API：

- `/api/layout`
- `/api/recent-layouts`
- `/api/file`
- `/api/asset-manifests`
- `/api/asset-manifest`
- `/api/asset-manifest/save`
- `/api/asset-manifest/delete`
- `/api/scenes/diff/image`
- `/api/presets`

也就是说，Viewer 在开发态自带了一个“面向本地文件系统的轻量服务层”。

### 4.3 Viewer 主页面结构

Viewer 主页面集中在 [`web/viewer/src/app.ts`](../web/viewer/src/app.ts)。

它不是 React 应用，而是较典型的 imperative Three.js app：

- 手工拼接 DOM
- 手工维护 UI 引用
- 手工创建场景、光照、相机、控制器、动画循环
- 手工绑定事件

这套方式的优点：

- 对 Three.js 交互性能和控制力更直接。
- 很适合工具型界面和复杂渲染状态。
- 不受 React 渲染模型束缚。

缺点也明显：

- `app.ts` 体量很大，职责很重。
- UI 状态、渲染状态、交互状态混在同一文件内。
- 长期维护成本会持续上升。

### 4.4 Viewer 实际承担的职责

从代码来看，Viewer 已经不只是“看模型”：

- 加载场景布局与生产步骤
- 自由漫游 / 第三人称 / frame mode
- 最小地图、graph overlay、layout overlay
- 音频 profile 播放
- 结果对比
- 评估面板
- 资产清单浏览、编辑、删除
- 注释和参考图相关能力

因此更准确的命名其实接近“Visualization Studio”而不是单纯 Viewer。

### 4.5 架构上的关键观察

#### 观察 1：Viewer 的“后端能力”被嵌在 Vite 配置里

这带来两个结果：

- 本地开发很方便，因为直接访问仓库文件即可。
- 但部署边界会不清晰，因为这些 API 不是 FastAPI 提供的，而是 dev server 提供的。

这意味着 Viewer 的很多能力天然偏“开发工具态”，而不是“标准生产服务态”。

#### 观察 2：Viewer 与主后端之间存在接口断裂风险

`app.ts` 中的评估按钮请求 `./api/design/evaluate`，但在 `web/viewer/vite.config.ts` 里没有看到对应 route，也没有看到代理到 `127.0.0.1:8010` 的配置。

基于当前源码，可以推断：

- `make viewer-web` 单独启动时，这个功能大概率无法直接工作。
- 它要么依赖额外的部署代理，要么是尚未补齐的开发接口。

这里属于“代码中存在的潜在断点”，建议在后续明确。

#### 观察 3：Viewer 内部预设与主后端预设重复维护

主后端的正式预设定义在 [`src/roadgen3d/presets.py`](../src/roadgen3d/presets.py)，而 Viewer 的 `vite.config.ts` 中又维护了一组独立的 `PRESETS` 常量。

这会带来典型的配置漂移风险：

- 名称不一致
- 参数不一致
- 新增/删除后容易漏改

### 4.6 架构判断

Viewer 当前已经是一个独立产品级子系统，但它的“工程边界”还不够稳定：

- 在产品定位上，它很强。
- 在部署模型上，它还偏开发工具。
- 在代码组织上，它正在向单体化脚本膨胀。

后续若继续扩展，建议将其拆分为：

- Three.js runtime
- 本地文件 API 适配层
- 资产编辑子应用
- 标注子应用

否则 `app.ts + vite.config.ts` 会继续承担越来越多非同类职责。

## 5. 后端：FastAPI 主业务后端分析

### 5.1 入口与定位

真正的后端入口是 [`web/api/main.py`](../web/api/main.py)。

`ui/api/main.py` 只是兼容层，单纯 re-export `web.api.main`，说明项目经历过一轮从旧 UI API 到新 Web API 的迁移，目前迁移已基本完成。

### 5.2 API 职责划分

`web/api/main.py` 里的接口大体可以分成 6 组：

1. 元数据接口：health、presets、graph-templates、reference-plans、china-cities
2. draft 接口：`/api/design/draft`
3. 生成接口：`/api/design/generate`、`/api/scene/jobs*`
4. 评估与优化接口：`/api/design/evaluate*`、`/api/design/improve`
5. 知识库接口：`/api/knowledge/*`
6. diff 接口：`/api/scenes/diff*`

从 API 设计上看，接口面向前端工作流而不是面向底层引擎，因此可读性较好。

### 5.3 真正的后端核心：DesignAssistantService

主业务编排几乎都收敛在 `DesignAssistantService`：

- draft 阶段：LLM + RAG + 参数补全 + 缓存
- generate 阶段：scene job service
- evaluate 阶段：LLM 评估 / road-metrics 评估
- improve 阶段：基于评估结果与 RAG 再生成 patch

这说明当前后端实际上是“单服务对象主导的单体应用”。

它的优势：

- 主逻辑集中，便于追调用链。
- 对研究迭代和功能试验非常友好。
- 外层 API 足够薄，真正复杂性集中在 service 层。

它的代价：

- `DesignAssistantService` 已经承载太多横向职责。
- 一旦再加入更多模式或模型后端，这个类会继续膨胀。

### 5.4 生成子系统

真正把 draft 转成场景的是 `generate_scene_from_draft()`，其特点是：

- 先把 `compose_config_patch` 合并进稳定默认值。
- 再根据 `scene_context.layout_mode` 选择不同分支：
  - `graph_template`
  - `metaurban`
  - `osm`
  - 普通 template
- 然后统一走到 `compose_street_scene()` 完成场景生成与导出。

这是一种典型的“入口统一、上下文分流、底层复用”设计。其架构思路是对的。

### 5.5 任务队列子系统

`SceneJobService` 当前是：

- 单进程
- 单后台线程
- 内存态队列
- 无持久化
- 无多 worker

这对于本地开发和研究 demo 完全足够，但对正式服务化来说有明显限制：

- 进程重启后任务状态丢失。
- 无法跨实例扩展。
- 长任务会阻塞后续任务排队。
- 不适合多人共享同一后端实例。

因此它更准确的定位是“本地/单机工作流队列”，不是“生产任务系统”。

## 6. 核心引擎层分析

### 6.1 graph template 是当前主入口模式

虽然后端保留了 template / osm / metaurban 多种 `layout_mode`，但从 Workbench 的调用方式看，当前主工作流固定使用：

```json
{
  "layout_mode": "graph_template",
  "graph_template_id": "..."
}
```

这说明目前真正稳定的主链路，是“图模板驱动的场景生成”，而不是开放式的城市区域生成。

### 6.2 graph template 到生成引擎的桥接

图模板定义在 [`src/roadgen3d/graph_templates.py`](../src/roadgen3d/graph_templates.py)，再通过 [`src/roadgen3d/graph_template_scene_bridge.py`](../src/roadgen3d/graph_template_scene_bridge.py) 转换成：

- `road_segment_graph`
- `projected_features`
- `placement_context`

然后这些中间结构被送入 `compose_street_scene()`。

这一步非常关键，因为它把“前端可选模板”与“底层几何/布局引擎”隔离开了，是当前系统中一个较成熟的边界。

### 6.3 评估引擎是相对独立的一层

评估逻辑已经较明显地从主项目中解耦出去，后端通过 `road-metrics` 子模块创建 `EvalEngine`，再对 `scene_layout.json` 进行评估。

这使得生成与评估之间形成了较好的“文件级契约”：

- 生成产物：`scene_layout.json`
- 评估输入：`scene_layout.json`

这也是当前整个系统最健康的一条边界之一。

## 7. 当前架构的主要优点

### 7.1 工作流闭环已经成立

这套系统并不是“能生成一个模型”的散点式原型，而是已经具备：

- 输入
- 生成
- 评估
- 改进
- 再生成

这样一个真正有产品意味的闭环。

### 7.2 两个前端的角色差异是清楚的

- Workbench 负责“设计过程”。
- Viewer 负责“结果查看与操作”。

这种分工是合理的，没有把所有职责都塞进一个大前端里。

### 7.3 后端对核心引擎做了有效包裹

FastAPI 并没有直接把底层函数裸露出去，而是通过 `DesignAssistantService`、`SceneJobService`、`design_runtime` 做了中间层。这个方向是对的。

### 7.4 图模板桥接层设计较成熟

`graph_template -> scene_bridge -> compose_street_scene` 这条链很清晰，是当前系统中最有“平台化潜力”的部分。

## 8. 当前架构的主要风险与技术债

### 8.1 主后端已经开始“单体 service 化”

`web/api/main.py + DesignAssistantService` 结构当前还能承载需求，但已经明显呈现“所有能力都往一个 service 里放”的趋势。

风险是：

- 修改某一个领域逻辑时，容易影响其他功能。
- 单元测试边界会越来越模糊。
- 新成员上手成本会上升。

### 8.2 Workbench 的 orchestration 太靠前端

现在前端知道太多生成策略和流程细节，例如：

- 三方案变体策略
- 轮询节奏
- 进度语义
- 优化后再生成逻辑

从长期看，这些更适合逐步下沉到后端 workflow API 或 job orchestration 层。

### 8.3 Viewer 的本地 API 让部署边界变复杂

Viewer 通过 `vite.config.ts` 访问真实文件系统，这是开发时非常方便的做法，但它天然不等价于标准后端服务。

这意味着：

- 本地可用，不代表线上可用。
- Viewer 的行为会依赖运行目录和本地文件权限。
- 未来若要部署到远端，会出现“前端依赖本地文件”的落差。

### 8.4 存在配置与接口漂移

当前已经能看到几类漂移：

- Workbench 指标类型与后端返回字段不一致。
- Viewer 预设与主后端预设重复维护。
- Viewer 评估按钮依赖的接口在本地 middleware 中未体现。

这类问题短期不一定炸，但长期会降低系统可预测性。

### 8.5 核心生成引擎仍然是巨型函数入口

`compose_street_scene` 位于 `street_layout.py` 深处，是当前真正的“能力黑盒”。这有两个含义：

- 对研究项目来说，这是正常现象。
- 对工程化扩展来说，这是最大的未来拆分点。

## 9. 建议的演进方向

如果后续目标是“继续快速研究”，当前架构仍然可用，只需控制复杂度增长。

如果后续目标是“更稳定的产品化/团队协作”，建议按下面顺序演进：

### 第一优先级

- 统一 Workbench 与后端的评估数据契约。
- 统一 Viewer 与主后端的 presets 来源。
- 明确 Viewer 的评估接口在开发态到底由谁提供。

### 第二优先级

- 将 Workbench 的“三方案生成与优化编排”向后端下沉。
- 为 `SceneJobService` 增加更明确的 progress/stage 输出，减少前端猜测。
- 把 `DesignAssistantService` 拆成 draft / generation / evaluation / improvement 四个子服务。

### 第三优先级

- 把 Viewer 的文件系统 API 从 `vite.config.ts` 中抽离成正式服务。
- 将 `compose_street_scene` 进一步拆分为可组合的 pipeline stages。
- 为 graph_template / osm / metaurban 三条链分别建立更明确的 adapter 层。

## 10. 最终判断

RoadGen3D 当前已经不是“松散的算法仓库”，而是一套具备明确交互形态和闭环能力的生成式街道设计系统。

它的架构特点可以概括为：

- 前端分工明确，但 Workbench 偏流程编排、Viewer 偏工具平台。
- 后端是单体式编排中台，核心逻辑集中在 `DesignAssistantService`。
- 生成与评估已经形成可复用的中间产物契约。
- 真正的风险不在“有没有架构”，而在“当前边界已经开始增长并出现漂移”。

如果把当前状态定义为一句话：

> 这是一套已经跑通产品闭环、并开始进入“需要整理工程边界”的研究型系统。

