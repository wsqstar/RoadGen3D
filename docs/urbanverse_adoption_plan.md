# RoadGen3D 借鉴 UrbanVerse 的改造计划

更新时间：2026-03-23

本文只讨论“借鉴 UrbanVerse 的数据库和 retrieval 设计”后的 RoadGen3D 改造路线。

重要前提：

- 我们保留 `文本 / 设计意图 -> 街道方案 -> 3D 场景` 主路线
- 我们不把项目改造成 video-to-scene 系统
- 我们不照搬 UrbanVerse 的完整 real-to-sim 体系
- 我们主要借鉴其 `object assets + ground materials + sky maps + retrieval strategy`

## 1. 总体改造原则

目标不是替换现有系统主干，而是把当前系统拆成：

```text
上半层：保持不动
  query / OSM / POI / StreetProgram / ConstraintSet / LayoutSolver

下半层：逐步替换
  asset backend / material backend / sky backend / retrieval backend
```

因此，近期应坚持：

- 不动 `OSM + POI + StreetProgram + LayoutSolver`
- 先把底层数据库和环境层改成更可扩展的 backend
- 后续再升级 retrieval

## 2. 阶段划分

### 2.1 第一阶段目标

第一阶段最值的三件事是：

1. 把 UrbanVerse object assets 映射进我们的 manifest，先接入和我们已有类别重合的 street furniture。
2. 新增 `ground_material_manifest` 和 `sky_manifest`，把 road / sidewalk / HDRI 作为 scene layer，而不是硬编码贴图。
3. 保留现有 `OSM + POI + StreetProgram + LayoutSolver` 不动，只替换底层 asset / material backend。

### 2.2 第二阶段目标

第二阶段再考虑参考 UrbanVerse 的 retrieval 设计，把当前 `纯文本 CLIP 检索`
升级为 `语义 + 尺寸 + 外观` 的多阶段检索。

这里仍然保持：

- 文本到场景的主入口不变
- 不引入 video distillation 作为近期主路径

## 3. 第一阶段详细计划

### 3.1 子目标 A：UrbanVerse object assets 接入

#### 目标

把 UrbanVerse 的 object assets 接入到 RoadGen3D 当前资产体系中，但只优先接入与现有类别重合、
并且能直接服务街道生成 runtime 的类别。

#### 优先类别

建议先做：

- bench
- lamp
- trash
- mailbox
- tree

第二批再看：

- bollard
- bus_stop
- hydrant
- building

这些类别优先级较低的原因是：

- 语义映射更难
- 几何质量和 upright 假设更敏感
- 与当前街道 runtime 的耦合更复杂

#### 设计原则

- 不要求 UrbanVerse 原始 category 直接等于 RoadGen category
- 允许建立一层 `source_category -> canonical_category` 映射
- 先解决“可接入”，再解决“全覆盖”

#### 需要新增或调整的内容

建议新增：

- `data/schemas/object_assets_manifest_v2.schema.json`
- `scripts/m2_15_import_urbanverse_assets.py`
- `src/roadgen3d/urbanverse_import.py`

建议在新 manifest 中标准化以下字段：

- `asset_id`
- `source_dataset`
- `source_uid`
- `category`
- `source_category`
- `text_desc`
- `mesh_path`
- `thumbnail_path`
- `latent_path`
- `license`
- `split`
- `metric_width_m`
- `metric_depth_m`
- `metric_height_m`
- `canonical_front`
- `mass_kg`
- `friction`
- `affordance_tags`
- `appearance_embedding_path`

#### 当前代码改造点

重点影响模块：

- `src/roadgen3d/street_layout.py`
- `src/roadgen3d/embedder.py`
- `src/roadgen3d/index_store.py`
- `data/schemas/real_assets_manifest.schema.json`

建议策略不是直接破坏旧 schema，而是：

- 保留旧 manifest 兼容
- 新增 v2 schema 或扩展字段读取逻辑
- 逐步让 runtime 优先消费 richer metadata

### 3.2 子目标 B：ground material 与 sky backend

#### 目标

把当前硬编码 scene texture 方案，升级为 manifest 驱动的 environment layer。

#### 当前问题

当前系统虽然支持 texture，但本质上还是固定资源包：

- road 贴图固定
- sidewalk 贴图固定
- sky 没有单独数据库层

这会导致：

- 场景风格变化有限
- road / sidewalk 材质多样性不足
- 光照与环境氛围表达能力弱

#### 新增数据层

建议新增两个 manifest：

- `data/materials/ground_material_manifest.jsonl`
- `data/materials/sky_manifest.jsonl`

建议 ground material row 至少包含：

- `material_id`
- `surface_type`，如 `road` / `sidewalk`
- `source_dataset`
- `license`
- `albedo_path`
- `normal_path`
- `roughness_path`
- `metallic_path`
- `preview_path`
- `style_tags`
- `weather_tags`
- `region_tags`

建议 sky row 至少包含：

- `sky_id`
- `source_dataset`
- `license`
- `hdri_path`
- `preview_path`
- `time_of_day`
- `weather_tags`
- `illumination_tags`
- `region_tags`

#### 代码层建议

建议新增：

- `src/roadgen3d/material_store.py`
- `src/roadgen3d/sky_store.py`
- `src/roadgen3d/scene_environment.py`

建议调整：

- `src/roadgen3d/scene_textures.py`
- `src/roadgen3d/street_layout.py`

改造目标是：

```text
旧模式：
  hard-coded texture pack -> scene mesh

新模式：
  ground material manifest / sky manifest
    -> select environment assets
    -> apply to scene base layer
```

#### 第一阶段不做的事

第一阶段不需要：

- 做复杂 material retrieval 学习器
- 做真实 photo matching
- 接 IsaacSim lighting pipeline

先把“可切换、可扩展、可索引的数据层”建立起来最重要。

### 3.3 子目标 C：保持上半层不动，只替换 backend

#### 目标

确保这次借鉴 UrbanVerse 不会破坏我们当前最重要的系统主线。

必须保持不动的核心链路：

- `OSM fetch + parse`
- `POI extraction`
- `POI-aware cross-section synthesis`
- `StreetProgram`
- `ConstraintSet`
- `LayoutSolver`

要替换的是：

- object asset source
- ground material source
- sky source
- asset retrieval backend

#### 推荐做法

建议明确抽出 backend interface：

- `ObjectAssetBackend`
- `GroundMaterialBackend`
- `SkyBackend`

然后让 `compose_street_scene(...)` 不再直接依赖：

- 某一个固定 manifest
- 某一个固定 texture pack

而是依赖 backend 提供：

- asset candidates
- material candidates
- sky candidates

## 4. 第二阶段详细计划

### 4.1 目标

在不改变文本到场景主路线的前提下，把当前检索升级为多阶段检索。

当前大致是：

```text
text query
  -> CLIP embedding
  -> FAISS retrieval
  -> category-aware placement
```

建议升级为：

```text
text query
  -> semantic retrieval
  -> size filtering
  -> appearance rerank
  -> slot-aware final selection
```

### 4.2 三阶段检索设计

#### 阶段 1：语义检索

保留当前文本驱动入口。

输入：

- user query
- normalized scene query
- slot category
- style hints

输出：

- top-k semantic candidates

#### 阶段 2：尺寸过滤

利用新 manifest 中的 metric metadata 做过滤：

- width
- depth
- height
- aspect ratio
- footprint compatibility

这一步尤其适合我们当前系统，因为 solver 已经给出了比较明确的 slot 和 band context。

#### 阶段 3：外观重排

当以下信息存在时，再做 appearance rerank：

- reference image
- style image
- material exemplar
- category preview

这一层可以用来借鉴 UrbanVerse 的思路，但不应成为主入口依赖。

### 4.3 第二阶段需要新增的能力

建议新增：

- `thumbnail_path` 管理
- appearance embedding 预计算
- size-aware candidate scoring
- slot / band context aware rerank

建议新增模块：

- `src/roadgen3d/appearance_index.py`
- `src/roadgen3d/retrieval_pipeline.py`
- `src/roadgen3d/retrieval_ranker.py`

## 5. 建议的工程任务拆分

### 5.1 第一阶段任务清单

任务 1：Object asset schema 扩展

- 新增 richer object manifest schema
- 兼容当前旧资产库
- 增加尺寸、朝向、物理和外观字段

任务 2：UrbanVerse object importer

- 读取 UrbanVerse metadata
- 做 category mapping
- 产出可直接被 RoadGen runtime 使用的 manifest

任务 3：Ground material manifest

- 建立 road / sidewalk 材质清单
- 支持 PBR 贴图路径和 preview

任务 4：Sky manifest

- 建立 HDRI 资源清单
- 支持 time-of-day / weather metadata

任务 5：backend abstraction

- 抽象 object / material / sky backend
- 让 `street_layout.py` 改为调用 backend interface

任务 6：scene base layer 改造

- 从固定 texture pack 改为 manifest 驱动选择
- 输出 environment summary 到 `scene_layout.json`

任务 7：基础验证

- 新 object assets 能被检索并摆放
- road / sidewalk 材质可切换
- sky 可替换
- 旧流程不回退

### 5.2 第二阶段任务清单

任务 1：semantic retrieval 和当前索引兼容

任务 2：size filtering 接入 slot context

任务 3：appearance embedding 与 rerank

任务 4：综合 candidate scoring

任务 5：回归测试与质量评估

## 6. 风险与注意事项

### 6.1 数据可用性与许可

前提是 UrbanVerse 数据库对外可获得，且许可证允许我们在本项目中使用。

### 6.2 类别映射不一定一一对应

UrbanVerse 的 object taxonomy 和我们的 canonical category 不会完全重合，
需要映射层，而不是假设能直接替换。

### 6.3 richer metadata 质量需要校验

即使导入成功，也要验证：

- 尺寸是否可信
- 朝向是否统一
- 缩放是否一致
- thumbnail 是否可用
- 许可信息是否完整

### 6.4 不要让 backend 改造反向污染主干

这次改造最需要避免的风险是：

- 为了接新数据库，破坏现有 `StreetProgram / LayoutSolver` 语义
- 为了接新 texture / sky，导致 compose runtime 变得难以维护

## 7. 最终建议

最合理的路线不是“做一个和 UrbanVerse 一样的系统”，而是：

```text
RoadGen3D 保持设计驱动主线
  +
借鉴 UrbanVerse 的数据库与 retrieval 设计
  =
更强的文本到街道场景生成系统
```

近期最值得投入的是第一阶段三件事：

1. 接 object assets
2. 接 ground materials 与 sky manifests
3. 把底层 backend 抽象出来，但不动上半层设计主链

等这三件事稳定后，再进入第二阶段的多阶段检索升级。
