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

## 1. 安装依赖

```bash
.venv/bin/python -m pip install -r requirements-m1.txt
```

或使用 `uv`：

```bash
/Users/shiqi/.local/bin/uv pip install --python .venv/bin/python -r requirements-m1.txt
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

## 4. 产物检查

- 环境报告：`artifacts/m1/env_report.json`
- 嵌入矩阵：`artifacts/m1/asset_text_embeds.npy`
- 检索索引：`artifacts/m1/index_ip.faiss`
- 检索结果：`artifacts/m1/last_retrieval.json`
- 体素结果：`artifacts/m1/voxel_prob.npy`, `artifacts/m1/voxel_bin.npy`
- 闭环结果：`artifacts/m1/pipeline_result.json`

## 5. 测试

```bash
.venv/bin/python -m pytest -q
```

说明：测试优先验证工程闭环和错误处理，默认不依赖在线模型下载。
