# 如何进入 Junction Editor

## 快速启动

### 1. 启动标准开发环境

```bash
cd /Users/shiqi/Coding/github/GIStudio/RoadGen3D
make dev
```

默认会启动：

- API：`http://127.0.0.1:8010`
- Viewer：`http://127.0.0.1:4173`

### 3. 访问 Junction Editor

在浏览器中打开以下任一URL：

- **直接访问**: `http://127.0.0.1:4173/#junction-editor`
- **通过导航**: 
  1. 打开 `http://127.0.0.1:4173`
  2. 点击 **Menu** 按钮
  3. 选择 **Junction Editor**

## 页面导航

所有4个页面现在可以互相切换：

### 🏠 3D Viewer (`#/`)
主查看器，用于查看生成的3D道路场景

### 📝 Annotation (`#/scene-graph`)
Reference Plan Annotator，用于标注完整路网

### 🎨 Asset Editor (`#/asset-editor`)
3D资产编辑器，用于浏览和管理资产

### ✏️ Junction Editor (`#/junction-editor`)
**新！** 独立路口绘制编辑器

## 导航方式

### 方法1: Menu 菜单
1. 点击页面右上角的 **Menu** 按钮
2. 在弹出的菜单中选择要切换的页面

### 方法2: 直接URL
在浏览器地址栏输入对应的hash路由：
- `#/` - 3D Viewer
- `#/scene-graph` - Annotation  
- `#/asset-editor` - Asset Editor
- `#/junction-editor` - Junction Editor

### 方法3: 导航按钮
在各页面的工具栏中都有快速切换按钮（Junction Editor 顶部工具栏）

## Junction Editor 使用流程

1. **选择工具**:
   - **Select** (↖) - 预留给后续控制点编辑
   - **Draw Corner Skeleton** (✏️) - 绘制角隅骨架线
   - **Draw Patch** (◧) - 预留给后续 patch 编辑

2. **绘制路口**:
   - 在 `Road Arms` 面板中配置四个道路臂：`North / East / South / West`
   - 中心点始终是局部坐标 `(0, 0)`，四个端点共同构成 5 点十字骨架
   - 为每个道路臂配置 `inbound` / `outbound` lane 数量，形成 8 个 flow
   - 编辑器会自动生成 lane 线，并在 `Lane Bindings` 面板中显示它们与 skeleton arm 的绑定关系
   - 如需补充角隅几何，可继续使用 `Draw Corner Skeleton`

3. **保存/导出**:
   - **Reset Cross** - 恢复默认十字骨架
   - **3D Preview** - 预览3D效果（开发中）
   - **Export JSON** - 导出路口模板JSON
   - **Save Template** - 保存到后端模板库

## 验证安装

### 检查后端 API
```bash
curl http://127.0.0.1:8010/api/junction-templates
```
应返回空数组 `[]`

### 检查前端构建
```bash
cd /Users/shiqi/Coding/github/GIStudio/RoadGen3D/web/viewer
npm run build
```
应显示构建成功

## 常见问题

### Q: 看不到 Junction Editor 按钮？
A: 确保前端已重新构建：`npm run build`

### Q: 保存模板失败？
A: 确保后端 API 服务器正在运行

### Q: 画布不显示内容？
A: 打开浏览器开发者工具查看是否有JavaScript错误

## 文件位置

- **前端代码**: `web/viewer/src/junction-editor.ts`
- **后端API**: `src/roadgen3d/api/junction_templates.py`
- **模板存储**: `data/junction_templates/`
- **文档**: `docs/features/junction-editor.md`
