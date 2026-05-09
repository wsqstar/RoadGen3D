# Junction Editor - 独立路口绘制编辑器

## 概述

Junction Editor 是一个独立的路口绘制工具，用于手动创建高质量的路口模板。这些模板可以在后续的编辑器中组装使用。

## 启动方式

推荐使用项目标准开发入口：

```bash
cd /Users/shiqi/Coding/github/GIStudio/RoadGen3D
make dev
```

默认服务地址：

- API: `http://127.0.0.1:8010`
- Viewer: `http://127.0.0.1:4173`

启动 Viewer 后，访问以下 URL 进入编辑器：

```
http://127.0.0.1:4173/#junction-editor
```

或在导航栏点击 **Junction Editor** 按钮。

## 功能特性

### 1. 结构化十字骨架

- 路口骨架使用 5 个点表达：1 个中心点 + 4 个道路端点
- 中心点在局部坐标系固定为 `(0, 0)`
- 四个道路臂固定对应 `North / East / South / West`
- 每个道路臂可配置：
  - `road_id`
  - `length_m`
  - `inbound_lane_count`
  - `outbound_lane_count`

### 2. 8 个 Lane Flow 配置

- 每个道路臂包含 2 个 flow：`inbound` 和 `outbound`
- 四个道路臂共 8 个 flow 桶
- 编辑器会根据 lane 数量自动生成平行 lane 线，并绑定到对应的 skeleton arm
- 生成的 lane 绑定会在左侧面板列出，并在导出 metadata 中保留

### 3. Corner Geometry

- 仍然保留手工 `Corner Skeleton` 绘制能力，用于补充角隅曲线和 patch 几何
- 手工 corner skeleton 继续按 `Q0` ~ `Q3` 归类
- 手动 patch 编辑仍在开发中

### 4. 路口属性编辑

- Junction ID / Label
- Junction Kind（十字/T型/复杂）
- 世界坐标锚点 `(X, Y)`；修改时会整体平移当前手工 corner 几何
- 人行横道深度

### 5. 导出与保存

- **Export JSON**: 导出带 `structured_cross_skeleton` metadata 的路口模板
- **Save Template**: 通过主后端 `http://127.0.0.1:8010/api/junction-templates` 保存到模板库
- 模板存储位置: `data/junction_templates/`

## 数据结构

### 路口模板格式

```json
{
  "template_id": "junction_1234567890",
  "junction": {
    "id": "junction_1234567890",
    "label": "My Custom Junction",
    "x": 0,
    "y": 0,
    "kind": "cross_junction",
    "connected_centerline_ids": [],
    "crosswalk_depth_m": 3.0,
    "source_mode": "explicit"
  },
  "compositions": [
    {
      "junctionId": "junction_1234567890",
      "kind": "cross_junction",
      "quadrants": [
        {
          "quadrantId": "Q0",
          "armAId": "",
          "armBId": "",
          "patches": [],
          "skeletonLines": [
            {
              "lineId": "skel_junction_1234567890_1234567890",
              "stripKind": "clear_sidewalk",
              "curve": {
                "start": { "x": 0, "y": 0 },
                "end": { "x": 10, "y": 10 },
                "control1": { "x": 2, "y": 5 },
                "control2": { "x": 8, "y": 5 }
              },
              "widthM": 3.0
            }
          ]
        }
      ]
    }
  ],
  "metadata": {
    "created_at": "2026-04-20T12:00:00",
    "version": "1.1",
    "structured_cross_skeleton": {
      "local_center": { "x": 0, "y": 0 },
      "anchor_world": { "x": 0, "y": 0 },
      "points_local": {
        "center": { "x": 0, "y": 0 },
        "north": { "x": 0, "y": -18 },
        "east": { "x": 18, "y": 0 },
        "south": { "x": 0, "y": 18 },
        "west": { "x": -18, "y": 0 }
      },
      "arms": [
        {
          "arm_key": "north",
          "direction": "north",
          "road_id": "road_north",
          "skeleton_id": "skel_junction_1234567890_north",
          "length_m": 18,
          "inbound_lane_count": 2,
          "outbound_lane_count": 2,
          "endpoint_local": { "x": 0, "y": -18 }
        }
      ],
      "lane_bindings": [
        {
          "lane_id": "road_north_inbound_1",
          "road_id": "road_north",
          "arm_key": "north",
          "direction": "north",
          "flow": "inbound",
          "lane_index": 0,
          "lane_width_m": 3.5,
          "skeleton_id": "skel_junction_1234567890_north",
          "offset_m": -3,
          "start_local": { "x": -3, "y": -18 },
          "end_local": { "x": -3, "y": -4 }
        }
      ]
    }
  },
  "created_at": "2026-04-19T12:00:00",
  "updated_at": "2026-04-19T12:00:00"
}
```

## 后端 API

### 保存模板

```http
POST /api/junction-templates
Content-Type: application/json

{
  "junction": {...},
  "compositions": [...],
  "metadata": {...}
}
```

### 列出所有模板

```http
GET /api/junction-templates
```

### 获取指定模板

```http
GET /api/junction-templates/{template_id}
```

### 更新模板

```http
PUT /api/junction-templates/{template_id}
Content-Type: application/json

{
  "junction": {...},
  "compositions": [...],
  "metadata": {...}
}
```

### 删除模板

```http
DELETE /api/junction-templates/{template_id}
```

### 下载模板

```http
GET /api/junction-templates/{template_id}/download
```

## 使用流程

1. **启动编辑器**: 访问 `/#junction-editor`
2. **配置十字骨架**: 在 `Road Arms` 面板中为 North/East/South/West 四个道路臂填写 `road_id`、长度、入向 lane 数和出向 lane 数
3. **检查 5 点骨架**: 在 `Cross Skeleton` 面板确认中心点 `(0,0)` 和四个端点的局部坐标
4. **查看 Lane Bindings**: 编辑器会自动生成并显示 8 个 flow 对应的 lane 绑定
5. **补充角隅几何**: 如有需要，可使用 `Draw Corner Skeleton` 继续绘制 corner curve
6. **保存/导出**: 点击 "Save Template" 保存到后端，或 "Export JSON" 下载文件

## 快捷键

- **鼠标滚轮**: 缩放画布
- **控制点拖拽**: 开发中
- **Reset Cross**: 重置十字骨架到默认 4 臂配置

## 技术栈

- **前端**: TypeScript + Canvas API
- **后端**: FastAPI + JSON文件存储
- **数据结构**: 与 Reference Plan Annotator 共享类型定义

## 与 Reference Plan Annotator 的区别

| 特性 | Reference Plan Annotator | Junction Editor |
|------|-------------------------|-----------------|
| 用途 | 标注完整路网 | 绘制单个路口 |
| 输入 | 参考图片 | 空白画布 |
| 输出 | scene_layout.json | 路口模板JSON |
| 复杂度 | 高（多条道路、交叉口） | 低（单个路口） |
| 重用性 | 场景特定 | 可重复使用 |

## TODO

- [ ] 3D 预览功能（集成 Three.js）
- [ ] 控制点拖拽编辑
- [ ] 面片手动编辑
- [ ] 模板库浏览器
- [ ] 导入已有模板
- [ ] 模板组装到编辑器

## 相关文件

- 前端: `web/viewer/src/junction-editor.ts`
- 样式: `web/viewer/src/style-junction-editor.css`
- 后端: `src/roadgen3d/api/junction_templates.py`
- 路由: `web/viewer/src/main.ts`, `web/viewer/src/ui.ts`
- 模板存储: `data/junction_templates/`
