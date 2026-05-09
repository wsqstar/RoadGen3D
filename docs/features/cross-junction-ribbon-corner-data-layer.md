# Cross-Junction Ribbon Corner 数据层设计

> 关键词：`cross_junction`、`carriageway_core`、`corner ribbon strip`、`sidewalk_corner_patches`、`turn_lane_patches`

## 1. 当前目标

课程主链路优先保证十字路口在 3D 近景中视觉正确、几何连续、语义可解释。当前 cross junction 不再依赖渲染层补洞，也不再使用从 `corner_center` 发散的扇形/三角 patch。正式数据层输出三组并行的 ribbon surface：

- `nearroad_corner_patches`：贴近车行道的设施/路缘相邻表面。
- `sidewalk_corner_patches`：行人净通行表面。
- `frontage_corner_patches`：退界或上下文铺装表面。

这些 surface 是默认 GLB 和 viewer overlay 的真实铺装来源。

## 2. 道路与路口核心面

道路生成首先根据中心线和横断面生成车行道、侧向人行/设施带等 Shapely 面。路口处使用单独的车行核心面：

```text
carriageway_core = junction_core_rect + approach throats
```

也就是说，`junction_core_rect` 只表示路口中心核心矩形；真正用于 3D 铺装、序列化和道路臂裁剪的是 `carriageway_core`。它将中心 core 与四个道路臂入口喉道合并，避免十字路口只剩小矩形或对角缺面。

对应实现：

- `src/roadgen3d/placement_zones.py::_junction_carriageway_surface`
- `build_placement_context()` 中道路 arm 和全局 carriageway 都以 `carriageway_core` 作为 junction trim 面
- `src/roadgen3d/street_layout.py::_serialize_osm_geometry` 优先序列化 `carriageway_core_rings`

## 3. Corner Ribbon Strip 生成

每个 cross junction quadrant 由相邻两条 arm 决定。生成逻辑如下：

1. 用两条 arm 的 split boundary normal 求出 `corner_center`。
2. 从两条 arm 面向该 quadrant 的 side strip stack 中读取同名 strip：
   - `nearroad_furnishing`
   - `clear_sidewalk`
   - `frontage_reserve`
3. 对每个 strip，分别取靠近道路的 `near_offset_m` 和远离道路的 `far_offset_m`。
4. 将 `corner_center` 视为两条道路侧边线的尖角顶点，而不是圆弧弧心。
5. 同一 quadrant 内所有 side strip 共享一个 turn radius；沿两条 arm 的 tangent 方向退开切点，并把真实弧心沿角平分线外移。
6. 对每条 strip 边界分别采样 fillet arc：

```python
ring = inner_arc + reversed(outer_arc)
```

7. 将 ring 转为 polygon，写入对应正式 bucket。

这使同一 quadrant 内的 `nearroad -> sidewalk -> frontage` 像彩虹带一样并排排列，而不是围绕 `corner_center` 切出一组扇形三角面。相邻 strip 共享同一条边界弧线，因此不会在 3D 中产生重叠暗斑。

对应实现：

- `src/roadgen3d/placement_zones.py::_corner_strip_ribbon_patch`
- `src/roadgen3d/placement_zones.py::_build_cross_corner_strip_patches`
- `src/roadgen3d/placement_zones.py::_build_cross_corner_kernel_geometries`

## 4. `turn_lane_patches` 的语义边界

`turn_lane_patches` 不再承载 side strip 铺装。当前 side-only cross junction 中，该字段可以为空。

保留该字段是为了未来真正的 vehicle/center turn lane：

- `stack_kind == "center"`
- 或 `surface_role in {"carriageway", "bike_lane", "bus_lane", "parking_lane"}`
- 或 `strip_kind in {"drive_lane", "bike_lane", "bus_lane", "parking_lane"}`

这条边界很重要：人行道、设施带、退界带的转角是正式 corner surface；车辆转向车道才属于 turn lane surface。

## 5. 3D 渲染契约

GLB base scene 只忠实渲染正式数据：

- `carriageway_core` -> `junction_carriageway_core_*`
- `crosswalk_patches` -> `junction_crosswalk_*`
- `frontage_corner_patches` -> `junction_frontage_corner_*`
- `nearroad_corner_patches` -> `junction_nearroad_corner_*`
- `sidewalk_corner_patches` -> `junction_sidewalk_corner_*`

渲染层不再生成 `junction_sidewalk_corner_apron_*` 之类的大面兜底。若存在旧数据中的 side-only `turn_lane_patches`，只有在缺少正式 corner surface 时才允许作为历史 fallback；新数据默认不走这条路径。

对应实现：

- `src/roadgen3d/street_layout.py::_build_osm_base_scene`
- `web/viewer/src/viewer-floating-lane.ts::buildFloatingLaneOverlay`

## 6. 保留的解释性数据

为了 viewer overlay、报告和调试可解释性，cross junction 仍保留：

- `quadrant_corner_kernels`
- `sidewalk_corner_polylines`
- `nearroad_corner_polylines`
- `frontage_corner_polylines`
- `arm_skeletons`
- `turn_lane_debug`

其中 polylines/kernels 用于解释 corner 走向；真正的物理铺装以 `*_corner_patches` 为准。

## 7. 回归验收

当前测试契约：

- cross junction 的 `carriageway_core.area > junction_core_rect.area`。
- 每类 `sidewalk/nearroad/frontage_corner_patches` 都按 quadrant 生成 ribbon polygon。
- 每个 ribbon polygon ring 点数大于简单四边形，不再以 `corner_center` 作为 fan 顶点。
- 同一 quadrant 内三类 ribbon patch 基本无重叠。
- `quadrant_corner_kernels.radius_m` 应为真实 fillet radius，而不是接近 0 的 stopline 小圆角。
- side-only cross junction 的 `turn_lane_patches == []`。
- GLB 中不生成 `junction_sidewalk_corner_apron_*`。
- GLB 中不生成默认可见的 side `junction_turn_lane_*`。

相关测试：

- `tests/test_reference_annotation_scene_bridge.py`
- `tests/test_reference_annotation_surfaces.py`

## 8. 历史教训

旧问题来自三类错误抽象：

1. 用 box chain 逼近弧形面，导致重叠、暗斑和低视角杂乱侧立面。
2. 将 T-junction 的 `_corner_connector_patch` 直接套用到 cross junction，导致 trim 和 join point 退化。
3. 把 side strip 写入 `turn_lane_patches`，再在渲染层把解释性数据画成物理铺装。

当前原则是：拐角铺装必须在数据生成层成为正式 polygon surface；渲染层只负责按语义忠实绘制。
