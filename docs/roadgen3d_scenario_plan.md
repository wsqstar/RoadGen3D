# RoadGen3D 场景方案实现计划

## 一、场景方案摘要

| 方案 | 核心特征 | 街道家具需求 |
|------|----------|--------------|
| **方案1-4** | 完整街道：双向四车道 + 骑行道 + 宽人行道 + 基本家具 | 树池、座椅、垃圾桶、公交站 |
| **方案5** | 精细化：多样长椅、自行车修理站、信息站、户外设施 | + 弧形长椅、健身站、信息亭 |
| **方案6** | 完整街道+中央绿化带 | + 中央绿化带 |
| **方案7** | 共享街道+口袋公园（不对称布局） | 分散树阵、社区集市、活动节点 |

---

## 二、当前系统能力

### 2.1 支持的 Strip Kind（横截面条带类型）

```
drive_lane       ✓ 已支持
bus_lane         ✓ 已支持
bike_lane        ✓ 已支持
parking_lane     ✓ 已支持
median           ✓ 已支持
nearroad_buffer  ✓ 已支持（Tree, Traffic_sign, Bollard）
nearroad_furnishing ✓ 已支持（Lamp_post, TrashCan, FireHydrant）
clear_sidewalk   ✓ 已支持（Pedestrian, Wheelchair, Mailbox）
farfromroad_buffer ✓ 已支持（Bench）
frontage_reserve ✓ 已支持（Building）
```

### 2.2 Curated Street Assets

```python
_CURATED_STREET_ASSET_IDS_FIXED_HQ = {
    "lamp": "lamp_modern_production",
    "trash": "objaverse_trash_f16b7d84113d4cba869412ee95769910",
    "bollard": "curated_railing_module_v1",
    "tree": "objaverse_tree_909de376b61d4a2fb073e195fb719619",
}
```

### 2.3 已有资产类别（street_furniture_manifest.jsonl）

- lamp: 17 个
- tree: 15 个
- bollard: 15 个
- trash: 13 个
- bench: 14 个
- bus_stop: 1 个
- kiosk: 1 个
- sculpture: 3 个
- flower: 3 个

---

## 三、需要新增的 2D 标注能力

### 3.1 特殊功能区标注

当前 annotation.json 只支持 centerline + cross_section_strips，需要扩展支持：

| 功能区 | 描述 | 建议字段 |
|--------|------|----------|
| **School Zone** | 学校入口广场、家长等候区、儿童专属步道 | `zones[]` with `zone_type: "school_entrance"` |
| **Commercial Frontage** | 商户外摆区 | `zones[]` with `zone_type: "commercial_frontage"` |
| **Community Plaza** | 社区广场、社区聚会亭 | `zones[]` with `zone_type: "community_plaza"` |
| **Park Area** | 露天剧场、儿童游乐区、健身站 | `zones[]` with `zone_type: "park"` |
| **Shared Street** | 共享街道（非对称布局） | `zones[]` with `zone_type: "shared_street"` |

### 3.2 公交站详细标注

当前只支持 lane 级别，需要：

```json
{
  "centerline_id": "cl_main",
  "bus_stop": {
    "position_m": 45.5,
    "direction": "forward",
    "length_m": 15,
    "has_shelter": true,
    "has_bench": true,
    "has_digital_sign": false
  }
}
```

### 3.3 树木布局标注

方案7需要不规则树阵分布：

```json
{
  "tree_placement": "irregular",  // or "grid", "cluster"
  "spacing_range_m": [4, 8],
  "cluster_centers": [{"x": 10, "y": 5}, ...]
}
```

### 3.4 铺装材质标注

不同功能区需要不同铺装：

```json
{
  "surface_material": "colored_concrete",
  "color_hex": "#E8D4B8",
  "pattern": "pavers"
}
```

---

## 四、需要新增的 3D 资产

### 4.1 分类优先级

| 优先级 | 资产类型 | 用途 | 来源建议 |
|--------|----------|------|----------|
| **P0** | 多样化长椅 | 方案1-7 | Objaverse: bench, stool |
| **P0** | 公交站候车亭 | 方案1-4 | 需采购/建模 |
| **P1** | 自行车修理站 | 方案5 | 需建模 |
| **P1** | 信息亭/社区布告栏 | 方案5,7 | Objaverse: kiosk |
| **P1** | 多样化树木 | 方案7 | Objaverse: tree (已有15个) |
| **P2** | 弧形长椅 | 方案5 | 需建模 |
| **P2** | 健身设施 | 方案5 | 需建模 |
| **P2** | 儿童游乐设施 | 方案5 | 需建模 |
| **P3** | 社区聚会亭 | 方案5 | 需建模 |
| **P3** | 露天剧场 | 方案5 | 需建模 |
| **P3** | 户外咖啡吧台 | 方案5 | 需建模 |
| **P3** | 家长等候亭 | 方案5 | 需建模 |

### 4.2 资产获取策略

```
street_furniture_manifest.jsonl 中已有资产（可立即使用）:
├── bench: 14 个
├── tree: 15 个
├── lamp: 17 个
├── trash: 13 个
├── bollard: 15 个
├── bus_stop: 1 个
├── kiosk: 1 个
└── sculpture: 3 个

需要扩展的类别:
├── curved_bench: 0 (需建模或找更多)
├── bike_repair_station: 0
├── fitness_station: 0
├── playground_slide: 0
├── amphitheater: 0
└── outdoor_cafe_table: 0
```

### 4.3 需更新的 METAURBAN_STRIP_ASSET_HINTS

```python
METAURBAN_STRIP_ASSET_HINTS: Dict[str, Tuple[str, ...]] = {
    # 现有...
    "nearroad_buffer": ("Tree", "Traffic_sign", "Bollard"),
    "nearroad_furnishing": ("Lamp_post", "TrashCan", "FireHydrant"),
    "clear_sidewalk": ("Pedestrian", "Wheelchair", "Mailbox"),
    "farfromroad_buffer": ("Bench",),
    "frontage_reserve": ("Building",),

    # 需要新增/扩展...
    "bike_lane": ("Bike_rack",),  # 新增
    "bus_stop": ("Bus_shelter", "Bus_bench"),  # 新增
}
```

---

## 五、渲染方案更新

### 5.1 非矩形区域支持

方案7的"共享街道"需要支持不规则边界：

```python
# 当前：只能生成矩形 polygon
# 需要：支持多边形 + 曲边

class ZonePolygon:
    kind: Literal["rectangle", "polygon", "circle"]
    coordinates: List[Tuple[float, float]]  # 对于 polygon
    center: Tuple[float, float]  # 对于 circle
    radius_m: float
```

### 5.2 多材质地面

```python
# 不同 strip 不同材质
strip_materials = {
    "drive_lane": "asphalt",
    "bike_lane": "blue_asphalt",
    "clear_sidewalk": "colored_concrete",
    "nearroad_furnishing": "pavers",
    "median": "grass",
}
```

### 5.3 动态资产放置

```python
# 方案7需要不规则树木分布
def place_trees_irregularly(
    zone: Zone,
    density_per_100sqm: float,
    min_spacing_m: float = 3,
    max_spacing_m: float = 10,
):
    # Poisson disk sampling for natural distribution
    pass
```

### 5.4 功能节点渲染

```python
# 特殊功能区需要预定义的3D结构
FUNCTIONAL_NODES = {
    "school_entrance": {
        "structures": ["parent_shelter", "bench", "tree_cluster"],
        "pavement": "colored_playground",
    },
    "community_plaza": {
        "structures": ["gathering_pod", "information_kiosk", "seating"],
        "pavement": "decorative_pavers",
    },
    "outdoor_cafe": {
        "structures": ["cafe_table", "cafe_chair", "sunshade"],
        "pavement": "wood_deck",
    },
}
```

---

## 六、实施路线图

### Phase 1: 基础完善（P0 优先级）

1. **扩展 annotation schema**
   - [ ] 添加 `zones[]` 字段支持特殊功能区
   - [ ] 添加 `bus_stop` 子对象
   - [ ] 添加 `surface_material` 字段

2. **完善资产清单**
   - [ ] 验证 bus_stop 资产可用性
   - [ ] 标记可用 bench
   - [ ] 更新 METAURBAN_STRIP_ASSET_HINTS

3. **修复已知问题**
   - [ ] 验证 normalizeAnnotation wrapper 格式支持
   - [ ] 调查 3D 三角形渲染问题

### Phase 2: 增强功能（P1 优先级）

4. **扩展 3D 资产**
   - [ ] 获取更多 bike_repair_station 模型
   - [ ] 获取/建模信息亭模型
   - [ ] 扩展多样化树木

5. **渲染增强**
   - [ ] 支持 colored_pavement 材质
   - [ ] 支持 central_green_belt（方案6）

### Phase 3: 高级功能（P2/P3 优先级）

6. **不规则布局支持**
   - [ ] 非矩形 zone 渲染
   - [ ] Poisson disk 树木分布
   - [ ] 功能节点预制结构

7. **特殊设施建模**
   - [ ] 健身站
   - [ ] 儿童游乐设施
   - [ ] 露天剧场
   - [ ] 社区聚会亭

---

## 七、测试验证

### 7.1 标注测试

```bash
# 测试新 annotation schema
cd web/viewer && npm run dev
# 在 Reference Plan Annotator 中:
# 1. 导入带 zones 的 annotation
# 2. 验证特殊区域显示正确
```

### 7.2 渲染测试

```bash
# 测试基本场景
make test-pipeline plan_id=test_complete_street

# 测试中央绿化带（方案6）
make test-pipeline plan_id=test_central_green_belt

# 测试共享街道（方案7）
make test-pipeline plan_id=test_shared_street
```

### 7.3 性能基准

| 场景复杂度 | 预期顶点数 | 预期加载时间 |
|------------|------------|--------------|
| 基础完整街道 | < 500K | < 3s |
| + 多样家具 | < 1M | < 5s |
| 共享街道+口袋公园 | < 2M | < 10s |

---

## 八、文件清单

| 文件路径 | 用途 |
|----------|------|
| `src/roadgen3d/reference_annotation.py` | annotation schema 定义 |
| `src/roadgen3d/metaurban_procedural.py` | 3D 场景生成 |
| `src/roadgen3d/street_layout.py` | 街道布局逻辑 |
| `src/roadgen3d/renderer.py` | 渲染器 |
| `web/viewer/src/scene-graph.ts` | Web 端场景图 |
| `assets/graph_templates/*/annotation.json` | 标注模板 |

---

## 九、附录：方案特征对照表

| 功能 | 方案1 | 方案2 | 方案3 | 方案4 | 方案5 | 方案6 | 方案7 |
|------|:-----:|:-----:|:-----:|:-----:|:-----:|:-----:|:-----:|
| 双向四车道 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| 骑行道 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| 宽人行道 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| 中央绿化带 | | | | | | ✓ | |
| 不对称布局 | | | | | | | ✓ |
| 多样长椅 | | | | | ✓ | | ✓ |
| 健身设施 | | | | | ✓ | | |
| 儿童游乐 | | | | ✓ | ✓ | | |
| 公交站 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| 商业外摆 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| 学校入口 | | | | ✓ | ✓ | | |
| 社区广场 | | | | | ✓ | | ✓ |
| 口袋公园 | | | | | | | ✓ |
