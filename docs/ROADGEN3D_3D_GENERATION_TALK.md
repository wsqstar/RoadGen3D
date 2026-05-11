# RoadGen3D 3D 生成流程演讲稿（修订版）

> 用途：5 分钟视频或组会口头汇报  
> 核心表述：RoadGen3D 是一个规则/约束驱动、AI 辅助的 3D 街道场景生成与评价框架。它不是把一句 prompt 黑箱变成 mesh，而是把设计意图编译成道路骨架、空间约束、家具槽位、资产放置、3D 输出和评分反馈。

## 最近提交对这版讲稿的校准

| 最近提交 | 对讲稿的影响 |
| --- | --- |
| `3a8566808 track-street-furniture-manifest` | 街道家具资产已经进入可追踪 manifest，共 295 条记录。讲家具时应强调资产库、类别、质量、可用性和检索，而不是说“随便生成一些物体”。 |
| `50a9fb4d4 feat: add style blend/transfer intent handling for draft prompts` | 风格不再只是固定 preset。系统能区分“融合一种风格”和“切换到另一种风格”，并把它变成可审计的 config patch。 |
| `1ef1a3871 feat: include design variant metadata in scene summaries` | 生成结果会带 variant metadata，便于解释不同方案从哪里来、用了哪些参数、如何对比。 |
| `0e4d9ae6f refactor: map center median strip role to safety island` | 道路骨架语义更细，中央分隔带不只是几何带，也可以被解释为安全岛。 |
| `2c4ed017c feat: add tapered bus lane widening and bus-stop slot tests` | 公交优先相关的道路加宽、公交站槽位和测试已经进入生成链路，讲 transit priority 时可以更具体。 |

## 5 分钟结构

| 时间 | 内容 | 画面建议 |
| --- | --- | --- |
| 0:00-0:35 | 项目定位：规则/约束驱动，AI 辅助，不是黑箱 prompt-to-3D | Viewer 全景、最终 3D 场景 |
| 0:35-1:25 | 输入如何进入系统：Scenario Designs、prompt、preset、template patch | Scenario Designs 面板或 catalog 片段 |
| 1:25-2:20 | 3D 生成主链路：DesignDraft -> SceneContext -> StreetProgram -> ConstraintSet -> LayoutSolver -> scene_layout/glb | 流程图、生成进度、scene_layout.json |
| 2:20-3:10 | 道路骨架与街道家具：骨架先定空间，家具 profile 再生成槽位和资产放置 | 横断面、slot plan、家具摆放 |
| 3:10-3:55 | 评分结构：walkability、safety、beauty、overall，以及 scenario rubric | 评价面板、对比视图 |
| 3:55-4:35 | LLM/RAG 接入：用于意图整理、参数建议、证据检索和视觉评价，不替代规则求解 | RAG evidence、patch、trace |
| 4:35-5:00 | 预设风格与风格转换：preset 是参数包，blend/transfer 是可审计的风格变换 | 风格对比、公交优先/步行友好示例 |

## 可直接照读的 5 分钟讲稿

大家好，这个视频我会介绍 RoadGen3D 是如何从设计意图生成 3D 街道场景的，以及系统里面道路骨架、街道家具、评分、LLM/RAG 和风格预设分别扮演什么角色。

先说项目定位。RoadGen3D 不是一个简单的 3D Viewer，也不是一句自然语言 prompt 直接黑箱生成 mesh。它更准确的定位是：一个规则和约束驱动、AI 辅助的 3D 街道场景生成与评价框架。也就是说，我们关注的不是只生成一个好看的街景，而是建立一条可以解释、可以控制、可以评价、也可以迭代的设计链路。

这条链路的起点可以有几种。当前最稳定的演示路径是 Scenario Designs，也就是预先定义好的场景设计目录。每个场景会记录设计目标、功能区、surface annotation、template patch 和 compose config patch。除此之外，系统也支持自然语言 prompt、preset、graph template、OSM 或 reference annotation。但组会里建议重点讲 Scenario Designs，因为它最稳定，也最能说明系统不是任意发挥，而是把设计意图结构化之后再生成。

生成过程可以分成五层。

第一层是 `DesignDraft`。它把用户或场景目录里的设计意图整理成一个后端能理解的草案，包括规范化后的 query、配置 patch、参数来源、设计摘要和风险提示。这里的重点是：输入不是只有一段文字，而是会变成可追踪的结构化设计请求。

第二层是 `SceneContext`。它决定这次生成用什么空间上下文。比如当前主线经常使用 graph template，也就是先有一个基础道路图模板，再通过 template patch 修改道路横断面、功能带、站点、入口和其他空间元素。如果是 reference annotation 或 OSM，则会先把外部标注或真实道路上下文转换成系统内部可以使用的场景上下文。

第三层是核心生成，也就是 `compose_street_scene()`。这里系统不会马上生成 3D mesh，而是先生成显式中间表示。`StreetProgram` 描述道路类型、车道、人行道、功能带、街道家具需求和设计目标；`ConstraintSet` 描述硬约束和软约束，例如人行净宽、家具允许放在哪些 band、公交边缘空间、车行和慢行需求；`LayoutSolver` 再把这些约束求解成具体的 band 宽度和 slot plan。

这就是 RoadGen3D 的关键思想：先把“道路应该如何组织”讲清楚，再去放置物体和输出模型。道路骨架不是后期装饰，而是生成前的结构基础。它决定道路的中心线、车道、人行空间、中央分隔带、安全岛、公交边缘、家具带和可放置区域。最近的提交也把 center median strip 更明确地映射成 safety island，这说明系统正在把道路几何和道路语义绑定起来。

第四层是街道家具设计。这里不是随机摆几个长椅和路灯，而是从 semantic profile 开始。比如用户说“儿童友好的学校街道”，系统可以把道路骨架理解成 `child_friendly_school`，再推荐对应的街道家具 profile，例如 `pedestrian_friendly`。这个 profile 会展开成一组生成参数：设计规则 profile、目标 profile、视觉 style preset、家具密度、行人/自行车/公交/机动车需求等级，以及必须出现或可选出现的家具类别。

然后 `StreetProgram` 会估算不同家具的需求量，比如 bench、lamp、trash、bollard、tree 或 bus_stop。`LayoutSolver` 会把这些需求变成 slot plan，决定每类家具放在哪个 band、左右两侧如何平衡、间距是否合理、是否需要靠近 POI 或公交站点。之后系统再根据资产 manifest 和检索结果选择实际 3D 资产，做碰撞检查、尺度归一、放置和导出。最近提交已经把街道家具 manifest 纳入仓库，共 295 条记录，所以现在讲家具时应强调它是有资产来源、类别、质量等级和可用性标记的，而不是临时拼贴。

第五层是输出。系统最终会生成 `scene_layout.json` 和 `scene.glb`。`scene.glb` 是 Viewer 中看到的 3D 模型；`scene_layout.json` 更像事实记录，里面保留道路结构、band、slot plan、资产放置、生成参数和摘要信息。后续的评分、对比、diff、报告和再生成，主要都依赖这个结构化输出。

接下来讲评分。RoadGen3D 的评分不是只看一个截图。当前主评价入口会读取 `scene_layout.json`，并且可以接收 Viewer 捕获的 rendered views。评分分成 walkability、safety、beauty 和 overall。Walkability 主要来自结构化布局，比如人行空间、功能带、家具占用、道路组织和可达性。Safety 和 beauty 可以结合结构化指标与视觉/LLM 评价，例如照明、保护感、可读性、视觉协调和空间丰富度。Overall 不是永远强行返回，只有 safety 和 beauty 都可用时才计算综合分，这样可以避免把缺失的视觉评价伪装成完整总分。对于批量 Scenario Designs，还有一层 scenario rubric，它更偏结构化门槛，用 Pass、Review、Fail 去判断场景是否满足预期。

然后是 LLM 和 RAG。这里最容易误解，所以要讲清楚：LLM/RAG 是增强层，不是主生成器。系统可以用 LLM 把自然语言整理成设计意图，把中文或含糊的目标改写成适合检索 complete streets 文档的英文 RAG query，再从知识库或参数库里取证据，生成 `compose_config_patch`。但这些 patch 进入后端后会被白名单字段校验，显式输入的参数优先级高于 LLM 建议。也就是说，LLM 提供建议和证据，规则层负责约束和落地。当前 Scenario Designs 批量生成通常使用 `preset_id=skip_llm`，不会每个样本都重新调用 LLM。LLM 还可以用于视觉评价和改进建议，但也不是替代 `StreetProgram`、`ConstraintSet` 和 `LayoutSolver`。

最后讲预设风格和风格转换。RoadGen3D 里的 preset 不是简单的颜色主题，而是一组可执行的生成参数包。它通常包括 `street_furniture_profile`、`style_preset`、`design_rule_profile`、`objective_profile`、家具密度、交通需求等级，以及最低必须出现的家具类别。比如 `pedestrian_friendly` 往往会对应更高的行人需求、更强调长椅、路灯、垃圾桶和 bollard；`transit_priority` 会提高公交需求，引入公交站相关槽位，并使用更适合公交优先场景的规则和视觉 preset。

最近的风格提交还支持两种意图：blend 和 transfer。Blend 是融合，比如“在步行友好的基础上融合公交优先”，系统会保留行人优先的底色，同时增加 bus_stop、提高 transit demand，并调整密度。Transfer 是切换，比如“把这个方案转换成公交优先”，系统会把目标 profile 改成 `transit_priority`，并同步切换设计规则和视觉 preset。这个过程不是直接改 UI 文案，而是会生成可审计的 config patch，并在结果摘要里保留风格变换的来源和被覆盖字段。

所以总结一下，RoadGen3D 的核心不是“生成一张好看的 3D 街景图”，而是把街道设计变成一条完整的计算流程：从场景意图，到道路骨架，到规则约束，到街道家具槽位和资产放置，再到 3D 输出、评分和迭代。LLM/RAG 让输入和证据更智能，preset 和风格转换让设计目标更容易控制，而真正保证结果可解释、可复现、可评价的，是中间表示、规则约束和结构化输出。

## 可以在视频里重点展示的流程图

```text
Scenario Designs / Prompt / Preset
  -> DesignDraft
  -> SceneContext
  -> Graph Template / Reference / OSM bridge
  -> StreetProgram
  -> ConstraintSet
  -> LayoutSolverResult + Slot Plans
  -> Asset Manifest + Retrieval + Placement
  -> scene_layout.json + scene.glb
  -> walkability / safety / beauty / overall
  -> Viewer compare / report / iteration
```

## 讲道路骨架时可以用的短句

道路骨架是生成前的空间结构，不是最后的视觉装饰。它决定中心线、车道、人行空间、家具带、公交边缘、安全岛和可放置区域。后面的街道家具、建筑和材质都要服从这个骨架和它对应的约束。

## 讲街道家具时可以用的短句

街道家具不是随机摆放。系统先根据 skeleton design profile 推荐 street furniture profile，再展开成密度、需求等级、必须类别和可选类别。然后 LayoutSolver 生成 slot plan，最后用资产 manifest 和检索结果选择实际 3D 资产并放进场景。

## 讲 LLM/RAG 时可以用的短句

LLM/RAG 负责把人的模糊意图变成更清晰的参数建议和证据引用，但它不是直接生成 3D 的核心。核心生成仍然走 `StreetProgram`、`ConstraintSet`、`LayoutSolver` 和 `scene_layout.json` 这条可审计链路。

## 讲风格转换时可以用的短句

预设风格不是皮肤，而是一组生成参数。Blend 是在原有风格上融合目标特征，Transfer 是切换到目标风格。两者都会生成 config patch，并记录哪些字段被保留、提升或覆盖。

## 容易被问到的问题

**Q：RoadGen3D 是不是每次都调用 LLM？**  
不是。当前 Scenario Designs 批量生成通常使用 `preset_id=skip_llm`，主要走 catalog、template patch 和规则求解。LLM/RAG 主要用于自然语言设计草案、参数建议、证据检索和视觉评价。

**Q：评分是真实结构评分还是 mock？**  
主评价读取真实 `scene_layout.json`。Walkability 主要来自结构化布局；safety 和 beauty 可以结合 rendered views 和视觉/LLM；scenario rubric 是独立的结构化门槛评估。

**Q：preset 和 style preset 有什么区别？**  
preset 是较大的生成意图或参数入口；`style_preset` 更偏视觉和展示风格。真正影响街道家具和道路规则的，还包括 `street_furniture_profile`、`design_rule_profile`、`objective_profile`、density 和 demand levels。

**Q：为什么不直接 prompt-to-3D？**  
街道设计需要可控和可解释。直接 prompt-to-3D 很难保证人行净宽、公交站位置、家具间距、安全岛、资产碰撞和评分反馈。RoadGen3D 选择把自然语言先编译成结构化约束，再生成 3D。
