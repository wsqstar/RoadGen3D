# RoadGen3D

RoadGen3D 当前是一套可运行的三维生成工程，目标是从文本检索资产并生成体素/网格，再进一步组合为街道场景。

当前已实现三层能力：
- M1: 单资产闭环 `text -> FAISS -> asset_id -> latent -> voxel -> mesh`
- M2: 真实数据链路（含 `mesh_ref` 无 Blender 分支）
- M3: 街道级多资产组合（检索 + 去重 + 碰撞约束 + 场景导出）

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

## 5.5 Gradio
```bash
.venv/bin/python scripts/m1_gradio_app.py --host 127.0.0.1 --port 7860 --inbrowser
```

---

## 6. 当前边界

- 当前不做跨模态训练（OpenShape/ULIP）
- `shapee` 直解 latent 只在 latent 维度匹配时可用；生产流程推荐 `mesh_ref`
- M3 目前是单段直路模板，不含复杂路口/曲线网络

---

## 7. 参考文档

- 详细运行手册见：`README_M1.md`
- 手动模型下载见：`docs/manual_download.md`
- Shape-E 环境说明见：`docs/shapee_setup.md`
