# RoadGen3D 评估引擎 (EvalEngine)

> **当前状态**: `roadgen3d.eval_engine` 现在是兼容 facade；活跃实现位于
> `roadgen3d.eval_engine_ext.road_metrics`。旧 import 仍可用，但新代码应优先引用
> `eval_engine_ext/road_metrics`。

> **独立的评估子系统**,可独立演进,通过通用参数接口与主系统交互。

## 🎯 设计目标

- ✅ **完全解耦**: 不依赖RoadGen3D内部模块,只依赖标准 `scene_layout.json` 格式
- ✅ **配置驱动**: 所有阈值、权重都可配置,支持独立调优和A/B测试
- ✅ **插件式扩展**: 新指标只需实现接口,自动注册
- ✅ **向后兼容**: 提供迁移层,旧代码无缝过渡
- ✅ **可独立测试**: 可单独运行,不依赖主系统

## 📦 快速开始

### 基础使用

```python
from roadgen3d.eval_engine import EvalEngine, EvalConfig
import json
from pathlib import Path

# 加载场景
payload = json.loads(Path("scene_layout.json").read_text())

# 创建引擎
engine = EvalEngine()

# 评估
result = engine.evaluate(payload)

print(f"步行性指数: {result.walkability.walkability_index}")
print(f"安全评分: {result.safety.final_score}")
print(f"美观评分: {result.beauty.final_score}")
print(f"综合评分: {result.evaluation_score}")
```

### 自定义配置

```python
from roadgen3d.eval_engine import EvalConfig

# 从字典创建配置
config = EvalConfig.from_dict({
    "walkability": {
        "protection_weight": 0.50,  # 默认0.40
        "comfort_weight": 0.30,     # 默认0.35
        "delight_weight": 0.20,     # 默认0.25
    },
    "aggregation": {
        "walkability_weight": 0.50,  # 默认0.45
        "safety_weight": 0.30,       # 默认0.35
        "beauty_weight": 0.20,       # 默认0.20
    },
    "enable_llm_eval": True,
    "enable_audio_profile": False,
})

engine = EvalEngine(config)
result = engine.evaluate(payload)
```

### 迁移层(向后兼容)

```python
# 旧代码
from roadgen3d.eval_quality import compute_walkability_indicators

# 新代码(完全兼容)
from roadgen3d.eval_engine.migration import compute_walkability_indicators

# 用法完全相同!
result = compute_walkability_indicators(payload)
```

## 🏗️ 架构

```
eval_engine/
├── __init__.py                     # 公共API
├── core/
│   ├── engine.py                   # 主评估引擎(编排器)
│   ├── config.py                   # 评估配置(通用参数)
│   └── types.py                    # 通用类型定义
├── metrics/
│   ├── walkability.py              # 步行性(11项指标)
│   ├── safety.py                   # 安全性
│   ├── beauty.py                   # 美观性
│   └── audio.py                    # 声音场景
├── evaluators/                     # (未来扩展)
│   ├── structural.py               # 结构化评估(无LLM)
│   ├── llm_based.py                # LLM增强评估
│   └── comparative.py              # 前后对比
├── utils/
│   └── bbox_utils.py               # 包围盒工具
├── reports/
│   └── writer.py                   # 报告生成
└── migration.py                    # 向后兼容迁移层
```

## 📊 评估指标

### 步行性 (Walkability)

**公式**: `W = 0.40×Protection + 0.35×Comfort + 0.25×Delight`

#### 11项底层指标

| 指标 | 含义 | 满分条件 |
|------|------|----------|
| SID_CLR | 净空宽度 | ≥3.2m |
| CLEAR_CONT | 净空连续性 | 100%连续 |
| FURN_D | 家具密度 | 每米0.15m² |
| LIGHT_UNI | 照明均匀度 | CV=0 |
| TREE_SHADE | 绿化遮荫 | 100%覆盖 |
| BUFFER_RATIO | 缓冲带比例 | 设施带=路宽 |
| TRANSIT_PROX | 交通可达性 | 公交站0m |
| CROSS_PROV | 过街设施 | 每80米1个 |
| ENTR_DENS | 入口密度 | 每米0.04个 |
| POI_MIX | POI混合度 | 业态均匀 |
| MICRO_ENV | 微环境 | 遮荫+隔音+开放 |

### 安全性 (Safety)

**无LLM**: `S = 0.15×CROSS + 0.15×LIGHT + 0.10×BUFFER + 0.10×BOLLARD + 0.10×VISIBILITY`

**有LLM**: `S = 0.60×LLM + 0.15×CROSS + 0.15×LIGHT + 0.10×BUFFER`

### 美观性 (Beauty)

**无LLM**: `B = 0.40×PRESENT + 0.10×FRONT + 0.10×ANCHOR + 0.10×(1-CLUTTER)`

**有LLM**: `B = 0.40×LLM + 0.40×PRESENT + 0.10×FRONT + 0.10×ANCHOR`

### 综合评分

`EvaluationScore = 0.45×W + 0.35×S + 0.20×B`

## 🔧 配置参数

### WalkabilityConfig

```python
WalkabilityConfig(
    protection_weight=0.40,       # 保护性权重
    comfort_weight=0.35,          # 舒适性权重
    delight_weight=0.25,          # 愉悦性权重
    clear_width_min=1.8,          # 最小净空(米)
    clear_width_ideal=3.2,        # 理想净空(米)
    amenity_density_ideal=0.15,   # 理想家具密度(m²/m)
    crossing_spacing_m=80.0,      # 过街设施间距(米)
    entrance_density_ideal=0.04,  # 理想入口密度(个/米)
    transit_decay_m=60.0,         # 交通可达性衰减常数
)
```

### SafetyConfig

```python
SafetyConfig(
    llm_weight=0.60,              # LLM权重
    bollard_density_ideal=0.15,   # 理想护柱密度(个/米)
    llm_stddev_threshold=0.20,    # LLM方差阈值(触发审查)
)
```

### BeautyConfig

```python
BeautyConfig(
    llm_weight=0.40,              # LLM权重
    active_frontage_ratio_ideal=0.70,  # 理想活跃界面比例
    anchor_poi_density_ideal=0.12,     # 理想锚点POI密度
)
```

### AggregationConfig

```python
AggregationConfig(
    walkability_weight=0.45,      # 步行性权重
    safety_weight=0.35,           # 安全性权重
    beauty_weight=0.20,           # 美观性权重
)
```

## 🚀 高级特性

### 真实bbox计算

评估引擎使用真实的 `bbox_xz` 计算:

- **净空宽度**: 根据家具包围盒计算真实通行空间
- **家具密度**: 使用实际占地面积,不是简单计数
- **绿化遮荫**: 使用 `native_size_m.canopy_width_m × scale`

### 诊断输出

```python
result = engine.evaluate(payload)

# 安全诊断
print(result.safety.diagnosis)
# {'weakest': 'CROSS_PROV', 'score': 0.0, 'all_scores': [...]}

# 美观诊断
print(result.beauty.diagnosis)
# {'weakest': 'active_front_ratio', 'score': 0.0, 'all_scores': [...]}
```

### 报告生成

```python
from roadgen3d.eval_engine.reports.writer import (
    write_evaluation_report,
    write_comparison_report,
)

# 写入评估报告
write_evaluation_report(result, Path("evaluation.json"))

# 对比两次迭代
write_comparison_report(current, previous, Path("comparison.json"))
```

## 🔄 从eval_quality.py迁移

### 方案1: 直接替换(推荐)

```python
# 旧
from roadgen3d.eval_quality import compute_walkability_indicators

# 新
from roadgen3d.eval_engine.migration import compute_walkability_indicators
```

### 方案2: 渐进迁移

```python
# 新代码使用EvalEngine
from roadgen3d.eval_engine import EvalEngine

engine = EvalEngine()
result = engine.evaluate(payload)

# 旧代码保持不变
from roadgen3d.eval_quality import compute_walkability_indicators
```

## 📈 独立进化

评估引擎可以独立演进:

1. **添加新指标**: 在 `metrics/` 下新增模块
2. **调整参数**: 修改 `EvalConfig` 无需改代码
3. **A/B测试**: 创建不同配置对比效果
4. **独立部署**: 可作为独立服务运行

## 🧪 测试

```bash
# 运行评估引擎测试
uv run pytest tests/test_eval_engine.py -v

# 对比新旧结果
uv run python scripts/compare_eval_engines.py
```

## 📝 示例

完整示例见 `examples/eval_engine_demo.py`
