# 数据流向图 — VideoIngest V1.1（TD-001）

> **生成方**：TD-001
> **日期**：2026-06-01
> **覆盖范围**：14 个数据实体（DE-001~DE-014），每实体含正常+异常流向

---

## DE-001 VideoURL

```
[数据实体] DE-001 VideoURL
[产生模块] M-001（BP-001 解析 / BP-015 本地文件）
[正常流转路径] 用户 → argv → M-001[argparse+platform_detect] → DE-001 → M-003 / M-004 / M-012
[异常流转路径] 用户 → M-001[非法 URL/不支持平台/参数冲突] → M-010[错误码 E_DL_001/E_DL_001_DENO_MISSING] → CLI stderr 退出
[消费模块] M-003, M-004, M-012
[存储位置] 内存（任务级）
[一致性要求] 强一致（URL 解析后才创建下游任务）
[来源标注] [SA:DE-001/EX-001/EX-002/EX-003]
```

## DE-002 VideoMeta

```
[数据实体] DE-002 VideoMeta
[产生模块] M-007（BP-008 抓取后注入 front matter）
[正常流转路径] M-003[yt-dlp --dump-json] → M-007[VideoMeta 注入] → DE-002 → M-007 (BP-007 章节) + M-007 (BP-014 引用)
[异常流转路径] M-007[API 限流/字段缺失] → M-010[warning E_DL_META_001] → M-007[null 占位继续]
[消费模块] M-007
[存储位置] 内存 + 最终持久化到 DE-009 PipelineNote front_matter
[一致性要求] 最终一致（异步抓取，允许部分缺失）
[来源标注] [SA:DE-002/EX-028/EX-029]
```

## DE-003 DownloadTask

```
[数据实体] DE-003 DownloadTask
[产生模块] M-003（BP-003 任务创建）
[正常流转路径] M-001 → M-003[yt-dlp 包装层] → DE-003 → M-004[缓存键计算]
[异常流转路径] M-003[403/412/版本过低/磁盘满/m3u8] → M-010[错误码登记] → M-001[部分任务失败汇总]
[消费模块] M-004, M-010
[存储位置] 内存 + 文件系统（视频文件落 raw/<topic>/assets/）
[一致性要求] 强一致
[来源标注] [SA:DE-003/EX-008~EX-013]
```

## DE-004 CacheEntry

```
[数据实体] DE-004 CacheEntry
[产生模块] M-004（BP-004 写入）
[正常流转路径] M-001 → M-004[query miss] → M-005/006/007/008[主链] → M-004[write] → DE-004 落 sqlite
[异常流转路径]
  - 缓存 DB 不可用: M-004 → M-010[E_CK_001 警告] → M-001[降级不走缓存继续主链]
  - 缓存命中陈旧: M-004 → M-001[双键校验失败] → 走 miss 分支 → 主链
  - 缓存条目损坏: M-004 → M-010[删除条目] → M-001[重新生成]
[消费模块] M-001（命中返回笔记）
[存储位置] sqlite ~/.cache/research-tool/cache.db
[一致性要求] 强一致（单进程内 asyncio.Lock）
[来源标注] [SA:DE-004/EX-014/EX-015/EX-016/BR-004/BR-024/BR-026]
```

## DE-005 Transcript

```
[数据实体] DE-005 Transcript
[产生模块] M-005（BP-005 转写）
[正常流转路径] M-004[miss] → M-005[whisper/bcut/groq] → DE-005 → M-006[LLM 总结] + M-001[RAG 上下文]
[异常流转路径]
  - Whisper 不可用: M-005 → 切 bcut → 切 groq → 仍失败 → M-010[E_TR_001]
  - OOM: M-005 → 自动降档 base/small → 重新转写
  - 音频损坏: M-005 → M-010[E_TR_001 + 保留中间产物] → M-001[需重下载]
  - CER 超阈: M-005 → M-010[警告] → M-001[继续] → 用户手动选 groq
[消费模块] M-006, M-001
[存储位置] 内存 + 文件系统（~/.cache/research-tool/transcripts/）
[一致性要求] 强一致
[来源标注] [SA:DE-005/EX-017/EX-018/EX-019/EX-020/BR-007]
```

## DE-006 LLMSummary

```
[数据实体] DE-006 LLMSummary
[产生模块] M-006（BP-006 总结）
[正常流转路径] M-005 → M-006[Deepseek-v4-flash] → M-006[字段名前缀校验 IF-014] → DE-006 → M-007[章节降级注入 IF-016]
[异常流转路径]
  - LLM 5xx 3 次: M-006 → 切 qwen-turbo fallback → DE-006(fallback_used=true)
  - Fallback 也失败: M-006 → M-010[E_LLM_001 + 保留中间产物]
  - YAML 解析失败: M-006 → 强制 LLM 重试 1 次 → 仍失败 → M-010[E_LLM_001]
  - 输出超 4k: M-006 → 自动截断 + 警告
[消费模块] M-007, M-008, M-001（RAG）
[存储位置] 内存 + 通过 DE-009 持久化
[一致性要求] 强一致
[来源标注] [SA:DE-006/EX-021~EX-024/BR-001/BR-002/BR-009/BR-025]
```

## DE-007 Chapter

```
[数据实体] DE-007 Chapter
[产生模块] M-007（BP-007 章节注入）
[正常流转路径] M-006 → M-007[解析 front matter.video_chapters] → DE-007 注入 front matter → M-008[PipelineNote]
[异常流转路径]
  - 章节数 < 3: M-007 → 等距切片 5min → 仍不足 → 1 章节兜底 → M-010[warning E_LLM_002_CHAPTERS_FALLBACK] → 继续
  - 时间戳越界: M-007 → 裁剪/丢弃该章节
  - 空转写稿幻觉: M-007 → 预检测 → 等距切片兜底
[消费模块] M-008
[存储位置] 内存 + 通过 DE-009 持久化
[一致性要求] 强一致（解析前不落盘）
[来源标注] [SA:DE-007/EX-025/EX-026/EX-027/BR-008/BR-021]
```

## DE-008 ScreenshotFrame

```
[数据实体] DE-008 ScreenshotFrame
[产生模块] M-009（BP-009 截图）
[正常流转路径] M-007 → M-009[ffmpeg I 帧] → DE-008 → M-007[Markdown 嵌入 IF-018]
[异常流转路径]
  - ffmpeg 失败: M-009 → 静默跳过（不阻塞）→ M-010[warning E_FM_001]
  - 截图 > 200KB: M-009 → 自动压缩 → DE-008(is_compressed=true)
[消费模块] M-007
[存储位置] 文件系统（raw/<topic>/assets/frames/）
[一致性要求] 最终一致（异步生成）
[来源标注] [SA:DE-008/EX-030/BR-022]
```

## DE-009 PipelineNote

```
[数据实体] DE-009 PipelineNote
[产生模块] M-008（BP-010 落盘）
[正常流转路径] M-007 → M-008[Markdown 总装] → DE-009 落 raw/<topic>/<video-id>.md → M-008[触发 5 阶段管道]
[异常流转路径]
  - 写入失败: M-008 → 重试 1 次 → 仍失败 → M-010[E_PIPE_001]
  - 管道失败: M-008 → 重试 1 次 → 仍失败 → M-010[E_PIPE_001]
  - tags 冲突: M-008 → 保留两版 + [duplicate_resolved] 标记
[消费模块] M-008（自身触发管道）、B-004（既有管道）、B-005（FS 落盘）
[存储位置] 文件系统（raw/<topic>/）
[一致性要求] 强一致
[来源标注] [SA:DE-009/EX-031/EX-032/EX-033/BR-028]
```

## DE-010 ErrorRecord

```
[数据实体] DE-010 ErrorRecord
[产生模块] M-010（BP-012 错误登记）
[正常流转路径] 任意模块 → 异常 → M-010[错误码匹配] → DE-010 落 LogEntry (DE-014) → M-001[CLI 退出码仲裁]
[异常流转路径]
  - 错误码未注册: M-010 → E_SYS_001 + 完整堆栈
  - 中间产物丢失: M-010 → 提示"需重新走主流程"
  - 3 并发退出码冲突: M-010[仲裁] → 取最严重错误码 [CE-010]
[消费模块] M-001（退出码）、M-011（日志）
[存储位置] 内存 + 日志 FS
[一致性要求] 最终一致
[来源标注] [SA:DE-010/EX-037/EX-038/EX-039/BR-013/BR-014]
```

## DE-011 CookieConfig

```
[数据实体] DE-011 CookieConfig
[产生模块] M-001（用户传入 --cookie-file 后解析）/ M-003（调用）
[正常流转路径] 用户 → --cookie-file → M-001[权限校验 0o600] → DE-011 → M-003[注入 yt-dlp]
[异常流转路径]
  - 权限非 0o600: M-001 → M-010[E_CK_001 + 提示 chmod 600]
  - 格式错误: M-001 → M-010[E_CK_001]
[消费模块] M-003
[存储位置] 内存（不持久化）
[一致性要求] 强一致（任务级）
[来源标注] [SA:DE-011/BR-010] [调研:S-001]
```

## DE-012 PreflightReport

```
[数据实体] DE-012 PreflightReport
[产生模块] M-002（BP-002 preflight）
[正常流转路径] CLI 启动 → M-002[4 项检查] → DE-012 → M-001 → M-003（YouTube 任务判定）
[异常流转路径]
  - Deno 缺失: M-002 → M-010[warning] → M-001[YouTube 任务返回 E_DL_001_DENO_MISSING]
  - ffmpeg/Node/Whisper 缺失: M-002 → 标记 [SIM-STUB] 警告 → M-001[继续但功能降级]
  - 并发共享过期: M-002 → M-001[每任务前重新拉取] (BR-027)
[消费模块] M-001, M-002（自反馈）
[存储位置] 内存（TTL 60s 共享）
[一致性要求] 强一致（单进程内共享）
[来源标注] [SA:DE-012/EX-004~EX-007/BR-005/BR-017/BR-027]
```

## DE-013 KnowledgeTag

```
[数据实体] DE-013 KnowledgeTag
[产生模块] M-008（BP-010 tags 合并）
[正常流转路径] M-007 → M-008[tags 合并 IF-024] → DE-013 注入 DE-009 → 知识树
[异常流转路径]
  - 冲突: M-008 → 保留两版 + is_duplicate_resolved=true
[消费模块] M-008, 知识树（B-004 后续）
[存储位置] 通过 DE-009 持久化（front matter 字段）+ 知识树
[一致性要求] 最终一致
[来源标注] [SA:DE-013/EX-033/BR-028]
```

## DE-014 LogEntry

```
[数据实体] DE-014 LogEntry
[产生模块] M-011（BP-016 任何步骤）
[正常流转路径] 任意模块 → M-011[JSON Lines] → DE-014 落 ~/.cache/research-tool/logs/<date>.jsonl
[异常流转路径]
  - 日志路径不可写: M-011 → 降级 stderr（不阻塞）
[消费模块] 用户/排障工具
[存储位置] 日志 FS
[一致性要求] 最终一致（append 写）
[来源标注] [SA:DE-014/EX-045/BR-016]
```

---

## 数据流向完整性自检

- 14 DE 全部含正常+异常流向 ✓
- 14 DE 全部有产生模块 + 消费模块 ✓
- 异常流向触发条件含 EX 编号或 SA 推断依据 ✓
- 0 数据孤岛 ✓

---

> **本文件结束**。14 DE × (正常+异常) = 28 条流向完整覆盖。
