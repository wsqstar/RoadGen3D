# RoadPen <-> RoadGen3D 2D 互转桥接说明

本目录下新增脚本用于实现 RoadPen 与 RoadGen3D 标注格式的双向桥接转换（不改主模型 schema）：

- `roadpen-to-roadgen3d`：RoadPen scene -> RoadGen3D `roadgen3d_reference_annotation_v2`
- `roadgen3d-to-roadpen`：RoadGen3D `annotation` -> RoadPen scene

入口脚本：

- `tools/bridge/format_bridge.py`

```bash
python tools/bridge/format_bridge.py roadpen-to-roadgen3d in.json -o out.json --mode preview
python tools/bridge/format_bridge.py roadgen3d-to-roadpen in.json -o out.json --mode preview
python tools/bridge/format_bridge.py roadpen-to-roadgen3d in.json -o out.json --mode strict
python tools/bridge/format_bridge.py roadgen3d-to-roadpen in.json -o out.json --mode repair
```

## 设计原则（对应计划）

1. 不改 RoadPen / RoadGen3D 核心模型。
2. 默认以几何保真优先，语义降级；高级语义放入 `__bridge_meta`。
3. 双向转换都输出带 `bridge_summary` 的 wrapper，便于回放和人工审阅。
4. 生成的两个格式彼此独立，不互相覆写。

## 模式说明

- `strict`：
  - 依赖输入可被对应解析器严格读取。
  - 缺失关键字段/不满足解析约束会失败退出。
  - 目标是“更严格可复用版本”。

- `preview`（默认）：
  - 允许轻量修复（字段兜底、几何修补、缺失字段降级）。
  - 不可互转/可降级项会写入 `warnings`、`losses`，但不阻塞流程。
  - 适合快速看效果与人工审阅。

- `repair`：
  - 在 `preview` 的基础上增加常见补齐行为（如 roundabout 近似展开等）。
  - 目标是更容易跑通本地流程，但仍保留可审阅的损失信息。

## RoadPen -> RoadGen3D 映射

可映射：

- 节点坐标 `x/y` -> `junction` 端点坐标
- 边 `from/to/controlPoints` -> centerline 片段
- 车道/宽度类 profile 信息 -> `*_lane_count`、`road_width_m`、`reference_width_px` 的近似推断
- `scalePxPerM` -> `pixels_per_meter`

非一一对应项（降级/空值）：

- `lane_count / strip / furniture / region / surface / junction_compositions` 等核心语义；
- `station_strip_patches` 严格约束项如缺失会在 `strict` 下报错，非 strict 下写 warning。

输出关键字段：

- `version`: 当前脚本统一使用 `roadgen3d_reference_annotation_v2`。
- `image_*`: 优先使用输入元数据；缺失时生成最小可用值并写入 warning。
- `centerlines/junctions`: 仅包含可互通的几何骨架。
- `__bridge_meta`: 记录来源与降级路径。

## RoadGen3D -> RoadPen 映射

提取策略：

- `centerlines` 拆分为 `nodes + edges`；
- `points` 去重并复用坐标生成拓扑节点；
- 多段折线按段生成对应 `edge`，保留 `controlPoints`；
- profile 从 `road_width_*` 与车道信息推断并缓存复用；
- 区域、家具、surface、roundabout 等高级语义写入 `__bridge_meta` / warning，默认不阻塞。

## 输出与兼容

每次转换会返回 wrapper JSON：

- `schema`: `roadpen_roadgen3d_bridge_v1`
- `bridge_summary`: 转换计数、warnings、losses、repaired 记录
- `source_path`: 输入路径
- `payload`: 真正的目标格式 payload

输出示例：

```json
{
  "schema": "roadpen_roadgen3d_bridge_v1",
  "command": "roadpen-to-roadgen3d",
  "mode": "preview",
  "payload": {...},
  "bridge_summary": {...},
  "warnings": [...]
}
```

## 不支持项（重要）

- 不是所有 RoadGen3D 的注释语义可回传到 RoadPen：
  - region / building_region / functional_zone / surface_annotation
  - station_strip_patch / junction_composition / furniture 细粒度关系
- RoadPen 内部 `profiles + geomType` 不能完整恢复 RoadGen3D 的全部交通设施语义。

## 建议验收

- 采用 2D 最小样例（直线、单控制点曲线）做快速互转。
- 拓扑样例（交叉、环形、分叉）检查 `nodes/edges` 与 `centerlines/junctions` 对齐。
- strict/preview/repair 缺失字段对比：
  - strict 严格失败
  - preview 给 warning
  - repair 额外尝试补齐可恢复项
- 回环测试：
  - `RoadPen -> RoadGen3D -> RoadPen`
  - 允许可控信息损失，关注 `bridge_summary.losses`。

## 回环一致性检查（新增）

新增 `tools/bridge/roundtrip_check.py` 可做自动批量回环检查与健康评分。

```bash
python tools/bridge/roundtrip_check.py --input /path/to/scene.json --format auto --mode preview
python tools/bridge/roundtrip_check.py --input /path/to/scenes_dir --format auto --mode preview --glob "*.json" --out report.json
python tools/bridge/roundtrip_check.py --input /path/to/scene.json --format roadgen3d --mode strict --tol 1.0 --len-drift 0.05 --node-recall 0.8 --edge-recall 0.6 --fail-on-warn
```

核心参数：

- `--format`：`roadpen|roadgen3d|auto`（`auto` 为默认自动推断）
- `--mode`：`preview|repair|strict`
- `--tol`：节点与边几何匹配容差（px）
- `--len-drift`：`geo_delta` 阈值（默认 `0.05`）
- `--node-recall`：节点回收率下界
- `--edge-recall`：边/中心线回收率下界
- `--sample-limit`：目录模式采样上限
- `--fail-on-warn`：有 warning 即判定失败
- `--reverse`：兼容标记，当前在 `auto` 模式下记录逆向复核意图

输出明细字段：

- 每个样本：`conversion_ok`、`geo_delta`、`node_recall`、`edge_recall`、`topology_ok`、`loss_digest`、`status`
- 汇总：`total`、`pass`、`warning`、`fail`、平均指标与失败列表
