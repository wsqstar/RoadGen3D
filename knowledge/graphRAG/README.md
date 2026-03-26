# GraphRAG 项目包

本目录为从本机项目复制出的**可分享版本**：已去掉真实密钥，保留索引与输出（`output/cache/logs` 等），可直接用于 GitHub 上传或备份。

## 目录说明

| 路径 | 说明 |
|------|------|
| `graphrag_quickstart/` | GraphRAG 主工程：配置、笔记本、输入输出与缓存 |
| `graphrag_quickstart/graphrag_api.ipynb` | 主流程与 API 自检（Jupyter） |
| `graphrag_quickstart/settings.yaml` | GraphRAG 配置（模型、`api_base` 等） |
| `graphrag_quickstart/input/` | 文本输入 |
| `graphrag_quickstart/output/` | 索引结果（Parquet、LanceDB 等） |
| `graphrag_quickstart/cache/` | 运行缓存 |
| `graphrag_quickstart/logs/` | 日志 |
| `graphrag_quickstart/api_client.py` | OpenAI 兼容 Chat Completions 客户端 |
| `graphrag_quickstart/.env.example` | 环境变量模板（**复制后改名为 `.env` 再填密钥**） |
| `books_to_graphrag_txt.py` | PDF 转文本等预处理脚本 |
| `graphrag_txt/` | 预处理生成的文本 |
| `Complete-Streets-Design-Handbook-2024.pdf` | 示例 PDF 源文件 |
| `requirements.txt` | 根目录 Python 依赖（如 PyMuPDF） |

> 本包**未包含**虚拟环境目录 `.venv/`，克隆后需自行创建并安装依赖。

## 快速开始

### 1. 创建虚拟环境并安装依赖

```bash
cd graphrag_quickstart   # 或仅在根目录处理 PDF 时用根目录 venv
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -U pip
pip install graphrag python-dotenv neo4j requests jupyter
# 根目录 PDF 处理另需：
pip install -r ../requirements.txt
```

（若你使用固定版本，请按原项目 `pip freeze` 或官方文档补齐。）

### 2. 配置密钥

```bash
cd graphrag_quickstart
cp .env.example .env
# 编辑 .env：填入 GRAPHRAG_API_KEY、GRAPHRAG_API_BASE（可选）、NEO4J_*（若使用 Neo4j 可视化）
```

**切勿**将 `.env` 提交到 Git；仓库已用 `.gitignore` 忽略 `.env`。

### 3. 运行笔记本

```bash
cd graphrag_quickstart
jupyter notebook graphrag_api.ipynb
```

在笔记本中按顺序执行「项目路径」等单元，确保 `load_dotenv` 能加载 `.env`。

## 上传到 GitHub

```bash
cd /Users/yuelabpublic/Desktop/graphRAG_github
git init
git add .
git commit -m "Add GraphRAG project with README"
git branch -M main
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

推送前请确认：`git status` 中**没有** `.env`，且未误提交大文件（若需可改用 Git LFS）。

## 本包在桌面上的位置

默认路径：`/Users/yuelabpublic/Desktop/graphRAG_github`

若 Finder 中找不到，可在访达中按 **Command + Shift + G**，粘贴上述路径并回车。

---

*由本机项目导出，含脱敏配置与说明文档。*
