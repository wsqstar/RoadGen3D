# RoadGen3D

RoadGen3D 当前是一套可运行的三维生成工程，目标是从文本检索资产并生成体素/网格，再进一步组合为街道场景。

当前已实现三层能力：
- M1: 单资产闭环 `text -> FAISS -> asset_id -> latent -> voxel -> mesh`
- M2: 真实数据链路（含 `mesh_ref` 无 Blender 分支）
- M3: 街道级多资产组合（检索 + 去重 + 碰撞约束 + 场景导出）

当前默认街道生成入口已经升级为神经符号式 v1：
- `text/context -> StreetProgram -> ConstraintSet -> LayoutSolver -> asset realization -> mesh export`
- 仍复用现有资产检索与网格实现层，但把街道结构与设计准则前移为显式中间表示

---

## 1. 系统输入

### 1.1 模型输入
- CLIP 文本模型（默认 `openai/clip-vit-base-patch32`）
- 可选 Shape-E 运行时（仅在 `--decoder shapee` 或真实编码模式需要）

### 1.2 数据输入
- M1/M2 资产元数据（`assets.jsonl`）
  - 关键字段：`asset_id`, `description`, `latent_path`
- M3 真实资产清单（`data/real/real_assets_manifest.jsonl`）
  - 必填字段：`asset_id`, `category`, `text_desc`, `mesh_path`, `latent_path`
- M3 Phase 1 backend 清单
  - `data/real/real_assets_manifest_v2.jsonl`
  - `data/materials/ground_material_manifest.jsonl`
  - `data/materials/sky_manifest.jsonl`
- 向量索引文件
  - `index_ip.faiss`
  - `id_map.json`

### 1.3 运行时输入
- 单资产查询：`--query "a wooden park bench"`
- 街道查询：`--query "modern clean urban street"`
- 布局参数（M3）：`length_m / road_width_m / sidewalk_width_m / density / seed / topk_per_category / max_trials_per_slot`

---

## 2. 系统输出

### 2.1 单资产输出（M1/M2）
- `artifacts/*/pipeline_result.json`
- `voxel_prob.npy`
- `voxel_bin.npy`
- `*_voxel.glb`
- `*_voxel.ply`

`pipeline_result.json` 关键字段：
- `query`
- `top_hit` (`asset_id`, `score`)
- `latent_shape`
- `voxel_shape`
- `occupied_voxels`
- `outputs`（包含 `voxel_prob`, `voxel_bin`, `mesh_glb`, `mesh_ply`，以及 `decoder_used` 等）

### 2.2 街道场景输出（M3）
- `artifacts/real/scene.glb`
- `artifacts/real/scene.ply`
- `artifacts/real/scene_layout.json`

`scene_layout.json` 关键字段：
- `config`（街道参数）
- `placements`（每个实例的类别、位置、朝向、AABB）
- `summary`
  - `instance_count`
  - `dropped_slots`
  - `unique_asset_count`
  - `diversity_ratio`
  - `per_category_unique`
  - `selection_source_counts`

---

## 3. 当前算法说明

### 3.1 文本检索（M1/M2/M3 通用）
1. 用 CLIP `get_text_features` 编码文本
2. 做 L2 归一化
3. 使用 FAISS `IndexFlatIP` 做最大内积检索

公式：
- 查询向量：`q = normalize(clip_text(query))`
- 检索：`argmax_z (q^T z)`

### 3.2 解码器
- `placeholder`（默认可用）
  - 轻量可复现占位解码器
  - 输出 `voxel_prob` + `voxel_bin`
- `shapee`（可选）
  - 优先读取真实 latent / mesh 引用
  - 支持失败回退到 placeholder（可配置 strict）

### 3.3 网格导出
- 默认 `marching_cubes`
- 失败时可切换 `cubes` 方法
- 输出 GLB（主展示）+ PLY（调试）

### 3.4 街道组合（M3）
- 固定模板：直路双向 + 双侧人行道
- 固定类别：`bench/lamp/trash/tree/bus_stop/mailbox/hydrant/bollard`
- 类别槽位数：按 `spacing / density` 计算
- 检索采样：Top-K 同类候选做 Softmax 加权
  - `p_i = softmax(score_i / 0.12)`
- 去重策略：类别内先不重复，耗尽后放宽重复（优先填满）
- 碰撞策略：2D AABB 强约束，冲突重采样，超限丢弃并计数

### 3.5 神经符号街道程序（最新）
- 中间表示：`StreetProgram`
  - 含道路类型、横断面、功能带、街道家具需求、控制点、设计目标、上下文条件
- 声明式规则层：`ConstraintSet` / `DesignRuleSpec`
  - 规则以硬/软约束声明，不再只依赖少量写死 penalty
- 布局求解层：`LayoutSolver`
  - 输出 `slot_plans / edits / conflicts / rule_evaluations`
  - 显式记录哪些对象被移动、删除、替换，以及原因
- 当前内置规则剖面：
  - `balanced_complete_street_v1`
  - `pedestrian_priority_v1`
  - `transit_priority_v1`

---

## 4. M3 资产质量与多样性控制（最新）

### 4.1 最低面数硬门槛（`m3_02`）
- `bench: 300`
- `lamp: 500`
- `trash: 300`
- `tree: 1500`
- `bus_stop: 800`
- `mailbox: 250`
- `hydrant: 350`
- `bollard: 180`

### 4.2 生成重试与复杂度调度
- 最多重试 `10` 次
- `complexity_level = min(3, attempt // 2)`
- 同时约束：`min_faces <= faces <= poly_budget_k * 1000`
- 不满足硬门槛或预算会重试；最终不满足则脚本非零退出

### 4.3 几何细节增强
- 树：分支层级 + 多团簇冠层
- 灯：底座/法兰环/支撑臂/灯罩细节

---

## 5. 推荐运行路径

## 5.1 安装
```bash
cd /Users/shiqi/Coding/github/GIStudio/RoadGen3D
.venv/bin/python -m pip install -r requirements-m1.txt
.venv/bin/python -m pip install -r requirements-m2.txt
.venv/bin/python -m pip install -r requirements-ui.txt
```

## 5.2 单资产闭环
```bash
.venv/bin/python scripts/m1_06_run_pipeline.py \
  --query "a wooden park bench" \
  --topk 1 \
  --data-dir data/m1 \
  --artifacts artifacts/m1 \
  --model-dir /Users/shiqi/Coding/github/GIStudio/RoadGen3D/models/clip-vit-base-patch32 \
  --local-files-only \
  --decoder placeholder \
  --export-format both
```

## 5.3 真实 latent（无 Blender，推荐）
```bash
.venv/bin/python scripts/m2_11_encode_shapee_latents.py \
  --manifest data/real/real_assets_manifest.jsonl \
  --output-manifest data/real/real_assets_manifest.jsonl \
  --latents-dir data/real/latents \
  --encode-mode mesh_ref
```

## 5.4 街道组合（M3）
```bash
.venv/bin/python scripts/m3_01_compose_street.py \
  --query "modern clean urban street" \
  --manifest data/real/real_assets_manifest.jsonl \
  --artifacts artifacts/real \
  --out-dir artifacts/real \
  --length-m 80 \
  --road-width-m 8 \
  --sidewalk-width-m 2.5 \
  --lane-count 2 \
  --density 1.0 \
  --seed 42 \
  --topk-per-category 20 \
  --max-trials-per-slot 30 \
  --model-dir /Users/shiqi/Coding/github/GIStudio/RoadGen3D/models/clip-vit-base-patch32 \
  --local-files-only \
  --export-format both
```

## 5.5 Dev 启动
```bash
make dev
```

默认会同时启动：
- `workbench-api`
- `workbench-web`
- `viewer-web`

如果还需要旧版 Gradio：

```bash
make gradio-dev
```

## 5.6 LLM + RAG Workbench
先为完整街道设计指南构建知识库：

```bash
make knowledge-build
```

启动新的生成后端 API：

```bash
make workbench-api
```

当前 canonical API 入口是 `web/api/main.py`，旧的 `ui/api/main.py` 只保留兼容壳。

Workbench 生成链路现在默认走异步 scene jobs：
- `POST /api/scene/jobs`
- `GET /api/scene/jobs`
- `GET /api/scene/jobs/{job_id}`
- `GET /api/scenes/recent`

兼容接口仍保留：
- `POST /api/design/generate`

首次运行前安装生成工作台前端依赖：

```bash
make workbench-install
```

启动新的 Web 生成工作台：

```bash
make workbench-web
```

如果要单独启动 viewer：

```bash
make viewer-install
make viewer-web
```

默认地址：
- API: `http://127.0.0.1:8010/api/health`
- Workbench: `http://127.0.0.1:4174`
- Viewer: `http://127.0.0.1:4173`

## 5.7 UrbanVerse 子集导入
目录包模式约定：

- `metadata/objects.jsonl`
- `metadata/ground_materials.jsonl`
- `metadata/skies.jsonl`

最小导入命令：

```bash
.venv/bin/python scripts/m2_15_import_urbanverse_subset.py \
  --input-root /path/to/urbanverse_subset \
  --subset-name first_wave \
  --append-object-manifest data/real/real_assets_manifest_v2.jsonl \
  --append-ground-manifest data/materials/ground_material_manifest.jsonl \
  --append-sky-manifest data/materials/sky_manifest.jsonl
```

默认情况下会：
- 先把源文件复制到 `artifacts/urbanverse_cache/<subset-name>/`
- 在 `data/urbanverse/<subset-name>/` 下生成独立 manifests 和审计报告
- 只有传入 `--append-*` 参数时，才会 upsert 到当前主资产库

---

## 6. 当前边界

- 当前不做跨模态训练（OpenShape/ULIP）
- `shapee` 直解 latent 只在 latent 维度匹配时可用；生产流程推荐 `mesh_ref`
- M3 目前是单段直路模板，不含复杂路口/曲线网络
- `StreetProgram` 目前是启发式生成器 `heuristic_v1`，尚未替换成学习式 program generator
- 规则求解器目前是 `banded` 启发式求解，不是 MILP/扩散式布局模型

---

## 7. 参考文档

- 详细运行手册见：`README_M1.md`
- 当前系统总览见：`docs/current_system_review.md`
- 架构决策记录见：`docs/architecture_decisions.md`
- 开发路线图见：`docs/roadmap.md`
- 手动模型下载见：`docs/manual_download.md`
- Shape-E 环境说明见：`docs/shapee_setup.md`

---

## 8. 可学习系统（M4）

M4 在 M3 的规则布局器上增加了可训练布局策略，目标是学习“每个槽位在 top-k 候选中应该选谁”。

### 8.1 学什么
- 学习对象：`slot + candidate -> score`
- 学习来源：用当前 `rule` 策略自动蒸馏出的 slot 级数据（监督学习）
- 不改变：类别约束、AABB 碰撞强约束、fallback pool 机制

### 8.2 怎么学
1. 数据蒸馏：`scripts/m4_01_collect_policy_data.py`
2. 特征构建：`src/roadgen3d/layout_features.py`（固定 32 维）
3. 训练模型：`src/roadgen3d/layout_policy.py`（MLP: `32 -> 64 -> 32 -> 1`）
4. 训练脚本：`scripts/m4_02_train_layout_policy.py`

训练输出：
- `artifacts/m4/layout_policy.pt`
- `artifacts/m4/layout_policy_meta.json`

M4 当前仍主要学习“资产选择”，但它现在工作在神经符号流水线的实现层：
- `StreetProgram` 与 `ConstraintSet` 决定街道结构
- `LayoutSolver` 产出槽位计划
- `layout_policy` 再学习“每个槽位在候选资产里选谁”

---

## 9. 神经符号街道生成（M6 v1）

新增公共接口：
- `StreetProgram`
- `DesignRuleSpec` / `ConstraintSet`
- `LayoutSolverInput` / `LayoutSolverResult`

新增 summary 指标：
- `rule_satisfaction_rate`
- `topology_validity`
- `cross_section_feasibility`
- `editability`
- `conflict_explainability`

`scene_layout.json` 现在额外包含：
- `street_program`
- `constraint_set`
- `solver`

CLI 新参数：
```bash
--design-rule-profile balanced_complete_street_v1|pedestrian_priority_v1|transit_priority_v1
--city-context generic_city
--target-street-type mixed_use
```
- `artifacts/m4/train_curve.json`

### 8.3 如何启用 learned policy

```bash
.venv/bin/python scripts/m3_01_compose_street.py \
  --query "modern clean urban street" \
  --manifest data/real/real_assets_manifest.jsonl \
  --artifacts artifacts/real \
  --out-dir artifacts/real \
  --placement-policy learned \
  --policy-ckpt artifacts/m4/layout_policy.pt \
  --policy-temperature 0.12 \
  --model-dir /Users/shiqi/Coding/github/GIStudio/RoadGen3D/models/clip-vit-base-patch32 \
  --local-files-only
```

如果 checkpoint 缺失或加载失败，系统会自动回退到 `rule`，并在输出中写入回退原因。

## 9. 评测系统（M4）

M4 新增工程评测闭环，目标是稳定衡量可学习策略与规则策略的工程质量，而不是只看单次可视化结果。

### 9.1 评测输入
- query 集（默认 `data/eval/queries_m4.txt`，缺省时使用内置 20 条模板）
- real manifest（`data/real/real_assets_manifest.jsonl`）
- real FAISS 索引（`artifacts/real/index_ip.faiss + id_map.json`）
- 策略模式（`rule` 或 `learned`）

### 9.2 指标定义
- `instance_count`
- `diversity_ratio`
- `dropped_slot_rate = dropped_slots / (instance_count + dropped_slots)`
- `overlap_rate`（AABB 两两重叠比率，目标 0）
- `retrieval_top3_category_hit`
- `latency_ms_total`
- `latency_ms_per_instance`

### 9.3 报告输出
- `artifacts/m4/eval_report.json`
- `artifacts/m4/eval_per_scene.csv`

`scene_layout.json` 的 `summary` 也会稳定包含：
- `policy_used`
- `latency_ms_total`
- `latency_ms_per_instance`
- `dropped_slot_rate`
- `overlap_rate`
- `retrieval_top3_category_hit`

### 9.4 评测命令

```bash
.venv/bin/python scripts/m4_10_eval_engineering.py \
  --queries data/eval/queries_m4.txt \
  --manifest data/real/real_assets_manifest.jsonl \
  --artifacts artifacts/real \
  --out-dir artifacts/m4 \
  --placement-policy learned \
  --policy-ckpt artifacts/m4/layout_policy.pt \
  --compare-rule \
  --model-dir /Users/shiqi/Coding/github/GIStudio/RoadGen3D/models/clip-vit-base-patch32 \
  --local-files-only
```

`learned` 模式下可启用 `--compare-rule`，报告会附带 `comparison_vs_rule` 的差值统计。
