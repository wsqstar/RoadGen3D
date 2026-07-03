# RoadGen3D 项目目录分层说明

## 目标

本文件用于说明当前仓库目录组织，支持：

- 活跃运行层与历史/归档层解耦
- 新增内容默认落在 `ops/`
- 历史内容集中到 `legacy/`
- 根目录保留兼容入口，不影响现有命令和脚本调用

> 状态说明：不改动主要运行能力；`data/`、`models/` 仍保持本地路径不变。`artifacts/` 仅保留小型说明/占位文件，长期运行产物已迁移到服务器。

## 一、根目录层级

```text
RoadGen3D/
├── src/                # 核心后端/生成/服务代码
├── web/                # 前后端子系统（含 web/api、web/viewer、web/workbench 兼容入口）
├── metaurban/          # 外部上下文，保持子模块边界
├── vendor/             # 第三方/历史代码基线
├── assets/             # 运行与展示资产（与 artifacts 配合）
├── data/               # 运行输入与缓存数据（不迁移）
├── models/             # 模型目录（不迁移）
├── artifacts/          # 本地临时运行缓存；完整快照在 docker-dev
├── tools/              # 工具子模块（保持不动）
├── legacy/             # 历史与停产目录
│   ├── evaluation/     # legacy 文档与停产评估脚本入口
│   ├── ui_api_legacy/  # archived ui_api
│   ├── web_workbench/  # archived web/workbench
│   └── _archive/       # 归档仓储（含 .archive）
├── ops/                # 日常脚本与配置集中目录
│   ├── scripts/        # CLI 脚本与运维脚本
│   ├── configs/        # 配置文件与实验配置
│   └── examples/       # 示例与最小复现说明
└── docs/               # 文档与说明文件
    ├── ACTIVE_ENTRYPOINTS.md
    ├── current-progress.md
    ├── PROJECT_LAYOUT.md
    ├── PROJECT_SUMMARY_FOR_MEETING.md
    └── ...
```

## 二、兼容入口（保底）

为兼容历史调用，根目录保留以下软链接/兼容入口：

- `scripts` -> `ops/scripts`
- `configs` -> `ops/configs`
- `examples` -> `ops/examples`
- `evaluation` -> `legacy/evaluation`
- `ui` -> `legacy/ui_api_legacy`
- `.archive` -> `legacy/_archive`
- `web/workbench` -> `legacy/web_workbench`

### 使用建议

- 开发新流程优先使用新路径（`ops/`、`legacy/`），例如 `ops/scripts/...`。
- 现有自动化脚本、教程和命令不应立即失效：可持续通过兼容路径执行。
- 任何历史引用仍可通过兼容路径回退。

## 三、子模块与边界

- **不移动** 以下子模块真实路径（按现状保留）
  - `web/viewer`
  - `src/roadgen3d/eval_engine_ext`
  - `tools/download3dAssets`
  - `vendor/RoadGen`
  - `vendor/RoadPen`
- `tools/` 及 `vendor/` 保持子模块管理边界，不作为日常目录重构对象。
- `web/viewer` 为当前前端主入口；`web/workbench` 为历史入口，默认不启动。

## 四、Artifacts 边界

- `artifacts/` 不再作为公开仓库数据目录，只保留 `artifacts/README.md` 或占位文件。
- 完整历史产物快照位于 `docker-dev:/workspace/dev/github/gistudio/RoadGen3D/artifacts/`。
- 本地运行仍可临时写入 `artifacts/`，这些文件默认被 Git 忽略。
- 如需恢复本地副本：

```bash
rsync -a --partial docker-dev:/workspace/dev/github/gistudio/RoadGen3D/artifacts/ artifacts/
```

## 五、文档同步指引

- `readme.md`：新增目录示意与兼容说明；`Roadmap` 已内置任务状态入口（Completed / Pending / Deferred）
- `docs/ACTIVE_ENTRYPOINTS.md`：入口层和兼容边界说明
- `docs/current-progress.md`：活跃/历史文档归集说明
- 本页：统一目录导航
- 任务入口统一指向：`readme.md#roadmap`（Completed / Pending / Deferred），避免分散待办记录文件
