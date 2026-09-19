# 接口注释清单 — M-007 笔记组装器（DD-M-007）

> **生成方**：DD-M-007
> **日期**：2026-06-01
> **负责模块**：M-007
> **接口契约数**：6（IC-016/017/018/019/020/021，来自 DD-001）

---

## API-016 章节降级注入（IC-016）

```
[接口编号] API-016
[关联契约] IC-016（来自 DD-001）
[实现文件] research_tool/notes_schema.py
[实现位置] NotesSchemaOrchestrator.assemble_markdown() / degrade_chapters() / ChapterDegrader.degrade()
[函数签名注释]
  def degrade_chapters(
      transcript: Transcript,    # [必填 转写稿]
      interval_min: int          # [必填 等距切片间隔（分钟），默认 5]
  ) -> List[Chapter]:             # [返回值：章节列表，至少 1 个]
      """
      章节降级注入：当 LLM 未返回 video_chapters 时按 5min 等距切片。

      Args:
          transcript: 转写稿（segments 非空）
          interval_min: 等距切片间隔（分钟）

      Returns:
          章节列表（至少 1 个 chapter）

      Raises:
          无（V1.1 不抛错，segments 为空时返回单空 chapter）

      Example:
          >>> chapters = degrade_chapters(transcript, interval_min=5)
          >>> len(chapters) >= 1
          True
      """
[参数说明]
  transcript: Transcript 必填 segments 非空
  interval_min: int 必填 默认 5
[返回值说明]
  类型: List[Chapter]
  含义: 降级后的章节列表
  特殊值: 至少 1 个
[错误码说明]
  E_LLM_002_CHAPTERS_FALLBACK: 章节降级 - 触发条件 LLM 未返回 video_chapters
[来源标注] [DD-001:IC-016] [DD-001:MD-007 §degrade_chapters]
```

## API-017 VideoMeta 注入（IC-017）

```
[接口编号] API-017
[关联契约] IC-017（来自 DD-001）
[实现文件] research_tool/notes_schema.py
[实现位置] NotesSchemaOrchestrator.assemble_markdown() / inject_video_meta() / VideoMetaInjector.inject()
[函数签名注释]
  def inject_video_meta(
      meta: VideoMeta            # [必填 视频元数据]
  ) -> Dict[str, object]:        # [返回值：含 video_* 字段的 front_matter]
      """
      将 VideoMeta（标题/作者/时长/平台）注入 front_matter。

      Args:
          meta: 视频元数据

      Returns:
          含 video_title / video_author / video_duration / video_platform 的 dict

      Raises:
          无（None 字段保留 null 占位）

      Example:
          >>> fm = inject_video_meta(meta)
          >>> "video_title" in fm
          True
      """
[参数说明]
  meta: VideoMeta 必填 非空
[返回值说明]
  类型: Dict[str, object]
  含义: 含 video_* 字段的 front_matter
  特殊值: 必含 video_title / video_author / video_duration / video_platform
[错误码说明] 无（null 占位继续）
[来源标注] [DD-001:IC-017] [DD-001:MD-007 §inject_video_meta]
```

## API-018 截图引用嵌入（IC-018）

```
[接口编号] API-018
[关联契约] IC-018（来自 DD-001）
[实现文件] research_tool/notes_schema.py
[实现位置] NotesSchemaOrchestrator.assemble_markdown() / embed_screenshots() / ScreenshotEmbedder.embed()
[函数签名注释]
  def embed_screenshots(
      md: str,                    # [必填 Markdown 文本]
      paths: List[str]            # [必填 截图路径列表]
  ) -> str:                        # [返回值：嵌入后的 Markdown]
      """
      在 Markdown 中嵌入截图引用 ![](path)。

      Args:
          md: Markdown 文本
          paths: 截图路径列表

      Returns:
          嵌入后的 Markdown（含 5 个 ![](path) 引用）

      Raises:
          无（路径不存在 → 占位图）

      Example:
          >>> md_out = embed_screenshots(md, [p1, p2, p3, p4, p5])
          >>> md_out.count("![") == 5
          True
      """
[参数说明]
  md: str 必填
  paths: List[str] 必填 非空
[返回值说明]
  类型: str
  含义: 嵌入后的 Markdown
  特殊值: 含 5 个 ![](path) 引用
[错误码说明]
  E_NS_002_SCREENSHOT_MISSING: 截图路径不存在 - 触发条件 os.path.exists 返回 False
[来源标注] [DD-001:IC-018] [DD-001:MD-007 §embed_screenshots] [DD-001:SR-003]
```

## API-019 参考来源生成（IC-019）

```
[接口编号] API-019
[关联契约] IC-019（来自 DD-001）
[实现文件] research_tool/notes_schema.py
[实现位置] NotesSchemaOrchestrator.assemble_markdown() / generate_references() / ReferenceGenerator.generate()
[函数签名注释]
  def generate_references(
      meta: VideoMeta             # [必填 视频元数据]
  ) -> str:                        # [返回值：Markdown 参考来源章节]
      """
      生成 ## 参考来源 章节，含原始 URL/平台/作者。

      Args:
          meta: 视频元数据

      Returns:
          Markdown 参考来源章节

      Raises:
          无

      Example:
          >>> refs = generate_references(meta)
          >>> refs.startswith("## 参考来源")
          True
      """
[参数说明]
  meta: VideoMeta 必填 非空
[返回值说明]
  类型: str
  含义: Markdown 参考来源章节
  特殊值: 以 "## 参考来源" 开头
[错误码说明] 无
[来源标注] [DD-001:IC-019] [DD-001:MD-007 §generate_references]
```

## API-020 Markdown 总装（IC-020）

```
[接口编号] API-020
[关联契约] IC-020（来自 DD-001）
[实现文件] research_tool/notes_schema.py
[实现位置] NotesSchemaOrchestrator.assemble_markdown() / MarkdownAssembler.assemble()
[函数签名注释]
  def assemble_markdown(
      meta: VideoMeta,                        # [必填 视频元数据]
      summary: LLMSummary,                    # [必填 LLM 总结]
      screenshots: List[ScreenshotFrame],     # [必填 截图列表]
      transcript: Transcript                  # [必填 转写稿]
  ) -> str:                                    # [返回值：完整 Markdown]
      """
      将 front_matter / body / 截图 / 参考来源 拼装为完整 Markdown。

      Args:
          meta: 视频元数据
          summary: LLM 总结
          transcript: 转写稿
          screenshots: 截图列表

      Returns:
          完整 Markdown（含 YAML front_matter + Markdown body）

      Raises:
          无（局部失败 → 部分降级）

      Example:
          >>> md = assemble_markdown(meta, summary, screenshots, transcript)
          >>> md.startswith("---")
          True
      """
[参数说明]
  meta: VideoMeta 必填
  summary: LLMSummary 必填
  transcript: Transcript 必填
  screenshots: List[ScreenshotFrame] 必填
[返回值说明]
  类型: str
  含义: 完整 Markdown
  特殊值: 含 YAML front_matter + Markdown body
[错误码说明] 无（局部失败 → 部分降级 → 主链继续）
[来源标注] [DD-001:IC-020] [DD-001:MD-007 §assemble_markdown] [调研:S-005]
```

## API-021 章节降级策略钩子（IC-021, EP-002）

```
[接口编号] API-021
[关联契约] IC-021（来自 DD-001, EP-002 扩展点）
[实现文件] research_tool/notes_schema.py
[实现位置] NotesSchemaOrchestrator._build_chapter_fallback()
[函数签名注释]
  def _build_chapter_fallback(
      self,
      transcript: Transcript      # [必填 转写稿]
  ) -> List[Chapter]:              # [返回值：降级章节列表]
      """
      章节降级策略钩子（V1.2 扩展点，EP-002）。

      V1.1 固定 5min 等距切片；V1.2 可替换为聚类/语义切片。

      Args:
          transcript: 转写稿

      Returns:
          降级章节列表（至少 1 个）

      Raises:
          无

      Example:
          >>> orchestrator = NotesSchemaOrchestrator()
          >>> chapters = orchestrator._build_chapter_fallback(transcript)
          >>> len(chapters) >= 1
          True
      """
[参数说明]
  transcript: Transcript 必填
[返回值说明]
  类型: List[Chapter]
  含义: 降级章节列表
  特殊值: 至少 1 个
[错误码说明] 无
[来源标注] [DD-001:IC-021] [DD-001:ADR-008] [CE-008]
```

---

## 接口契约验收汇总

| 契约 | 函数签名注释 | 参数说明 | 返回值说明 | 错误码说明 | 通过 |
|------|-------------|---------|-----------|-----------|------|
| IC-016 | 有 | 有 | 有 | 有 | 4/4 |
| IC-017 | 有 | 有 | 有 | 有（说明） | 4/4 |
| IC-018 | 有 | 有 | 有 | 有 | 4/4 |
| IC-019 | 有 | 有 | 有 | 有 | 4/4 |
| IC-020 | 有 | 有 | 有 | 有（说明） | 4/4 |
| IC-021 | 有 | 有 | 有 | 有 | 4/4 |

**6/6 全部通过接口注释验收标准**。

[来源标注] [DD-001:IC-016/017/018/019/020/021] [soul §3.6 接口注释清单模板]

---

> **本文件结束**。6 个接口契约注释清单 100% 覆盖，交付 DD-S。
