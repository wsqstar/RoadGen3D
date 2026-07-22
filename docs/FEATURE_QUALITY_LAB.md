# Feature Quality Lab

RoadGen3D 的视觉迭代单元应是一个小要素，而不是整条街。Feature Quality Lab 固定场景、随机种子和相机，只允许一个要素的参数发生变化，再把文本要求与俯视、纵向立面、横断面三张正交图交给视觉模型审查。

## 闭环

1. 选一个目标：`curb_ramp`、`bus_stop`、`building` 或 `surface_material`。
2. 写一句可验证的文本要求，并建立一个或多个参数 variant。
3. 使用同一图模板、同一 seed 和 `feature_tri_view` 捕获配置生成每个 variant。
4. 保存 `scene_layout.json`、`scene.glb`、三视图、参数和生成 provenance。
5. 视觉模型按文本一致性、几何正确性、放置有效性、材质协调性和视觉质量分别评分。
6. 模型只能返回目标要素 allowlist 内的参数 patch；任意其他字段都会被拒绝。
7. 重新生成并做 A/B；结构指标不退化且目标视觉分数提高时才接受。

这套流程不会把坡道和公交站绑定。`curb_ramp` 实验只能改 `curb_ramp_*` 字段；公交站是否存在属于固定控制变量。

## 创建第一个坡道实验

```bash
python scripts/feature_quality_lab.py \
  --target curb_ramp \
  --experiment-id curb_ramp_baseline_v1 \
  --output artifacts/feature_quality/curb_ramp_baseline_v1/experiment.json
```

生成的 manifest 是后续批量 runner、视觉审查和 HTML contact sheet 的稳定输入。三视图必须来自真实 GLB 的 `feature_tri_view` 捕获，不能用 2D 示意图代替。

## Viewer 快速工作台

进入专业 Viewer 的 3D 场景页面，点击顶部质量检查区的 **要素实验**：

1. 选择坡道、公交站、建筑或道路与材质。
2. 修改验收文本，选择 3–6 个变体，并决定是否调用视觉模型评分。
3. 点击 **批量生成**。后台固定当前图模板、seed 和非目标参数，逐个生成真实 GLB 与正交三视图。
4. 勾选两个完成的候选进入纯 A/B 对照；也可在主画布打开任一候选。
5. 点击候选的 **接受参数**，或直接接受最高分。参数会写回 Viewer 的确定性生成参数，但不会自动覆盖当前正式场景；再次执行正式生成后才形成新版本。

后端接口为：

- `POST /api/design/feature-quality-runs`
- `GET /api/design/feature-quality-runs/{run_id}`
- `POST /api/design/feature-quality-runs/{run_id}/accept/{variant_id}`
- `GET /api/design/feature-quality-runs/{run_id}/artifacts/{variant_id}/{view_id}`

运行记录保存在 `artifacts/feature_quality_runs/{run_id}/`，包括每个候选的 GLB、layout、三视图、视觉审查结果、总 manifest 和 HTML contact sheet。

## 质量门槛

- 每次实验只改一个要素；跨要素 patch 直接报错。
- 三视图缺一张就不进行视觉审查。
- 几何约束与审美评分分开，视觉模型不能替代尺寸和碰撞检查。
- 自动循环建议最多 3–5 轮；低置信度或没有可见证据时进入人工 A/B。
- 所有轮次保留 seed、输入文本、参数 diff、模型、评分理由和图像路径，避免“看起来更好”但无法复现。
