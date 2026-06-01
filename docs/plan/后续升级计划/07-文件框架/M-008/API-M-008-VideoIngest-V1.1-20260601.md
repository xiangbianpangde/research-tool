# 接口注释清单 — M-008 管道适配器（DD-M-008）

> **生成方**：DD-M-008
> **日期**：2026-06-01
> **接口契约覆盖**：3/3（IC-022 / IC-023 / IC-024）

---

## API-022 → IC-022 落盘

```
[接口编号] API-022
[关联契约] IC-022（来自 DD-001）
[实现文件] research_tool/pipeline_adapter.py
[函数签名注释]
  def write_markdown(
      md: str,        # [Markdown 文本，必填，长度 ≥ 0]
      topic: str,     # [主题分类，必填，例: "general" / "ai" / "finance"]
      video_id: str   # [视频 ID，必填，例: "abc123def"]
  ) -> str:            # [返回写入的文件绝对路径]
      """
      将 Markdown 写入 raw/<topic>/<video-id>.md 并返回绝对路径。

      Args:
          md: Markdown 文本
          topic: 主题分类（用于路径分段）
          video_id: 视频 ID（用于文件名）

      Returns:
          写入的文件绝对路径

      Raises:
          E_PIPE_DISK_FULL: 磁盘剩余 < 100MB
          E_PIPE_001: 写文件失败（重试 1 次后仍失败）

      Example:
          >>> path = write_markdown("# Title\\n...", "general", "abc123")
          >>> path
          'raw/general/abc123.md'
      """
[来源标注] [DD-001:IC-022] [DD-001:MD-008 子模块1]
```

---

## API-023 → IC-023 触发 5 阶段管道

```
[接口编号] API-023
[关联契约] IC-023（来自 DD-001）
[实现文件] research_tool/pipeline_adapter.py
[函数签名注释]
  def trigger_pipeline(
      file_path: str  # [落盘后的 Markdown 绝对路径，必填，例: "raw/general/abc123.md"]
  ) -> StagesResult:   # [返回 StagesResult(stages_run, success, duration_ms, retried)]
      """
      触发既有管道的 5 阶段处理（subprocess 异步）。

      Args:
          file_path: 落盘后的 Markdown 绝对路径

      Returns:
          StagesResult 含 stages_run / success / duration_ms / retried

      Raises:
          E_PIPE_001: 管道失败（重试 1 次后仍失败）

      Example:
          >>> result = trigger_pipeline("raw/general/abc123.md")
          >>> result.stages_run
          ['ingest', 'analyze', 'index', 'notify', 'cleanup']
          >>> result.success
          True
      """
[来源标注] [DD-001:IC-023] [DD-001:MD-008 子模块2] [DD-001:SR-007] [DD-001:CE-009]
```

---

## API-024 → IC-024 tags 合并

```
[接口编号] API-024
[关联契约] IC-024（来自 DD-001）
[实现文件] research_tool/pipeline_adapter.py
[函数签名注释]
  def merge_tags(
      new_tags: Sequence[str],      # [新 tags 列表，必填]
      existing: Sequence[str]       # [既有 tags 列表，必填]
  ) -> list[str]:                     # [合并并去重后的 tags 列表]
      """
      合并新 tags 与既有 tags，冲突保留两版（union + dedup，保持首次出现顺序）。

      Args:
          new_tags: 新 tags 列表
          existing: 既有 tags 列表

      Returns:
          合并并去重后的 tags（大小写不敏感去重 + 保持首次出现顺序）

      Raises:
          无（冲突保留两版，不抛错）

      Example:
          >>> merge_tags(["AI", "RAG"], ["rag", "LLM"])
          ['AI', 'RAG', 'LLM']  # "AI" 首次出现，"RAG"/"rag" 视为同一（保留首次 "RAG"）
      """
[来源标注] [DD-001:IC-024] [DD-001:MD-008 子模块3]
```

---

## CE-009 Collect 配置注入（DD-001 引用，DD-M 推断编号 API-CFG-001）

```
[接口编号] API-CFG-001
[关联契约] CE-009 Collect 配置（来自 DD-001，未编号为 IC-XXX，但 MD-008 明确定义）
[实现文件] research_tool/pipeline_adapter.py
[函数签名注释]
  def inject_collect_config() -> bool:
      """
      注入/校验 Collect 子系统的运行时配置。

      Returns:
          True=注入成功且 schema 版本一致；False=注入失败（已 WARN 日志 + 回退默认配置）

      Raises:
          E_PIPE_CONFIG_MISMATCH: Collect 配置 schema 版本与 COLLECT_CONFIG_VERSION 不一致

      Example:
          >>> if inject_collect_config():
          ...     print("Collect config loaded")
      """
[来源标注] [DD-001:CE-009] [DD-M推断:MD-008 子模块4 明确定义 inject_collect_config 但无独立 IC 编号]
```

---

## 接口契约覆盖率

| IC 编号 | 实现文件 | 函数签名注释 | 参数说明 | 返回值说明 | 错误码说明 | 状态 |
|---------|---------|------------|---------|-----------|-----------|------|
| IC-022 | pipeline_adapter.py | ✓ | ✓ | ✓ | ✓ | 完整 |
| IC-023 | pipeline_adapter.py | ✓ | ✓ | ✓ | ✓ | 完整 |
| IC-024 | pipeline_adapter.py | ✓ | ✓ | ✓ | ✓ | 完整 |
| CE-009 | pipeline_adapter.py | ✓ | ✓ | ✓ | ✓ | 完整 |

**覆盖率：4/4 = 100%**

---

> **本文件结束**。M-008 接口注释清单完成。
