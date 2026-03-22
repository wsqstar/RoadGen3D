# Gradio 信息分层重构计划（3 Tabs + 前置理论/流程说明）

## 摘要
把当前单页长表单重构为 `3 个主 Tab`，并在每个 Tab 顶部放置“短版要点”说明（4-6 行，含目的/输入/输出/原理）。按钮放入各自 Tab 内，避免全局拥挤，同时保留现有 M1/M2/M3/M4 功能与默认行为。

## 范围
1. 仅改 UI 组织与说明文案，不改核心算法逻辑。
2. 文件：`/Users/shiqi/Coding/github/GIStudio/RoadGen3D/scripts/m1_gradio_app.py`。
3. 保持现有回调函数与输出结构可用（包括实时训练日志流）。

## Tab 结构（已锁定）
1. `Tab A: 准备与索引`
- 顶部说明（短版要点）：
  - 为什么先准备索引与 latent（RAG 入口）
  - 输入：manifest/model/path
  - 输出：`index_ip.faiss/id_map.json/real_assets_for_pipeline.jsonl`
  - 与后续步骤关系（3/4/5/6 依赖它）
- 放置按钮：
  - `1) Prepare Assets + Index`
  - `2) Prepare Real Latents`
- 放置参数：
  - dataset profile、model path、manifest、real latent/mesh 参数、mock 参数
- 放置输出：
  - `Prepare Log`、`Encode Log`、`Assets Preview`

2. `Tab B: 推理与街道`
- 顶部说明（短版要点）：
  - 单资产链路：`text -> retrieve -> latent/mesh_ref -> voxel -> mesh`
  - 街道链路：多资产检索 + AABB 编排 + scene 导出
  - voxel 是诊断链路，街道主路径是 mesh 组合
- 放置按钮：
  - `3) Run Query Pipeline`
  - `4) Run Street Compose`
- 放置参数：
  - query/topk、decoder、voxel/export、street 参数、policy 选择/ckpt/temperature
- 放置输出：
  - 单资产：summary/hits/json/model/files
  - 街道：summary/instances/layout json/model/files

3. `Tab C: M4 训练评测`
- 顶部说明（短版要点）：
  - 蒸馏数据来源（rule 生成 slot 样本）
  - learned policy 训练目标（候选打分）
  - 评测指标与对比（learned vs rule）
  - 续训机制（resume ckpt）
- 放置按钮：
  - `5) Train Layout Policy (M4)`
  - `6) Train + Run Street`
- 放置参数：
  - M4 数据采集 seed 范围、训练超参、resume/recollect/eval 开关
- 放置输出：
  - `M4 Train Log`（实时 epoch loss）
  - `M4 Train Summary JSON`
  - `M4 Eval Report JSON`
- `6)` 点击后继续联动更新 Tab B 的街道输出组件（保持现有一键训练后生成的体验）。

## 具体实现步骤（决策完整）
1. 在 `build_demo()` 中引入 `gr.Tabs()`，新建 3 个 `gr.Tab(...)`。
2. 删除当前页面顶部“6 按钮横排”和大而全的单一 `Advanced Parameters`；按 Tab 重新归类控件。
3. 为每个 Tab 添加一个独立 `gr.Markdown`，写“短版理论+流程”说明（中文为主，术语保留英文缩写）。
4. 组件变量命名保持不变（如 `prepare_btn/run_btn/street_btn/train_btn`），仅改变布局位置，避免回调改动面过大。
5. 事件绑定 `.click(...).then(...)` 逻辑保持现状：
- `train_btn` 只更新 M4 输出与 policy ckpt。
- `train_and_street_btn` 先训练再调用 `run_street_compose`，更新街道输出。
6. 保留默认值：
- `dataset_profile=real`
- `decoder=shapee`
- `street_placement_policy=learned`
- `policy_ckpt=artifacts/m4/layout_policy.pt`
7. 清理页面冗余：
- 训练相关控件不出现在非训练 Tab。
- mock 控件折叠在准备 Tab 内。
- 避免重复展示同一参数（同一变量只保留一处输入源）。

## 公共接口/类型变更
1. Python 后端接口：不新增、不删除。
2. CLI：不变。
3. JSON 输出结构：不变。
4. 仅 Gradio 视图层结构重排与文案新增。

## 测试与验收
1. 自动回归：
- 运行 `pytest -q`，确保现有 39+ 测试继续通过。
2. UI 功能验收（手工）：
- Tab A 点击 1/2 可正常生成 index 和 latent，日志更新。
- Tab B 点击 3/4 可正常出单资产/街道结果与下载文件。
- Tab C 点击 5 可实时看到 epoch loss；点击 6 可训练后自动更新街道结果。
- policy ckpt 路径在训练完成后自动刷新，并被 4/6 正常读取。
3. 可读性验收：
- 首屏不再出现超长滚动表单。
- 每个 Tab 顶部有对应理论与流程说明，先解释再操作。

## 假设与默认值
1. 本轮不改算法路径（rule/learned/mesh_ref/shapee 行为不变）。
2. 文案采用“短版要点”，不写长篇教程。
3. 不新增额外 Tab（固定 3 个主 Tab）。
4. 训练实时输出保持 `epoch` 粒度，不改成 `batch` 粒度。
