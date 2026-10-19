# track-integration 交付报告

> 任务：集成入口层 3 模块（M-001 CLI 绑定 / M-008 管道适配器 / M-012 并发编排）
> 计划 ID：`plan_eb7bebb4`
> Track：track-integration
> Agent：Coder (`mvs_07cfa130f9034ddaa30ba7a8d313b4a3`)
> Worktree：`.worktrees/track-foundation-2`（复用 track-core 同 worktree + 同分支 `feature/track-foundation-2`）
> Commit hash：**`2a7afb0`**

详细见 `C:\Users\yhn\.mavis\plans\plan_eb7bebb4\outputs\track-integration\deliverable.md`。
简要摘要：

- 3 模块全实现：M-001 CLI `--video-url` + `--no-cache` 扩展 / M-008 Markdown 落盘 + 5 阶段管道触发 + tags 合并 + Collect 配置注入 / M-012 `Semaphore(3)` 并发 + 资源探测降级
- 配套 `src/application/video_pipeline.py` 顶层编排（URL 校验 → 任务构造 → 并发调度 → 触发管道 → 报告）
- 109 个新单测 + 16 集成测试 = 379 passed, 5 skipped（up from 270 in track-core）
- ruff check 全部干净
- README 新增"视频摄入"章节（175 行：NFR / 入口命令 / optional extras / 错误码速查）
- **下游 5 阶段管道零改动**——复用 `ResearchPipeline` 的 `resume=True` 机制让 collect 阶段自动跳过已存在的 `video_<id>.md` 文件
- **CLI 现有 7 个命令签名完全保持原样**——仅在 `run` 命令末尾追加 2 个可选参数

D7 模块边界遵守：跨模块文件操作 = 0；未触动 M-002/003/004/005/006/007/009/010/011 任何模块。
