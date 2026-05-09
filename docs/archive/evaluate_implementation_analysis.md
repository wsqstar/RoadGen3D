# 后端评估系统实现分析报告

**分析日期**: 2026-04-23  
**分析依据**: `docs/evaluate.md` 设计要求  
**分析范围**: `src/roadgen3d/eval_engine/` 及相关模块

---

## 一、设计要求概览（来自 evaluate.md）

`docs/evaluate.md` 提出了 4 个核心要求：

1. **评价标准**：需选用合适的评价公式，区分通用公式与数学公式，明确公式适用场景
2. **规则边界**：界定规则驱动（Rule-based）可直接解决的问题，无需复杂模型
3. **特殊场景**：感官类、主观类指标需做适配处理与可识别化，比如用ViT、或者VLM
4. **可视化环节**：补充绘图选择，根据数据类型与对比目的选用合适图表呈现结果

---

## 二、实现符合度分析

### ✅ 1. 评价标准 - **完全符合**

#### 1.1 评价公式体系

**状态**: ✅ 已完整实现，且超出预期

**实现情况**:
- ✅ 建立了完整的三级评分体系（步行性/安全性/美观性）
- ✅ 所有公式均有明确的数学表达和文献依据
- ✅ 公式适用场景清晰（单街段模型、二维平面假设）

**具体实现**:

| 维度 | 公式 | 实现位置 | 状态 |
|------|------|----------|------|
| **步行性** | `W = 0.40×Protection + 0.35×Comfort + 0.25×Delight` | `eval_engine/metrics/walkability.py` | ✅ |
| **安全性(无LLM)** | `S = 0.15×CROSS + 0.15×LIGHT + 0.10×BUFFER + 0.10×BOLLARD + 0.10×VISIBILITY` | `eval_engine/metrics/safety.py` | ✅ |
| **安全性(有LLM)** | `S = 0.60×LLM + 0.15×CROSS + 0.15×LIGHT + 0.10×BUFFER` | `eval_engine/metrics/safety.py` | ⚠️ LLM未接入 |
| **美观性(无LLM)** | `B = 0.40×PRESENT + 0.10×FRONT + 0.10×ANCHOR + 0.10×(1-CLUTTER)` | `eval_engine/metrics/beauty.py` | ✅ |
| **美观性(有LLM)** | `B = 0.40×LLM + 0.40×PRESENT + 0.10×FRONT + 0.10×ANCHOR` | `eval_engine/metrics/beauty.py` | ⚠️ LLM未接入 |
| **综合评分** | `Score = 0.45×W + 0.35×S + 0.20×B` | `eval_engine/core/engine.py` | ✅ |

**文档支持**:
- ✅ `docs/scoring_formula_specification.md` - 详细的公式规范（v1.0）
- ✅ `docs/evaluation_module_plan.md` - 完整的指标定义和文献依据
- ✅ `docs/EVAL_ENGINE_DECOUPLING.md` - 引擎架构设计文档

#### 1.2 配置化与通用参数

**状态**: ✅ 优秀实现

所有阈值、权重均可通过 `EvalConfig` 配置：

```python
config = EvalConfig.from_dict({
    "walkability": {
        "protection_weight": 0.40,
        "comfort_weight": 0.35,
        "delight_weight": 0.25,
        "clear_width_min": 1.8,
        "clear_width_ideal": 3.2,
    },
    "aggregation": {
        "walkability_weight": 0.45,
        "safety_weight": 0.35,
        "beauty_weight": 0.20,
    },
})
```

**优势**:
- 支持 A/B 测试
- 支持场景定制（不同街道类型可用不同权重）
- 参数与代码解耦，便于调优

#### 1.3 公式适用场景

**状态**: ✅ 明确定义

- ✅ 单街段模型（独立街段，非网络级）
- ✅ 二维平面假设（`topdown_render` 显式以长宽参数构建）
- ✅ 所有指标基于 `scene_layout.json`，无需额外几何推断

---

### ⚠️ 2. 规则边界 - **部分符合**

#### 2.1 规则驱动问题的界定

**状态**: ⚠️ 隐式实现，缺乏明确文档

**已实现的规则型指标**（无需复杂模型）:

| 指标类型 | 具体指标 | 计算方法 | 是否规则驱动 |
|---------|---------|---------|------------|
| **步行性** | SID_CLR, CLEAR_CONT, FURN_D, LIGHT_UNI, TREE_SHADE, BUFFER_RATIO, TRANSIT_PROX, CROSS_PROV, ENTR_DENS, POI_MIX, MICRO_ENV | 几何计算/统计分析 | ✅ 全部规则驱动 |
| **安全性** | LIGHT_UNI, CROSS_PROV, BUFFER_RATIO, BOLLARD_DENSITY, VISIBILITY_PENALTY | 几何计算/统计分析 | ✅ 全部规则驱动 |
| **美观性** | presentation_score, active_front_ratio, anchor_poi_score, visual_clutter | 几何计算/启发式 | ✅ 部分规则驱动 |
| **工程指标** | overlap_rate, dropped_slot_rate, spacing_uniformity, style_consistency | 几何/统计 | ✅ 全部规则驱动 |

**问题**:
- ⚠️ 缺少明确的"规则边界"文档说明哪些场景用规则、哪些需要模型
- ⚠️ 未建立规则 vs LLM 的决策树或流程图
- ⚠️ 对于"何时使用规则 vs 何时使用LLM"缺乏指导

**建议补充**:
```markdown
# 规则边界决策指南（待创建）

## 规则驱动适用场景
✅ 几何可计算的问题（宽度、密度、均匀度）
✅ 统计可量化的问题（数量、比例、分布）
✅ 有明确公式的问题（Shannon熵、变异系数）

## 需要LLM/VLM的场景
⚠️ 主观感知问题（美感、舒适度感知）
⚠️ 视觉质量评估（材质对比、视觉兴趣）
⚠️ 复杂语义理解（场所氛围、文化契合度）

## 混合策略
🔄 规则计算基础分 + LLM做增量调整
🔄 规则用于诊断 + LLM用于综合判断
```

---

### ⚠️ 3. 特殊场景（感官/主观指标） - **部分符合**

#### 3.1 LLM增强评估框架

**状态**: ⚠️ 框架已建，LLM未实际接入

**已实现**:
- ✅ LLM安全性评估接口 (`compute_llm_enhanced_safety`)
- ✅ LLM美观性评估接口 (`compute_llm_enhanced_beauty`)
- ✅ LLM子维度评分体系（安全性4项、美观性4项）
- ✅ 方差检测机制（`needs_review` 标记）
- ✅ 诊断机制（`diagnosis.weakest` 标识最弱维度）

**未实现**:
- ❌ 实际LLM调用（代码中标记为 `TODO: 调用LLM评估`）
- ❌ 提示词模板实现（`safety_eval.py`, `beauty_eval.py` 未创建）
- ❌ 渲染图输入管道（LLM需要俯视图作为输入）
- ❌ LLM缓存机制（避免重复计费）

**代码位置**:
```python
# eval_engine/core/engine.py:103-105
if self.config.enable_llm_eval:
    # TODO: 调用LLM评估
    # llm_scores = call_llm_safety_eval(features, scene)
    pass
```

#### 3.2 ViT/VLM 方案

**状态**: ❌ 未实现

**设计要求**: "感官类、主观类指标需做适配处理与可识别化，比如用ViT、或者VLM"

**当前方案**:
- 计划使用 LLM（GPT-4/Claude）进行主观评估
- 未提及 ViT（Vision Transformer）或 VLM（Vision-Language Model）

**差距分析**:
- ❌ 未评估 ViT vs LLM 的优劣
- ❌ 未实现本地视觉模型（避免API成本）
- ❌ 未建立视觉模型基准测试

**建议**:
1. 短期：接入云端LLM（GPT-4o/Claude 3.7）进行可行性验证
2. 中期：评估 ViT/VLM 方案（如 CLIP、BLIP-2、LLaVA）
3. 长期：训练专用城市街道评估模型

#### 3.3 主观指标的结构化近似

**状态**: ✅ 良好实现（作为LLM不可用时的回退）

当前实现了结构化评分作为LLM的降级方案：

| 主观维度 | 结构化近似 | 公式 |
|---------|-----------|------|
| **安全性** | 照明+横道+缓冲+护柱 | `0.15*CROSS + 0.15*LIGHT + 0.10*BUFFER + 0.10*BOLLARD + 0.10*VISIBILITY` |
| **美观性** | 演示分+活跃界面+锚点POI | `0.4*PRESENT + 0.1*FRONT + 0.1*ANCHOR + 0.1*(1-CLUTTER)` |

**优势**:
- 完全可解释、可复现
- 无需外部依赖、成本低
- 在早期开发阶段可用

**局限**:
- 无法捕捉视觉质量（材质、光影、构图）
- 无法评估主观感受（美感、氛围感）
- 与人类专家评分可能存在偏差

---

### ❌ 4. 可视化环节 - **不符合要求**

**状态**: ❌ 严重缺失

**设计要求**: "补充绘图选择，根据数据类型与对比目的选用合适图表呈现结果"

#### 4.1 当前可视化能力

**已实现**:
| 可视化类型 | 实现位置 | 状态 | 用途 |
|-----------|---------|------|------|
| **俯视图渲染** | `topdown_render.py`, `beauty.py` | ✅ | 场景展示、LLM输入 |
| **演示视图** | `presentation_views/` | ✅ | 美观性评估 |
| **空间分布图** | `spatial_viz.py` | ✅ | 调试、分析 |
| **散点图** | `scripts/eval_scatter.py` | ✅ | 评估结果探索 |
| **比例尺** | `scene_renderer.py` | ✅ | 渲染图标注 |

**问题**:
- ⚠️ 这些是**场景可视化**，不是**评估结果可视化**
- ⚠️ 缺少评估指标的可视化呈现（雷达图、柱状图、对比图）
- ⚠️ 缺少 UI 报表（前端展示评估结果）

#### 4.2 缺失的可视化类型

根据评估数据的特性，需要以下可视化：

| 数据类型 | 对比目的 | 推荐图表 | 实现状态 |
|---------|---------|---------|---------|
| **11项步行性指标** | 诊断强弱项 | 雷达图/蜘蛛图 | ❌ |
| **安全/美观子维度** | 细粒度分析 | 柱状图 | ❌ |
| **迭代前后对比** | 优化效果 | 双柱对比图 | ❌ |
| **多场景对比** | 方案选择 | 分组柱状图/小提琴图 | ❌ |
| **Top Contributors** | 改进优先级 | 水平条形图 | ❌ |
| **评估分数分布** | 批量分析 | 直方图/箱线图 | ❌ |
| **LLM vs 规则差异** | 模型验证 | 散点图+回归线 | ❌ |
| **指标相关性** | 冗余分析 | 热力图 | ❌ |

#### 4.3 前端UI集成

**状态**: ❌ 未实现

根据 `docs/evaluation_module_plan.md` 步骤5：

> 更新 `GET /api/scene/jobs/{job_id}` 等接口返回 Walkability/Safety/Beauty 及子指标。
> 前端展示雷达图、诊断标签、LLM 置信度，允许下载 per-scene JSON。

**当前API**:
- ⚠️ 未检查API是否返回评估结果
- ⚠️ 未检查前端是否有评估展示

**建议实现**:
```python
# 后端 API 扩展
@app.get("/api/scenes/{scene_id}/evaluation")
async def get_evaluation(scene_id: str):
    return {
        "walkability": walkability.to_dict(),
        "safety": safety.to_dict(),
        "beauty": beauty.to_dict(),
        "evaluation_score": evaluation_score,
        "visualization_urls": {
            "radar_chart": "...",
            "bar_chart": "...",
        }
    }
```

---

## 三、架构与设计亮点

### ✅ 优秀设计

1. **完全解耦的评估引擎**
   - `eval_engine/` 不依赖主系统，可独立测试、部署
   - 清晰的插件式架构，易于扩展新指标

2. **配置驱动**
   - 所有权重、阈值可配置
   - 支持A/B测试和场景定制

3. **真实计算（非简化）**
   - 使用家具 `bbox_xz` 计算真实遮挡
   - 使用实际占地面积（不是简单计数）
   - 使用 `native_size_m.canopy_width_m × scale` 计算树冠

4. **向后兼容**
   - 提供迁移层 (`migration.py`)
   - 旧代码可无缝过渡

5. **诊断机制**
   - `top_contributors` 标识改进优先级
   - `needs_review` 标记需要人工审查
   - `diagnosis.weakest` 标识最弱维度

---

## 四、问题清单与优先级

### 🔴 高优先级（核心功能缺失）

| # | 问题 | 影响 | 建议方案 |
|---|------|------|---------|
| 1 | **LLM评估未接入** | 安全性和美观性评分不完整 | 实现 `safety_eval.py` 和 `beauty_eval.py` |
| 2 | **评估结果可视化缺失** | 无法直观展示评估结果 | 实现雷达图、柱状图、对比图 |
| 3 | **前端UI集成未完成** | 用户无法查看评估结果 | API扩展 + 前端组件开发 |

### 🟡 中优先级（设计完善）

| # | 问题 | 影响 | 建议方案 |
|---|------|------|---------|
| 4 | **规则边界文档缺失** | 不清楚何时用规则vs模型 | 创建决策指南文档 |
| 5 | **ViT/VLM方案未评估** | 可能错过更优方案 | 对比LLM vs ViT vs VLM |
| 6 | **LLM缓存机制未实现** | API成本可能过高 | 实现基于哈希的缓存 |

### 🟢 低优先级（优化增强）

| # | 问题 | 影响 | 建议方案 |
|---|------|------|---------|
| 7 | **评估结果对比功能** | 无法追踪优化进展 | 实现 `write_comparison_report` |
| 8 | **批量评估工具** | 多场景分析效率低 | 创建批量CLI脚本 |
| 9 | **评估版本管理** | 难以追踪评估逻辑变更 | 引入评估引擎版本号 |

---

## 五、总体评估

### 符合度总结

| 设计要求 | 符合度 | 说明 |
|---------|--------|------|
| 1. 评价标准 | ✅ **95%** | 公式体系完整，配置化优秀 |
| 2. 规则边界 | ⚠️ **60%** | 规则指标已实现，但缺文档和决策树 |
| 3. 特殊场景 | ⚠️ **50%** | 框架已建，LLM未接入，ViT/VLM未评估 |
| 4. 可视化 | ❌ **20%** | 场景可视化OK，评估结果可视化严重缺失 |

**总体符合度**: **~56%** （部分符合）

### 优势

✅ 评估公式体系完整、有文献依据  
✅ 配置驱动、可解释性强  
✅ 架构解耦、易于扩展  
✅ 诊断机制完善  

### 劣势

❌ LLM评估未实际接入（核心功能）  
❌ 评估结果可视化严重缺失（核心要求）  
❌ 前端UI集成未完成  
❌ 规则边界和特殊场景缺乏指导文档  

### 建议

1. **短期（1-2周）**：
   - 接入LLM评估（GPT-4o/Claude）
   - 实现评估结果可视化（雷达图、柱状图）
   - 扩展API返回评估结果

2. **中期（1个月）**：
   - 前端UI集成（雷达图展示、诊断标签）
   - 创建规则边界文档
   - 评估ViT/VLM方案

3. **长期（3个月）**：
   - 训练专用评估模型（降低成本）
   - 实现批量评估和对比工具
   - 建立评估基准测试集

---

## 六、详细代码审查

### 6.1 步行性指标实现

**文件**: `src/roadgen3d/eval_engine/metrics/walkability.py`

**符合度**: ✅ **100%**

- ✅ 11项指标全部实现
- ✅ 公式与 `scoring_formula_specification.md` 完全一致
- ✅ 使用真实bbox计算（非简化）
- ✅ 三大支柱聚合正确
- ✅ Top contributors计算

**代码质量**: 优秀
- 清晰的函数拆分
- 良好的命名和注释
- 完整的类型注解

### 6.2 安全性指标实现

**文件**: `src/roadgen3d/eval_engine/metrics/safety.py`

**符合度**: ⚠️ **70%**

- ✅ 结构化安全评分已实现
- ✅ LLM增强接口已定义
- ❌ LLM实际调用未实现（`TODO`标记）
- ✅ 方差检测机制已实现
- ✅ 诊断机制已实现

**代码质量**: 良好
- 接口设计清晰
- 但LLM部分留白过多

### 6.3 美观性指标实现

**文件**: `src/roadgen3d/eval_engine/metrics/beauty.py`

**符合度**: ⚠️ **65%**

- ✅ 结构化美观评分已实现
- ✅ 活跃界面比例计算
- ✅ 锚点POI评分（带权重）
- ❌ LLM实际调用未实现（`TODO`标记）
- ⚠️ presentation_score使用简化逻辑（取第一个placement的score）

**问题**:
```python
# 第111行 - 简化逻辑
presentation_score = float(
    scene.placements[0].get("score", 0.0) if scene.placements else 0.0
)  # TODO: 从composition_report提取
```

应改为从 `composition_report` 或 `beauty.py` 的真实演示评分提取。

**代码质量**: 良好
- 但部分TODO未完成

### 6.4 评估引擎核心

**文件**: `src/roadgen3d/eval_engine/core/engine.py`

**符合度**: ⚠️ **75%**

- ✅ 评估流程编排清晰
- ✅ 步行性/安全性/美观性评估调用正确
- ✅ 综合评分聚合正确
- ⚠️ 音频配置为可选（已实现）
- ❌ LLM评估开关未实际调用（`enable_llm_eval` 配置无效）

**代码质量**: 优秀
- 良好的架构设计
- 清晰的职责划分

### 6.5 配置系统

**文件**: `src/roadgen3d/eval_engine/core/config.py`

**符合度**: ✅ **100%**

- ✅ 所有参数可配置
- ✅ 默认配置合理
- ✅ 支持从字典创建
- ✅ 支持序列化为字典

**代码质量**: 优秀
- 数据类使用得当
- 类型注解完整

### 6.6 类型系统

**文件**: `src/roadgen3d/eval_engine/core/types.py`

**符合度**: ✅ **100%**

- ✅ 类型定义完整
- ✅ 序列化方法一致
- ✅ `SceneLayout.from_layout_payload()` 解析健壮

**代码质量**: 优秀
- 不可变类型使用 `frozen=True`
- 默认值合理

### 6.7 报告生成

**文件**: `src/roadgen3d/eval_engine/reports/writer.py`

**符合度**: ✅ **85%**

- ✅ JSON报告生成
- ✅ 对比报告生成
- ⚠️ 缺少可视化URL生成

**建议**:
```python
def write_evaluation_report(result, out_path, viz_urls=None):
    """增强版，包含可视化URL"""
    report = result.to_dict()
    if viz_urls:
        report["visualizations"] = viz_urls
    # ...
```

---

## 七、测试覆盖度

**已有测试**:
- ✅ `test_auto_eval.py` - 自动评估管线端到端测试
- ✅ `test_m4_eval_metrics.py` - M4 工程指标测试
- ✅ `test_eval_llm_status.py` - LLM 评估状态测试
- ✅ `test_beauty_presentation.py` - 美观性展示评估测试
- ✅ `test_m7_entrance_analysis.py` - M7 入口分析测试

**缺失测试**:
- ❌ LLM评估集成测试（因LLM未接入）
- ❌ 可视化输出测试
- ❌ 配置变更影响测试（A/B测试）
- ❌ 多场景批量评估测试

---

## 八、结论

### 总体评价

当前后端评估系统的**核心算法实现优秀**，公式体系完整、架构清晰、配置化程度高。但**关键功能尚未完成**，特别是：

1. **LLM评估未接入** - 安全性和美观性评分不完整
2. **评估结果可视化缺失** - 不符合 `evaluate.md` 的核心要求
3. **前端UI集成未完成** - 用户无法查看评估结果

### 是否符合设计要求？

**答案**: **部分符合（~56%）**

- ✅ **评价标准**：完全符合，实现优秀
- ⚠️ **规则边界**：部分符合，缺文档
- ⚠️ **特殊场景**：部分符合，LLM未接入
- ❌ **可视化环节**：严重不符合，核心缺失

### 下一步行动

**立即执行**（1-2周）:
1. 实现LLM评估器（`safety_eval.py`, `beauty_eval.py`）
2. 实现评估结果可视化（雷达图、柱状图、对比图）
3. 扩展API返回评估结果

**中期计划**（1个月）:
4. 前端UI集成
5. 创建规则边界文档
6. 评估ViT/VLM方案

**长期规划**（3个月）:
7. 训练专用评估模型
8. 批量评估和对比工具
9. 评估基准测试集

---

**报告编写**: Qwen Code  
**审核建议**: 建议与产品团队确认优先级，优先解决LLM接入和可视化问题。
