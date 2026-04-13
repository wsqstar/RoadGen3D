# Cross-Junction Corner 渲染修复经验总结

> **关键词**：`cross_junction`、corner polygon、trim outside corner、wheel-spoke artifact、SVG 分形

---

## 1. 问题现象

在 `cross_junction`（十字路口）的四个拐角处，出现了以下三类视觉异常：

| 视角 | 现象 |
|------|------|
| **3D 低视角** | 人行道拐角呈现杂乱的“车轮辐条”或“米粒”状线条（radial clutter）。 |
| **3D 俯视角 / Top-Down Map** | 拐角出现一块颜色更深的黄色补丁，像是多层几何重叠。 |
| **SVG 导出** | 十字路口被填充成“俄罗斯方块”或“分形田字”——本该完整的扇形区域被切割成多个互相嵌套的小矩形。 |

---

## 2. 根因分析

### 2.1 旧方案：折线拆段 + Axis-Aligned Box Chain

最初 `cross_junction` 的拐角几何由 `_add_polyline_segments` 生成：

1. 在 `_build_cross_corner_kernel_geometries` 中，先计算一段圆弧中心线（polyline）。
2. 渲染时把这段 polyline 切成一段段直线，每段都生成一个独立的 axis-aligned box（`_add_road_box`）。
3. 在弯曲拐角处，这些 box 彼此大量重叠；从低角度观察时，重叠的侧立面产生了密集的“辐条”假象；俯视时，重叠区域颜色更深。

**结论**：用离散的 box chain 逼近连续曲面，本质上就不适合描述平滑拐角。

### 2.2 弯路：直接复用 `_corner_connector_patch`

为了修复 box chain 的瑕疵，第一反应是把非 cross junction（T-junction 等）使用的 `_corner_connector_patch` 直接套用到 `cross_junction` 上。该函数的逻辑是：

- 读取两条 arm 的 `inner_offset` 与 `outer_offset`；
- 求切线延长线的交点得到 `join_point`；
- 用 `Polygon([outer_a, outer_join, outer_b, inner_b, inner_join, inner_a])` 构建 patch；
- 如果 `trim_outside_corner=True`，再切掉外侧三角形。

然而，这一移植在 cross junction 上产生了**更严重的几何碎裂**，原因有二：

#### (1) `trim_outside_corner` 对 90° 内角是毁灭性的

`_should_trim_outside_corner` 在 sweep ≈ 90° 时返回 `True`。对于 T-junction 的“外角”（sweep > 90° 的钝角或锐角），trim 可以削平突出的三角形；但 cross junction 的四个角都是**内角**（凹向路口中心），trim 恰恰把需要保留的扇形主体切掉了，导致 polygon 碎片化。

#### (2) Join-point 退化

在标准十字路口，两条道路互相垂直且关于 corner center 对称。`inner_start` 和 `inner_end` 常常都与 `corner_center` 共线或非常接近，切线延长线求出的 `join_point` 会落在 corner 内部或退化成一个极小的三角形。这使得生成的 polygon 面积只有预期的 10% ~ 20%，露出大量 ground context。

**结论**：`_corner_connector_patch` 的通用算法（含 trim）是为 T-junction/不规则路口设计的，**不适合直接套用到规则的 cross junction**。

---

## 3. 修复策略

### 3.1 废弃 box chain 渲染

删除了 `street_layout.py` 中的 `_add_polyline_segments` 和 `_extrude_corner_polyline_patches`，不再将 corner polyline 拆成 box 渲染。

### 3.2 在 cross-junction 专属路径内直接生成简单 Polygon Patch

由于 `_build_cross_corner_kernel_geometries` 已经知道：

- `corner_center`（两条 boundary 法线的交点，即路口 core rect 的顶点）；
- 每条 arm 的 `inner_offset`、`outer_offset`；

我们直接在同一个循环里构造一个简单、稳定的多边形：

```python
patch = Polygon([
    corner_center,           # 路口内角
    inner_start,             # arm A 的内侧边点
    outer_start,             # arm A 的外侧边点
    outer_end,               # arm B 的外侧边点
    inner_end,               # arm B 的内侧边点
    corner_center,
])
```

这个多边形的特点：
- **不依赖切线延长线**，避免了 join-point 退化；
- **不做 `trim_outside_corner`**，保留了完整的扇形填充；
- **不做 `difference(junction_core_rect)`**，因为 `corner_center` 本身就落在 core rect 的角上，自然与道路内边界衔接。

### 3.3 统一渲染路径

`street_layout.py` 中不再区分 `if junction_kind == "cross_junction"`，所有 junction 统一读取 `*_corner_patches` 并用 `_extrude_polygon` 挤出。这消除了 cross junction 的“特殊渲染分支”，降低了维护成本。

### 3.4 保留向后兼容的数据结构

为了不影响依赖 `quadrant_corner_kernels` 和 `*_corner_polylines` 的下游逻辑（如家具放置、测试断言），这些字段继续输出；新增 `*_corner_patches` 仅用于 3D/2D 渲染。

---

## 4. 关键教训与最佳实践

### 4.1 “连续曲面”优先用 Polygon，不要用离散 Primitive 拼接

当需要描述平滑的拐角填充时，**直接在生成端输出 Polygon（或 buffer后的 polygon），而不是在渲染端用 box chain 去逼近**。离散几何不仅会产生 Z-fighting 和重叠暗斑，还会随着视角变化产生严重的视觉 artifacts。

### 4.2 通用算法不等于万能算法

`_corner_connector_patch` 对 T-junction 效果很好，但对 cross junction 失效。这提醒我们：

> **在把非 cross junction 的算法移植到 cross junction 之前，必须验证其几何假设（如 sweep 角范围、内外角方向、trim 的适用性）。**

Cross junction 的四个角是规则 90° 内角，使用最简单的“从 corner_center 延伸到 outer offset 的多边形”往往比通用算法更可靠。

### 4.3 `trim_outside_corner` 只适用于真正的“外角”

在路口几何中：
- **外角**（concave，指向路口外部）：可以 trim，防止 patch 突出到路口范围之外；
- **内角**（convex，指向路口中心）：trim 会把有效填充切掉。

Cross junction 的四个角全部是内角，因此应始终关闭 trim。

### 4.4 更新测试以反映新的几何契约

原有测试断言 `cross_junction` **不包含** `sidewalk_corner_patches`。修复后，cross junction 与其他 junction 一样会输出 patches。测试需要同步更新：

```python
# 旧（错误）
assert "sidewalk_corner_patches" not in junction_geometry

# 新（正确）
assert len(junction_geometry.get("sidewalk_corner_patches", [])) == 4
```

---

## 5. 相关文件变更

| 文件 | 变更内容 |
|------|----------|
| `src/roadgen3d/placement_zones.py` | 在 `_build_cross_corner_kernel_geometries` 中为 cross junction 直接生成 `sidewalk/nearroad/frontage_corner_patches`；移除了两个 junction builder 中错误套用 `_corner_connector_patch` + `trim` 的代码。 |
| `src/roadgen3d/street_layout.py` | 删除了 `_add_polyline_segments` / `_extrude_corner_polyline_patches`；统一用 `_extrude_polygon` 渲染所有 junction 的 `*_corner_patches`；更新序列化逻辑。 |
| `tests/test_reference_annotation_scene_bridge.py` | 更新断言：cross junction 现在被期望包含 4 个 corner patches。 |

---

## 6. 结果

- 3D 中十字路口的四个拐角变为**完整、平滑的多边形填充**，彻底消除了 wheel-spoke / 深色补丁；
- SVG 导出中十字路口呈现**规则、对称的扇形/矩形填充**，不再出现分形嵌套；
- 所有相关测试（37 项）通过。
