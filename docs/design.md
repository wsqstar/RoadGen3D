# RoadGen3D 参数化生成近期设计

更新时间：2026-03-11

## 背景与目标

RoadGen3D 近期阶段的核心目标，不是依赖复杂模型去做不可控的任意生成，而是建立一条以物理合理、设计规范约束、参数显式可控为第一原则的参数化生成链路。

空间组织与街道设施布置以当前实现能力为标准，不再预设固定的沿路用地切分方案或特定设施布置模式。

本阶段只承诺打通 `Bench` 与 `Lamp` 两个地物的高质量参数化生成流程，不追求一次覆盖全部街道资产。选择这两个地物的原因是：

- 它们已经有现成的程序化几何基础和质量门槛，可作为最小可落地样板，见 [scripts/m3_02_generate_procedural_assets.py](../scripts/m3_02_generate_procedural_assets.py)。
- `Bench` 代表低高度、承重、接地稳定类地物。
- `Lamp` 代表高细长、立柱、净空与布置规范类地物。

本阶段设计目标聚焦两件事：

1. 生成质量提高：以 `Bench + Lamp` 为样板，完成“参数输入 -> 规则校验 -> 几何组装 -> 质量验收 -> 导出登记”的完整闭环。
2. 生成速度提高：优先减少无效重试，并建立 Apple Silicon 优先、可替换为 CUDA 的统一设备策略。

## 设计原则

### 1. 物理合理优先

- 地物必须满足地面接触、支撑完整、基本稳定性可解释。
- 不允许明显悬空、穿插、结构断裂或支撑数量不足的结果通过默认质量门槛。

### 2. 设计规范优先

- 关键尺寸和布置必须有默认安全值、推荐范围和超界处理策略。
- 参数不是“随便调”，而是要在可解释规范空间内调节。

### 3. 参数优先

- 所有核心几何、风格和质量相关变量都必须显式暴露。
- 默认路线是参数化生成优先、轻模型辅助，不依赖黑箱模型决定最终几何。

### 4. 设备可移植

- Apple Silicon 是近期默认优化目标。
- 设备抽象必须同时保留 `mps`、`cuda`、`cpu` 三类后端，且参数语义不能因后端变化而改变。
- 当前仓库已经暴露 `cpu / mps / cuda` 设备输入面，但仍缺少统一 `auto` 策略，见 [scripts/m1_gradio_app.py](../scripts/m1_gradio_app.py#L2975)、[src/roadgen3d/embedder.py](../src/roadgen3d/embedder.py)、[src/roadgen3d/street_layout.py](../src/roadgen3d/street_layout.py#L2207)。

## 近期范围（Bench + Lamp）

### 本期纳入

- `Bench`
- `Lamp`

### 本期不纳入完成定义

- `Tree`
- `Bus Stop`
- `Building`

这些地物只作为后续扩展方向，不进入本期验收范围。

## 参数化生成总流程

本阶段统一采用如下生成流程：

1. 参数输入  
   接收 `GenerationRequest`，解析公共运行参数与地物专属参数。
2. 规则校验  
   对尺寸、比例、稳定性、规范约束做 `clamp / reject / warn` 处理。
3. 几何组装  
   通过程序化部件拼装生成原始 mesh，不以黑箱生成模型为主路径。
4. 尺度拟合与落地  
   将几何拟合到目标包围盒，并保证地物正确落地。
5. 质量检查  
   检查面数、预算、结构完整性、尺寸误差、参数可响应性。
6. 导出与登记  
   输出 mesh、bbox、参数快照、质量指标，并登记到 manifest 或缓存产物中。

## Bench 生成流程

### 输入参数

`BenchParams` 至少包含以下字段：

| 字段 | 默认值 | 推荐范围 | 超界策略 |
| --- | --- | --- | --- |
| `width_m` | `1.80` | `1.20 - 2.40` | `clamp` |
| `depth_m` | `0.55` | `0.40 - 0.75` | `clamp` |
| `seat_height_m` | `0.45` | `0.38 - 0.50` | `clamp` |
| `backrest_height_m` | `0.35` | `0.20 - 0.55` | `clamp` |
| `backrest_angle_deg` | `12` | `5 - 20` | `clamp` |
| `leg_type` | `"dual_frame"` | `"dual_frame" | "pedestal" | "four_leg"` | `reject` |
| `armrest_enabled` | `false` | `true / false` | `reject` |
| `slat_count` | `5` | `3 - 8` | `clamp` |
| `material_family` | `"metal_wood"` | `"metal" | "wood" | "metal_wood" | "concrete"` | `reject` |
| `style_tag` | `"modern"` | 当前 style vocab | `warn` 后回退默认 |
| `detail_level` | `2` | `0 - 3` | `clamp` |

### 规则约束

- 座高必须保持在可坐姿范围内。
- 座深不能过浅或过深，避免“看似可坐、实际不可坐”。
- 靠背角必须落在可接受后倾范围内。
- 支撑跨度不能导致中部明显悬挑。
- 至少满足最小接地支撑数量：
  - `dual_frame`: 至少 2 组有效支撑
  - `four_leg`: 至少 4 个接地点
  - `pedestal`: 底座直径必须满足稳定性要求
- 重心投影必须落在有效支撑多边形内；如无法严格计算，至少做近似稳定性检查。

### 输出

`Bench` 生成结果至少输出：

- `mesh`
- `bbox`
- `parameter_snapshot`
- `quality_metrics`
  - `face_count`
  - `poly_budget_k`
  - `dimension_error_ratio`
  - `ground_contact_ok`
  - `support_count`
  - `stability_check_ok`

## Lamp 生成流程

### 输入参数

`LampParams` 至少包含以下字段：

| 字段 | 默认值 | 推荐范围 | 超界策略 |
| --- | --- | --- | --- |
| `pole_height_m` | `5.00` | `3.50 - 8.00` | `clamp` |
| `pole_radius_m` | `0.06` | `0.04 - 0.12` | `clamp` |
| `base_diameter_m` | `0.35` | `0.25 - 0.60` | `clamp` |
| `arm_length_m` | `0.80` | `0.40 - 1.80` | `clamp` |
| `luminaire_type` | `"flat_led"` | `"flat_led" | "globe" | "box" | "cone"` | `reject` |
| `single_or_double_arm` | `"single"` | `"single" | "double"` | `reject` |
| `light_direction` | `"roadside"` | `"roadside" | "bidirectional" | "downward"` | `reject` |
| `material_family` | `"metal"` | `"metal" | "painted_steel" | "cast_iron"` | `reject` |
| `style_tag` | `"modern"` | 当前 style vocab | `warn` 后回退默认 |
| `detail_level` | `2` | `0 - 3` | `clamp` |

### 规则约束

- 立柱细长比必须在可接受范围内，防止过细导致视觉和结构失真。
- 底座直径必须与总高度联动，避免高杆小底座。
- 灯臂伸出长度不能超过稳定性与默认道路使用场景允许范围。
- 净空必须满足默认道路设施要求，灯头不得低于最低安全高度。
- 默认布置间距必须有显式目标范围；本期采用与当前系统兼容的默认值，逐步从固定 prior 升级为参数配置。

### 输出

`Lamp` 生成结果至少输出：

- `mesh`
- `bbox`
- `parameter_snapshot`
- `quality_metrics`
  - `face_count`
  - `poly_budget_k`
  - `dimension_error_ratio`
  - `ground_contact_ok`
  - `slenderness_ratio`
  - `clearance_ok`

## 参数接口与配置策略

### 通用请求对象

```python
@dataclass(frozen=True)
class GenerationRequest:
    asset_kind: Literal["bench", "lamp"]
    runtime_profile: Literal["preview", "production"] = "preview"
    device_backend: Literal["auto", "mps", "cuda", "cpu"] = "auto"
    seed: int = 42
    quality_profile: str = "default_v1"
    physics_profile: str = "default_v1"
    design_profile: str = "default_v1"
    precision: Literal["fp32"] = "fp32"
    allow_fallback: bool = True
    params: dict[str, object] = field(default_factory=dict)
```

### 地物专属参数对象

```python
@dataclass(frozen=True)
class BenchParams:
    width_m: float = 1.80
    depth_m: float = 0.55
    seat_height_m: float = 0.45
    backrest_height_m: float = 0.35
    backrest_angle_deg: float = 12.0
    leg_type: str = "dual_frame"
    armrest_enabled: bool = False
    slat_count: int = 5
    material_family: str = "metal_wood"
    style_tag: str = "modern"
    detail_level: int = 2


@dataclass(frozen=True)
class LampParams:
    pole_height_m: float = 5.00
    pole_radius_m: float = 0.06
    base_diameter_m: float = 0.35
    arm_length_m: float = 0.80
    luminaire_type: str = "flat_led"
    single_or_double_arm: str = "single"
    light_direction: str = "roadside"
    material_family: str = "metal"
    style_tag: str = "modern"
    detail_level: int = 2
```

### `runtime_profile`

- `preview`
  - 用于交互调参
  - 目标是低耗时、少细分、减少 blind retry
  - 允许使用较低 `detail_level` 和较宽松但可解释的质量门槛
- `production`
  - 用于正式导出
  - 必须满足质量门槛
  - 允许更高 detail 和更严格质量检查

### `device_backend`

默认值为 `auto`，解析规则如下：

1. Apple Silicon 且 `torch.backends.mps.is_available()` 为真时，优先选 `mps`
2. CUDA 可用时，选 `cuda`
3. 否则回退 `cpu`

仍允许显式强制 `mps / cuda / cpu`。设备切换不能改变参数语义或输出接口，只能影响运行后端和速度表现。

## 质量验收

### 通用门槛

- 几何质量
  - 面数达到类别最低门槛
  - 不超过 polygon budget
- 尺寸质量
  - 输出 bbox 与目标尺寸误差受控
- 结构质量
  - 不得悬空
  - 不得出现明显穿插
  - 不得出现支撑缺失
- 参数质量
  - 任一暴露参数都能稳定触发可预期几何变化
  - 参数变化不能导致随机失真或无响应

### Bench 验收

- 满足当前最低面数门槛，和现有质量脚本兼容，见 [scripts/m3_02_generate_procedural_assets.py](../scripts/m3_02_generate_procedural_assets.py)、[tests/test_m3_asset_generation_quality.py](../tests/test_m3_asset_generation_quality.py)
- 默认参数下必须稳定落地、结构完整
- `seat_height_m`、`slat_count`、`leg_type` 至少三项参数对最终几何有明确可见影响
- `production` 档优先一次命中质量门槛，避免反复 retry 才达到面数要求

### Lamp 验收

- 满足当前最低面数门槛，和现有质量脚本兼容，见 [scripts/m3_02_generate_procedural_assets.py](../scripts/m3_02_generate_procedural_assets.py)、[tests/test_m3_asset_generation_quality.py](../tests/test_m3_asset_generation_quality.py)
- 默认参数下满足立柱稳定性与净空约束
- `pole_height_m`、`arm_length_m`、`luminaire_type` 至少三项参数对最终几何有明确可见影响
- `production` 档优先一次命中质量门槛，避免通过多轮 blind retry 才达到最低面数

## 性能与设备策略

### 哪些部分优先由 MPS / CUDA 加速

- CLIP / embedder
- learned layout policy runtime
- learned program generator runtime
- 可选 Shape-E 解码

这些模块当前已经通过 `device` 字符串透传到 `torch.device(...)` 使用，但还没有形成统一 `auto` 策略，见 [src/roadgen3d/embedder.py](../src/roadgen3d/embedder.py)、[src/roadgen3d/street_layout.py](../src/roadgen3d/street_layout.py#L2207)。

### 哪些部分短期仍以 CPU 为主

- `trimesh` 程序化几何拼装
- `Shapely` 几何处理
- 规则校验与部分质量检查

因此，近期速度提升的第一抓手不是“把所有步骤搬到 GPU”，而是：

1. 减少 blind retry
2. 为 `Bench` 与 `Lamp` 设计能够直接命中质量门槛的 detail presets
3. 增加 `preview / production` 双运行档位
4. 统一模型推理层的 `auto / mps / cuda / cpu` 设备解析

### 近期性能目标

以下目标均指单资产参数化生成，不含大场景 compose、不含网络下载、不含大规模训练：

- `preview` 档  
  参数变更后的单资产重生成应接近实时。目标值：单资产几何生成与基本质检在本地交互场景下尽量控制在 `< 100 ms`。
- `production` 档  
  单资产正式几何生成与质量检查应尽量控制在 `< 500 ms`，并优先一次命中质量门槛，不依赖多轮 retry。

如果硬件或环境无法满足目标值，应先保证输出质量和参数一致性，其次再退化性能。

### Apple Silicon 与 CUDA 策略

- Apple Silicon 是近期默认优化方向，但文档不假设当前机器一定已经具备 MPS 可用状态。
- CUDA 不是备选废线，而是必须保留的等价后端。
- 后续实现中，所有 torch 相关入口都应通过统一设备解析函数收敛，而不是在各脚本中分散处理。

## 近期里程碑

### M1 文档与参数面定稿

- 定稿 `GenerationRequest`、`BenchParams`、`LampParams`
- 定稿 `preview / production` 语义
- 定稿 `auto / mps / cuda / cpu` 设备策略
- 定稿 `Bench + Lamp` 的质量门槛和超界处理策略

### M2 Bench 完整闭环

- 将 `Bench` 的参数输入、规则校验、几何组装、质量检查、导出登记做成完整链路
- 减少当前为达到面数门槛而发生的无效重试
- 增加 Bench 参数响应性与质量验收测试

### M3 Lamp 完整闭环 + Apple Silicon 默认加速策略

- 将 `Lamp` 的参数输入、规则校验、几何组装、质量检查、导出登记做成完整链路
- 增加统一 `device_backend=auto` 设备解析
- 让 CLIP / learned runtime / Shape-E 可选路径默认走 Apple Silicon 优先策略，同时保留 CUDA 替换能力

## 后续扩展

在 `Bench + Lamp` 两个样板完成之后，再逐步扩展到：

- `Tree`
- `Bus Stop`
- `Building`

扩展顺序仍应遵守同一原则：先参数化闭环、再质量门槛、最后再考虑复杂模型增强。
