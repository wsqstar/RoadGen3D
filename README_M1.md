# RoadGen3D Milestone-1 Runbook

M1 目标：验证后端闭环 `text -> FAISS -> asset_id -> latent -> voxel`。

## 0. 使用 uv 创建环境（推荐 Python 3.11/3.12）

优先使用本机已存在的 Python 3.11（最快）：

```bash
cd /Users/shiqi/Coding/github/GIStudio/RoadGen3D
UV_CACHE_DIR=/Users/shiqi/Coding/github/GIStudio/RoadGen3D/.uv-cache \
UV_PYTHON_INSTALL_DIR=/Users/shiqi/Coding/github/GIStudio/RoadGen3D/.uv-python \
/Users/shiqi/.local/bin/uv venv \
  --python /Users/shiqi/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 \
  .venv
```

也可用 3.12（会下载解释器）：

```bash
cd /Users/shiqi/Coding/github/GIStudio/RoadGen3D
UV_CACHE_DIR=/Users/shiqi/Coding/github/GIStudio/RoadGen3D/.uv-cache \
UV_PYTHON_INSTALL_DIR=/Users/shiqi/Coding/github/GIStudio/RoadGen3D/.uv-python \
/Users/shiqi/.local/bin/uv venv --python 3.12 .venv
```

若网络受限，再临时回退到系统 Python：

```bash
cd /Users/shiqi/Coding/github/GIStudio/RoadGen3D
UV_CACHE_DIR=/Users/shiqi/Coding/github/GIStudio/RoadGen3D/.uv-cache \
/Users/shiqi/.local/bin/uv venv --python /usr/bin/python3 .venv
```

说明：当前 `torch` 在 `cp313 + macOS arm64` 上常出现轮子不匹配，M1 阶段不建议用 3.13。
另外，`transformers` 加载 `.bin` 权重时要求 `torch>=2.6`（CVE-2025-32434 安全限制）。

## 1. 安装依赖

```bash
.venv/bin/python -m pip install -r requirements-m1.txt
.venv/bin/python -m pip install -r requirements-m2.txt
```

或使用 `uv`：

```bash
/Users/shiqi/.local/bin/uv pip install --python .venv/bin/python -r requirements-m1.txt
/Users/shiqi/.local/bin/uv pip install --python .venv/bin/python -r requirements-m2.txt
```

若你当前是旧版 torch（例如 2.5.1），可单独升级：

```bash
/Users/shiqi/.local/bin/uv pip install --python .venv/bin/python "torch>=2.6,<2.8"
```

## 2. 准备模型（离线优先）

按 [manual_download.md](/Users/shiqi/Coding/github/GIStudio/RoadGen3D/docs/manual_download.md) 下载
`openai/clip-vit-base-patch32` 到：

`/Users/shiqi/Coding/github/GIStudio/RoadGen3D/models/clip-vit-base-patch32`

## 3. 命令顺序（六步）

1) 环境检查

```bash
.venv/bin/python scripts/m1_00_check_env.py --out artifacts/m1/env_report.json
```

2) 生成 mock 数据

```bash
.venv/bin/python scripts/m1_01_seed_assets.py --out-dir data/m1 --num-assets 8 --seed 42
```

3) 编码资产文本

```bash
.venv/bin/python scripts/m1_02_embed_texts.py \
  --assets data/m1/assets.jsonl \
  --out artifacts/m1 \
  --model-dir /Users/shiqi/Coding/github/GIStudio/RoadGen3D/models/clip-vit-base-patch32 \
  --local-files-only
```

4) 构建 FAISS 索引

```bash
.venv/bin/python scripts/m1_03_build_faiss.py \
  --embeds artifacts/m1/asset_text_embeds.npy \
  --asset-ids artifacts/m1/asset_ids.json \
  --out artifacts/m1
```

5) 独立检索验证

```bash
.venv/bin/python scripts/m1_04_retrieve.py \
  --query "a wooden park bench" \
  --topk 3 \
  --artifacts artifacts/m1 \
  --model-dir /Users/shiqi/Coding/github/GIStudio/RoadGen3D/models/clip-vit-base-patch32 \
  --local-files-only
```

6) 一键闭环

```bash
.venv/bin/python scripts/m1_06_run_pipeline.py \
  --query "a wooden park bench" \
  --topk 1 \
  --data-dir data/m1 \
  --artifacts artifacts/m1 \
  --model-dir /Users/shiqi/Coding/github/GIStudio/RoadGen3D/models/clip-vit-base-patch32 \
  --local-files-only
```

7) 从历史体素导出网格（可选）

```bash
.venv/bin/python scripts/m1_07_export_mesh.py \
  --voxel-bin artifacts/m1/voxel_bin.npy \
  --out-dir artifacts/m1 \
  --method marching_cubes \
  --export-format both
```

## 4. 产物检查

- 环境报告：`artifacts/m1/env_report.json`
- 嵌入矩阵：`artifacts/m1/asset_text_embeds.npy`
- 检索索引：`artifacts/m1/index_ip.faiss`
- 检索结果：`artifacts/m1/last_retrieval.json`
- 体素结果：`artifacts/m1/voxel_prob.npy`, `artifacts/m1/voxel_bin.npy`
- 网格结果：`artifacts/m1/*_voxel.glb`, `artifacts/m1/*_voxel.ply`
- 闭环结果：`artifacts/m1/pipeline_result.json`

## 5. 测试

```bash
.venv/bin/python -m pytest -q
```

说明：测试优先验证工程闭环和错误处理，默认不依赖在线模型下载。

## 6. Gradio 界面

安装 UI 依赖：

```bash
/Users/shiqi/.local/bin/uv pip install --python .venv/bin/python -r requirements-ui.txt
```

启动界面：

```bash
.venv/bin/python scripts/m1_gradio_app.py --host 127.0.0.1 --port 7860 --inbrowser
```

若本机开了代理，脚本默认会在本地启动时自动绕过代理。若你想保留代理变量，可加：

```bash
.venv/bin/python scripts/m1_gradio_app.py --host 127.0.0.1 --port 7860 --inbrowser --keep-proxy-env
```

说明：脚本已内置 OpenMP 冲突兼容设置（`KMP_DUPLICATE_LIB_OK=TRUE` + 单线程限制）以避免 `libomp` 重复初始化导致的崩溃。

界面里的关键能力：

- `1) Prepare Assets + Index`：`mock` 模式对应 `m1_01 + m1_02 + m1_03`；`real` 模式从 manifest 建索引
- `2) Prepare Real Latents`：调用 `m2_11` 准备 real latent（默认 `mesh_ref` 模式，不依赖 Blender）
- `3) Run Query Pipeline`：对应 `m1_06`，支持 `decoder=placeholder|shapee`
- `4) Run Street Compose`：M3 街道组合，输出 `scene.glb/scene.ply/scene_layout.json`
- `Model3D`：直接预览导出的 GLB
- `Mesh Downloads`：下载 `GLB/PLY`

`real` 模式注意事项：

- `data/real/real_assets_manifest.jsonl` 不能为空；空文件会被直接拒绝并报错。
- `latent_path` 若写相对路径，会按 `manifest` 所在目录解析。
  例如 manifest 在 `data/real`，`latent_path=latents/bench_01.pt` 会解析为 `data/real/latents/bench_01.pt`。
- 索引文件存在但内容为空时，pipeline 会报 `FAISS index is empty`，需要先重新建索引。

## 7. Shape-E 模式

Shape-E 的环境和模型准备见：

- [shapee_setup.md](/Users/shiqi/Coding/github/GIStudio/RoadGen3D/docs/shapee_setup.md)

无 Blender 推荐路径（`mesh_ref`）：

```bash
.venv/bin/python scripts/m2_11_encode_shapee_latents.py \
  --manifest data/real/real_assets_manifest.jsonl \
  --output-manifest data/real/real_assets_manifest.jsonl \
  --latents-dir data/real/latents
```

说明：`m2_11` 现在默认 `--encode-mode mesh_ref`，会写入 `{"mesh_path": ...}` 形式 latent，不触发 Blender。

命令行示例：

```bash
.venv/bin/python scripts/m1_06_run_pipeline.py \
  --query "a wooden park bench" \
  --topk 1 \
  --assets artifacts/real/real_assets_for_pipeline.jsonl \
  --artifacts artifacts/real \
  --model-dir /Users/shiqi/Coding/github/GIStudio/RoadGen3D/models/clip-vit-base-patch32 \
  --local-files-only \
  --decoder shapee \
  --export-format both
```

## 8. M3 真实街道组合

M3 目标：从 real manifest 做多资产编排，输出场景级文件。

M3 的 manifest 行必须包含：

- `asset_id`
- `category`
- `text_desc`
- `mesh_path`
- `latent_path`

CLI 示例：

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

输出文件：

- `artifacts/real/scene.glb`
- `artifacts/real/scene.ply`
- `artifacts/real/scene_layout.json`
