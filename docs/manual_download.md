# 手动下载指南（M1）

本指南用于在网络受限或离线环境下，准备 M1 所需的 CLIP 本地模型。

## 1. 目标模型

- Hugging Face 模型：`openai/clip-vit-base-patch32`

## 2. 本地目录约定

- 目标目录：`/Users/shiqi/Coding/github/GIStudio/RoadGen3D/models/clip-vit-base-patch32`
- 运行脚本时通过 `--model-dir` 指向该目录。

## 3. 推荐下载方式（联网机器执行）

方式 A（推荐，使用 `huggingface-cli`）：

```bash
huggingface-cli download openai/clip-vit-base-patch32 \
  --local-dir /Users/shiqi/Coding/github/GIStudio/RoadGen3D/models/clip-vit-base-patch32
```

方式 B（`git lfs`）：

```bash
git lfs install
git clone https://huggingface.co/openai/clip-vit-base-patch32 \
  /Users/shiqi/Coding/github/GIStudio/RoadGen3D/models/clip-vit-base-patch32
```

下载后可将目录拷贝到目标机器。

## 4. 离线加载方式

所有需要模型的脚本都支持以下参数组合：

- `--model-dir /Users/shiqi/Coding/github/GIStudio/RoadGen3D/models/clip-vit-base-patch32`
- `--local-files-only`

示例：

```bash
python scripts/m1_02_embed_texts.py \
  --assets data/m1/assets.jsonl \
  --out artifacts/m1 \
  --model-dir /Users/shiqi/Coding/github/GIStudio/RoadGen3D/models/clip-vit-base-patch32 \
  --local-files-only
```

若模型目录缺失或文件不完整，脚本会输出清晰错误并以非零状态退出。

## 5. Shape-E 说明（后续阶段）

M1 不要求下载 Shape-E。`src/roadgen3d/decoder.py` 当前为占位解码器，后续可替换为真实 3D 解码器实现。
