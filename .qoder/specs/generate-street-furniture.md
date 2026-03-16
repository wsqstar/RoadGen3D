# 街道家具自动生成质量限制分析

## 上下文

用户询问为什么当前的 RoadGen3D 系统无法自动生成高质量的街道家具。通过代码库探索，我们发现了以下核心限制因素。

---

## 核心问题分析

### 1. 参数化生成覆盖范围有限

**当前状态**：
- 只有 **bench（长椅）** 和 **lamp（路灯）** 支持参数化生成
- 其他 6 个类别（trash, tree, bus_stop, mailbox, hydrant, bollard）依赖程序化生成

**关键代码位置**：[parametric_assets.py](src/roadgen3d/parametric_assets.py)

```python
# 只有这两种类型有完整的参数化支持
_GENERATORS = {
    "bench": generate_bench,
    "lamp": generate_lamp,
}
```

### 2. 质量与面数的矛盾

**面数阈值系统** ([m3_04_clean_asset_manifest.py](scripts/m3_04_clean_asset_manifest.py))：

| 类别 | Tier 0 | Tier 1 | Tier 2 | Tier 3 |
|------|--------|--------|--------|--------|
| tree | 120 | 350 | 1000 | >1000 |
| lamp | 100 | 300 | 1100 | >1100 |
| bench | 100 | 280 | 900 | >900 |
| bus_stop | 180 | 600 | 1600 | >1600 |

**问题**：
- 面数过低 → 细节不足，质量差
- 面数过高 → 渲染性能问题，内存占用大
- 自动生成难以在两者间找到最佳平衡

### 3. Preview vs Production 的质量差距

```python
_POLY_BUDGET_K = {
    "bench": {"preview": 8, "production": 15},
    "lamp": {"preview": 10, "production": 20}
}
```

**问题**：
- Preview 模式面数预算低 50%，用于快速迭代
- Production 模式需要重新生成，无法自动升级
- Preview 资产在 production 版本存在时会被降级

### 4. 缺乏自动化的美学评估

**当前质量评估仅基于**：
- 面数 (mesh_face_count)
- 质量层级 (quality_tier)
- 场景可用性 (scene_eligible)

**缺失的评估维度**：
- 几何拓扑质量（非流形边、法线一致性）
- 纹理/材质质量
- 语义正确性（比例、功能合理性）
- 视觉美观度

### 5. 风格一致性难以保证

**系统支持 15 种风格**：
```python
_STYLE_TAGS = {"modern", "classic", "industrial", "minimalist", "ornate",
               "retro", "modular", "eco", "brutalist", "nordic",
               "japan_scandi", "victorian", "contemporary", "tactical", "art_deco"}
```

**问题**：
- 风格标签是手动指定的
- 生成器难以确保生成的几何形状真正符合风格描述
- 不同资产之间的风格一致性需要人工审核

### 6. 程序化生成的固有限制

**[m3_02_generate_procedural_assets.py](scripts/m3_02_generate_procedural_assets.py)** 使用预定义变体：

```python
def _bench_variant(i):
    # 15种固定变体，通过参数组合产生
    variants = [
        {"leg_style": "dual_frame", "has_armrest": False},
        {"leg_style": "pedestal", "has_armrest": True},
        # ...
    ]
    return variants[i % len(variants)]
```

**限制**：
- 变体组合是有限的
- 难以产生真正新颖的设计
- 依赖人工设计的参数空间

---

## 技术债务

1. **无自动质量验证循环**
   - 生成后需要手动运行 `m3_04_clean_asset_manifest.py` 来评估质量
   - 没有自动重新生成低质量资产的机制

2. **latent 空间质量依赖外部模型**
   - 使用 Shape-E 进行 latent 编码
   - 生成质量受限于预训练模型的能力

3. **FAISS 检索的语义鸿沟**
   - 文本描述 → CLIP 嵌入 → 资产检索
   - 文本描述的质量直接影响检索结果

---

## 可能的改进方向

| 问题 | 潜在解决方案 |
|------|-------------|
| 参数化覆盖有限 | 扩展参数化生成器到其他类别 |
| 美学评估缺失 | 引入学习型质量评估器 |
| 风格一致性 | 使用风格条件生成模型 |
| 自动化程度低 | 构建 quality-aware 生成循环 |
| 面数平衡 | 自适应 LOD 生成系统 |

---

## 总结

系统当前**不能**自动生成高质量街道家具的核心原因是：

1. **技术覆盖不足**：只有 2/8 类别支持参数化生成
2. **评估维度单一**：仅基于面数评估，缺乏几何和美学评估
3. **自动化流程断裂**：需要多个手动步骤（生成→评估→筛选→重新生成）
4. **质量标准模糊**："高质量"的定义在不同场景下不同，难以量化

要实现真正的自动化高质量生成，需要：
- 完善参数化生成器的覆盖范围
- 引入自动化的多维度质量评估系统
- 构建闭环的质量反馈机制
