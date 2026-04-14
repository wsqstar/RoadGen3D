# 数据恢复指南

> 本文档帮助新成员或外部用户获取 RoadGen3D 项目中未提交到 Git 的本地数据。

---

## ⚠️ 为什么这些数据没有提交到 Git？

由于以下原因，部分数据未提交到 Git 仓库：
1. **文件过大**: 2D 纹理 (4.5 GB) 和 AI 模型 (3.3 GB) 超过 GitHub 推荐限制
2. **可重新生成**: 部分数据可通过脚本重新计算
3. **内部资产**: 3D 网格文件为项目内部资产，通过内部渠道分发

---

## 📦 缺失数据清单

| 数据类型 | 路径 | 大小 | 获取方式 |
|:---|:---|:---|:---|
| **3D 网格文件** | `data/real/meshes/*.glb` | 49 MB | 🔒 内部渠道 |
| **2D 俯视纹理** | `assets/` | 4.5 GB | 🔒 内部渠道 |
| **AI 模型权重** | `models/clip-vit-base-patch32/` | 3.3 GB | 🌐 HuggingFace (公开) |
| **知识库索引** | `knowledge/graphRAG/output/` | ~50 MB | 🔄 可重新生成 |

---

## 🌐 公开数据获取

### 1. CLIP 模型权重 (3.3 GB)

这是唯一可以从公开渠道获取的数据，用于资产语义匹配。

```bash
# 方法 1: 使用 HuggingFace CLI
pip install huggingface_hub
huggingface-cli download openai/clip-vit-base-patch32 \
  --local-dir models/clip-vit-base-patch32

# 方法 2: 手动下载
# 访问 https://huggingface.co/openai/clip-vit-base-patch32
# 下载所有文件到 models/clip-vit-base-patch32/ 目录
```

**验证**: 下载完成后，目录应包含以下文件：
```
models/clip-vit-base-patch32/
├── config.json
├── merges.txt
├── pytorch_model.bin          # ~3.3 GB
├── special_tokens_map.json
├── tokenizer_config.json
└── vocab.json
```

---

## 🔄 可重新生成的数据

### 1. GraphRAG 知识库索引 (~50 MB)

如果 `knowledge/graphRAG/output/` 目录缺失，可以重新生成：

```bash
# 1. 确保 PDF 文件存在
ls -lh knowledge/graphRAG/Complete-Streets-Design-Handbook-2024.pdf

# 2. 运行 GraphRAG 索引生成
cd knowledge/graphRAG/graphrag_quickstart
# 参考 graphrag 官方文档重新运行索引构建
```

**预计耗时**: 30-60 分钟（取决于 CPU 性能）

### 2. Sidewalk Area 索引 (~12 MB)

```bash
# 如果 knowledge/sidewalk_area/ 缺失
# 运行 sidewalk 索引生成脚本
python scripts/knowledge/build_sidewalk_index.py
```

---

## 🔒 内部数据获取

以下数据为项目内部资产，**不对外公开**。请联系项目维护者获取访问权限。

### 1. 3D 网格文件 (`data/real/meshes/`)

**内容**: 125 个 GLB 格式的 3D 场景资产
- 树木、路灯、长椅、垃圾桶、公交站等街道家具
- 总大小: 49 MB

**数据来源**:
- **UrbanVerse 数据集**: 主要的 3D 城市场景资产来源
  - 通过 `src/roadgen3d/urbanverse_import.py` 脚本导入
  - 包含高质量的街景对象 (长椅、路灯、垃圾桶等)
- **Objaverse 数据集**: 补充的开源 3D 模型
  - 通过 `src/roadgen3d/objaverse_import.py` 脚本导入
  - 8 个额外资产 (bench, lamp, trash 等)
  - 基于 LVIS 分类和关键词匹配筛选
- **参数化生成**: 程序化生成的资产
  - 101 个资产通过算法生成

**获取方式**: 
- 联系项目维护者获取下载链接
- 或通过内部资产管理系统导出
- 或使用 UrbanVerse/Objaverse 原始数据集重新导入

#### 从 UrbanVerse 重新导入

如果你有 UrbanVerse 数据集的访问权限：

```bash
# 运行 UrbanVerse 导入脚本
python src/roadgen3d/urbanverse_import.py \
  --urbanverse-root /path/to/urbanverse/dataset \
  --output-root data/real
```

#### 从 Objaverse 重新导入

```bash
# 运行 Objaverse 导入脚本
python src/roadgen3d/objaverse_import.py \
  --output-dir data/real/meshes \
  --manifest data/real/objaverse_assets_manifest.jsonl
```

### 2. 2D 俯视纹理 (`assets/`)

**内容**: 231 个 PNG 文件，用于俯视预览图渲染
- 地形瓦片（沥青、人行道、草地等）
- 资产精灵图标
- 道路标线贴图
- 总大小: 4.5 GB

**获取方式**:
- 联系项目维护者获取下载链接
- 或从内部资源服务器同步

### 3. 图模板标注 (`assets/graph_templates/`)

**内容**: 3 套预定义的街道网络拓扑标注
- `hkust_gz_gate/annotation.json`
- `hkust_gz_gate_all/annotation.json`
- `hkust_gz_detailed/annotation.json`

**获取方式**: 通常随内部资产包一起分发

---

## ✅ 验证数据完整性

运行以下脚本检查数据是否完整：

```bash
python scripts/check_data_integrity.py
```

**预期输出**:
```
=== Data Integrity Check ===
✓ CLIP model: models/clip-vit-base-patch32/pytorch_model.bin (3.3 GB)
✓ 3D meshes: data/real/meshes/ (125 files, 49 MB)
✓ CLIP latents: data/real/latents/ (154 files, 616 KB)
✓ Asset manifests: data/real/*.jsonl (4 files)
✓ 2D textures: assets/ (231 files, 4.5 GB)
✓ Graph templates: assets/graph_templates/ (3 sets)
✓ Knowledge base: knowledge/ (82 MB)

All data present and accounted for! ✅
```

---

## 📋 最小运行配置

如果你只想**运行代码**而不需要完整资产，以下是最低要求：

| 数据 | 必需？ | 说明 |
|:---|:---|:---|
| CLIP 模型 | ✅ 必需 | 代码运行依赖 CLIP 特征提取 |
| CLIP 特征向量 | ✅ 已提交 | 已在 Git 中，无需额外下载 |
| 资产清单 JSONL | ✅ 已提交 | 已在 Git 中，包含元数据 |
| 3D 网格文件 | ⚠️ 可选 | 无则无法生成完整 3D 场景 |
| 2D 纹理 | ⚠️ 可选 | 无则无法渲染俯视预览图 |
| 知识库 | ⚠️ 可选 | 无则 RAG 功能降级为默认值 |

**最小启动命令**:
```bash
# 1. 克隆仓库
git clone --recurse-submodules https://github.com/GIStudio/RoadGen3D.git
cd RoadGen3D

# 2. 安装依赖
uv sync
npm --prefix web/workbench install
npm --prefix web/viewer install

# 3. 下载 CLIP 模型
huggingface-cli download openai/clip-vit-base-patch32 \
  --local-dir models/clip-vit-base-patch32

# 4. 启动服务 (部分功能可能受限)
make dev
```

---

## 🆘 常见问题

### Q: 我没有 3D 资产，能运行项目吗？
**A**: 可以启动服务，但生成场景时会使用占位资产或报错。建议联系维护者获取完整资产包。

### Q: CLIP 模型下载太慢怎么办？
**A**: 
- 使用国内镜像站（如 HuggingFace 镜像）
- 或联系维护者获取离线包

### Q: 如何确认我的数据是完整的？
**A**: 运行 `python scripts/check_data_integrity.py` 检查。

### Q: 我可以自己创建 3D 资产吗？
**A**: 可以！项目支持通过 `procedural_generated` 流程创建参数化资产。参考 `src/roadgen3d/parametric_assets.py`。

---

## 📞 联系方式

如需获取内部资产或有任何疑问，请联系：

- **项目维护者**: [联系信息]
- **GitHub Issues**: https://github.com/GIStudio/RoadGen3D/issues
- **内部文档**: [内部 Wiki 链接]

---

*最后更新: 2026-04-13*
