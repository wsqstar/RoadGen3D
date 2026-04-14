# RoadGen3D 系统架构与工作流程

> 本文档描述 RoadGen3D 的整体架构、核心工作流以及各组件之间的关系。

---

## 📋 目录

- [1. 系统总览](#1-系统总览)
- [2. 核心工作流：街道生成的"一条龙"](#2-核心工作流街道生成的一条龙)
- [3. 三大界面组件](#3-三大界面组件)
- [4. Workbench vs Test-Pipeline](#4-workbench-vs-test-pipeline)
- [5. 评估引擎架构](#5-评估引擎架构)
- [6. 完整闭环：从生成到优化](#6-完整闭环从生成到优化)
- [7. 开发者快速开始](#7-开发者快速开始)

---

## 1. 系统总览

RoadGen3D 是一个**AI 驱动的街道场景生成与评估系统**。它能够根据自然语言描述或预设模板，自动生成 3D 街道场景，并对其进行多维度质量评估，甚至支持"一键优化"自动改进设计。

### 架构分层

```
┌─────────────────────────────────────────────────────────────┐
│                     用户交互层 (UI)                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │  Workbench   │  │   Viewer     │  │  Test Pipeline   │  │
│  │  (操作台)     │  │  (3D显示器)  │  │  (自动化脚本)     │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                            ↓ HTTP API
┌─────────────────────────────────────────────────────────────┐
│                     后端服务层 (Python)                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ Design API   │  │ Scene Gen    │  │ Evaluation API   │  │
│  │ (意图理解)    │  │ (场景生成)    │  │ (评估与优化)      │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                            ↓ 调用
┌─────────────────────────────────────────────────────────────┐
│                     核心引擎层 (Core)                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │  LLM Client  │  │ Road-Metrics │  │  Graph RAG       │  │
│  │  (AI大脑)     │  │ (评估引擎)    │  │  (知识库)         │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 核心工作流：街道生成的"一条龙"

无论通过哪种界面触发，底层都遵循以下 **5 步流程**：

### 步骤 1️⃣：意图理解 (Draft)
- **输入**: 用户自然语言（如"步行友好的商业街"）或预设模板
- **处理**: LLM 提取关键参数 → 生成 `compose_config_patch`
- **输出**: 结构化配置（路宽、车道数、密度、需求等级等）

### 步骤 2️⃣：场景生成 (Generation)
- **输入**: 配置参数 + 图模板 (Graph Template)
- **处理**:
  - 布局生成 (Layout Generation)
  - 约束求解 (Constraint Solving)
  - 资产组合 (Asset Composition)
  - 网格生成 (Mesh Generation)
- **输出**: `scene_layout.json` + `scene.glb` (3D 场景文件)

### 步骤 3️⃣：场景评估 (Evaluation)
- **输入**: `scene_layout.json`
- **处理**: **Road-Metrics 评估引擎**计算多维度指标
- **输出**:
  - 步行性指数 (Walkability): 11 项底层指标
  - 安全性评分 (Safety): 结构化 + LLM 增强
  - 美观性评分 (Beauty): 展示质量 + 空间丰富度
  - 综合评分: `0.45×W + 0.35×S + 0.20×B`

### 步骤 4️⃣：诊断与优化 (Diagnosis & Improvement)
- **输入**: 评估结果
- **处理**: 识别短板 → 生成改进建议 (`config_patch`)
- **输出**: 具体参数修改（如 `road_width_m: 13.5 → 14.0`）

### 步骤 5️⃣：闭环迭代 (Loop)
- **操作**: 应用 `config_patch` → **回到步骤 2** 重新生成
- **结果**: 场景质量逐步提升，直到达到满意分数

---

## 3. 三大界面组件

### 🎛️ Workbench (`web/workbench`)
**角色**: 用户操作台

**功能**:
- 选择预设模板 / 自由文本描述
- 生成 3 个备选方案 (A/B/C)
- 方案对比查看
- 评估结果可视化（雷达图、柱状图、详细指标）
- **一键优化**: 应用建议并自动重新生成

**使用场景**: 设计师想要交互式地探索不同街道设计方案。

---

### 🖥️ Viewer (`web/viewer`)
**角色**: 纯 3D 渲染器

**功能**:
- 加载 `scene_layout.json`
- 渲染 3D 街道场景（Three.js）
- 支持鼠标旋转、缩放
- 环境音频播放（根据场景特征生成）

**特点**: 
- 不参与任何业务逻辑
- 可以独立运行，只需传入布局文件路径
- 轻量级，专注于视觉呈现

---

### 🤖 Test Pipeline (`make test-pipeline`)
**角色**: 自动化测试脚本

**功能**:
- 自动启动后端服务
- 批量生成场景（支持 6 种预设模板）
- 自动调用评估接口
- 生成 Markdown 报告（分数、耗时、通过率）
- 检测回归（与历史数据对比）

**使用场景**: 
- 开发者验证代码修改是否引入 bug
- 算法调优时对比不同版本的评估分数
- CI/CD 流水线中的自动化验证

---

## 4. Workbench vs Test-Pipeline

| 维度 | Workbench | Test Pipeline |
|:---|:---|:---|
| **使用者** | 设计师/用户 | 开发者/CI 系统 |
| **触发方式** | 手动点击 UI | 命令行 `make test-pipeline` |
| **交互性** | 高（实时反馈） | 无（全自动） |
| **输出** | 3D 可视化 + 评估面板 | Markdown 报告 + JSON 日志 |
| **目的** | 探索设计方案 | 验证系统健康度 |
| **运行时长** | 按需（通常几分钟） | 固定（约 3-5 分钟/场景） |

### 它们操作的是同一条链条

```
Workbench:  你点按钮 → HTTP API → 生成 → 评估 → 返回 UI 展示
Pipeline:   脚本调用 → HTTP API → 生成 → 评估 → 写入报告文件
```

**本质区别**: Workbench 是**交互式**的，Pipeline 是**批处理式**的。

---

## 5. 评估引擎架构

评估引擎 (`road-metrics`) 是独立于 RoadGen3D 主系统的子模块，采用**分层架构**：

### 分层设计

```
Layer 1: Extractors (数据提取层)
  ↓ 从 scene_layout.json 提取原始数据（不计算）
  
Layer 2: Base Metrics (基础指标层)
  ↓ 每个函数只计算一个根本指标（如充足度、均匀性、密度）
  
Layer 3: Composers (组合层)
  ↓ 将基础指标加权组合为最终分数
  
Layer 4: Engine (引擎层)
  ↓ 编排整个评估流程，输出完整报告
```

### 11 项步行性指标

| 指标 | 含义 | 满分条件 |
|:---|:---|:---|
| SID_CLR | 净空宽度 | ≥3.2m |
| CLEAR_CONT | 净空连续性 | 100% 连续 |
| FURN_D | 家具密度 | 0.15m²/m |
| LIGHT_UNI | 照明均匀度 | CV=0 |
| TREE_SHADE | 绿化遮荫 | 100% 覆盖 |
| BUFFER_RATIO | 缓冲带比例 | 设施带=路宽 |
| TRANSIT_PROX | 交通可达性 | 公交站 0m |
| CROSS_PROV | 过街设施 | 每 80 米 1 个 |
| ENTR_DENS | 入口密度 | 每米 0.04 个 |
| POI_MIX | POI 混合度 | 业态均匀分布 |
| MICRO_ENV | 微环境 | 遮荫 + 隔音 + 开放 |

### 综合评分公式

```
EvaluationScore = 0.45 × WalkabilityIndex 
                + 0.35 × SafetyScore 
                + 0.20 × BeautyScore
```

---

## 6. 完整闭环：从生成到优化

### 用户视角的"一键优化"流程

```
┌─────────────────┐
│ 1. 查看评估结果  │  步行性 88, 安全性 65, 美观性 82
└────────┬────────┘
         ↓
┌─────────────────┐
│ 2. 看到改进建议  │  "增加过街设施密度"、"优化路灯布局"
└────────┬────────┘
         ↓
┌─────────────────┐
│ 3. 查看参数修改  │  road_width_m: 13.5 → 14.0
│                 │  transit_demand_level: medium → high
└────────┬────────┘
         ↓
┌─────────────────┐
│ 4. 点击一键优化  │  [✨ 一键优化] 按钮
└────────┬────────┘
         ↓
┌─────────────────┐
│ 5. 后台自动执行  │  应用 config_patch → 重新生成 → 自动评估
└────────┬────────┘
         ↓
┌─────────────────┐
│ 6. 查看新结果    │  步行性 90, 安全性 78 (+13), 美观性 84
└─────────────────┘
```

### 技术实现

```typescript
// 前端：useGeneration.ts
async function applyAndRegenerate(patch, scheme, schemes) {
  // 1. 合并配置
  const newConfig = { ...baseConfig, ...patch };
  
  // 2. 重新生成场景
  const result = await createSceneJobFromPatch(newConfig);
  
  // 3. 自动评估新场景
  const evalResult = await evaluateScene(result.layoutPath);
  
  // 4. 更新 UI 展示
  updateSchemeWithNewResult(result, evalResult);
}
```

---

## 7. 开发者快速开始

### 环境准备

```bash
# 1. 克隆仓库（包含 submodule）
git clone --recurse-submodules https://github.com/wsqstar/RoadGen3D.git
cd RoadGen3D

# 2. 安装 Python 依赖
uv sync

# 3. 安装前端依赖
npm --prefix web/workbench install
npm --prefix web/viewer install
```

### 启动服务

```bash
# 一键启动所有服务（API + Workbench + Viewer）
make dev
```

访问:
- **Workbench**: http://127.0.0.1:4174
- **Viewer**: http://127.0.0.1:4173
- **API Docs**: http://127.0.0.1:8010/docs

### 运行测试

```bash
# 单元测试
make test

# 完整测试流水线（生成报告）
make test-pipeline

# 查看最新测试报告
make test-report
```

### 独立使用评估引擎

```bash
# 进入评估引擎目录
cd src/roadgen3d/eval_engine_ext

# 运行单元测试
python -m pytest tests/ -v

# 或在主项目中导入使用
python -c "
from roadgen3d.eval_engine_ext import EvalEngine
engine = EvalEngine()
result = engine.evaluate(payload)
print(result.evaluation_score)
"
```

---

## 📚 相关文档

- [评估引擎详细文档](src/roadgen3d/eval_engine_ext/README.md)
- [分层架构设计](src/roadgen3d/eval_engine_ext/LAYERED_ARCHITECTURE.md)
- [迁移指南](src/roadgen3d/eval_engine_ext/MIGRATION_GUIDE.md)
- [评分公式展开](docs/EVALUATION_REPORT.md)

---

*最后更新: 2026-04-13*
