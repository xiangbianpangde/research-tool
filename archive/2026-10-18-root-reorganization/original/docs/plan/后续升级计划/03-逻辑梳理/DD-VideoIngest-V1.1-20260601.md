# 数据字典 — VideoIngest V1.1（SA-001 终版）

> **生成方**：SA-001
> **日期**：2026-06-01
> **接收方**：TD-001（顶层设计师）/ DD-001（详细设计）
> **数据实体统计**：14 个实体（覆盖 16 流程所有数据引用）

---

## 0. 数据实体总览

| 实体编号 | 实体名称 | 关联流程 | 风险等级 |
|----------|----------|----------|----------|
| DE-001 | VideoURL | BP-001, BP-003, BP-015 | 标准 |
| DE-002 | VideoMeta | BP-006, BP-007, BP-008, BP-014 | 标准 |
| DE-003 | DownloadTask | BP-003, BP-011 | 核心 |
| DE-004 | CacheEntry | BP-004 | 核心 |
| DE-005 | Transcript | BP-005, BP-006, BP-013 | 核心 |
| DE-006 | LLMSummary | BP-006, BP-010, BP-013 | 核心 |
| DE-007 | Chapter | BP-007, BP-010 | 标准 |
| DE-008 | ScreenshotFrame | BP-009, BP-010 | 辅助 |
| DE-009 | PipelineNote | BP-010 | 核心 |
| DE-010 | ErrorRecord | BP-012 | 核心 |
| DE-011 | CookieConfig | BP-003 | 核心 |
| DE-012 | PreflightReport | BP-002 | 核心 |
| DE-013 | KnowledgeTag | BP-010, BP-013 | 辅助 |
| DE-014 | LogEntry | BP-016 | 辅助 |

---

## 1. 数据实体详图

---

### DE-001 VideoURL

```
[实体编号] DE-001
[实体名称] 视频 URL 入参对象
[实体描述] CLI 解析后的标准化 URL/本地路径对象，承载平台标识和校验结果。30-60 字。
[字段列表]
  字段名             | 字段类型  | 约束                                | 描述
  --------------------+----------+-------------------------------------+----------------------------------
  raw_input           | str      | 必填                                | 用户原始入参（URL 或本地路径）
  normalized_url      | str      | 必填，URL 必须合法                   | 标准化后的 URL（去除追踪参数）
  platform            | enum     | 必填，bilibili/youtube/local/unknown | 平台识别器输出 [PRD:F-002.AC-1]
  url_hash_sha256     | str(64)  | 必填，唯一                           | sha256(normalized_url)
  is_batch            | bool     | 默认 False                          | 是否为多视频批量入参 [PRD:F-008]
  batch_index         | int      | 默认 0；is_batch=True 时必填         | 批量中索引
  file_path_local     | Optional[Path] | is_batch/local 时可填        | 本地视频文件路径 [PRD:F-007]
  detected_at         | datetime | 必填                                | 解析时间戳
[实体关系]
  DE-001 → DE-003 (1:1) ：每个 URL 对应一个下载任务
  DE-001 → DE-004 (1:1) ：每个 URL 对应一个缓存条目
[来源标注] [PRD:F-001/F-002/F-007/F-008]
```

---

### DE-002 VideoMeta

```
[实体编号] DE-002
[实体名称] 视频元信息（front matter video_ 前缀字段集）
[实体描述] 视频基础元信息，直接映射为 Markdown front matter 的 video_ 前缀字段。30-80 字。
[字段列表]
  字段名             | 字段类型        | 约束                              | 描述
  --------------------+----------------+-----------------------------------+------------------------------
  video_id            | str            | 必填，唯一                        | 视频唯一标识（BV/YouTube ID 或 sha256）
  video_source_url    | str            | 必填                              | 原始 URL
  video_title         | str            | 必填                              | 标题
  video_author        | str            | 必填                              | 作者/UP主
  video_duration      | int (秒)        | 必填，> 0                         | 视频时长
  video_cover_url     | str            | 必填                              | 封面 URL
  video_tags          | List[str]      | 默认 []                           | 标签列表
  video_chapters      | List[Chapter]  | 默认 []（结构见 DE-007）           | 章节列表
  video_created_at    | datetime       | 必填                              | 视频上传时间
  video_platform      | enum           | 必填                              | bilibili/youtube/local
  video_ingested_at   | datetime       | 必填                              | 摄入时间戳
  video_screenshots   | List[str]      | 默认 []                           | 截图路径列表（≤ 5）
  video_cer_estimate  | Optional[float]| 可选                              | 转写 CER 估算（监控用）[SA推断:监控字段]
  video_llm_model     | str            | 必填，默认 "deepseek-v4-flash"     | 使用的 LLM 模型 [PRD:NF-010]
  video_transcriber   | str            | 必填                              | whisper/bcut/groq [PRD:F-014]
[实体关系]
  DE-002 → DE-007 (1:N) ：含多个 Chapter
  DE-002 → DE-008 (1:N) ：含多张截图
[来源标注] [PRD:F-006.AC-2/F-017] [调研:S-005 第 9 章 notes_schema] [SA推断:CER 估算/llm_model/transcriber 为可观测性字段]
```

---

### DE-003 DownloadTask

```
[实体编号] DE-003
[实体名称] 视频下载任务
[实体描述] 单个视频下载任务的全生命周期状态对象。40-90 字。
[字段列表]
  字段名             | 字段类型        | 约束                              | 描述
  --------------------+----------------+-----------------------------------+------------------------------
  task_id             | str (uuid)     | 必填，唯一                        | 任务唯一标识
  url                 | str            | 必填                              | 标准化 URL
  platform            | enum           | 必填                              | bilibili/youtube
  cookie_file_path    | Optional[Path] | 用户提供 --cookie-file 时填       | Cookie 文件路径 [PRD:F-012]
  yt_dlp_version      | str            | 必填，≥ 2023.07.06                | 实际 yt-dlp 版本 [PRD:NF-004]
  status              | enum           | 必填                              | pending/downloading/done/failed
  file_path           | Optional[Path] | status=done 时必填                | 媒体文件落盘路径
  file_size_bytes     | Optional[int]  | status=done 时必填                | 文件大小
  etag                | Optional[str]  | 服务端返回时填                    | HTTP ETag [PRD:F-009.AC-2]
  last_modified       | Optional[datetime] | 服务端返回时填                | Last-Modified [调研:S-102]
  started_at          | datetime       | 必填                              | 启动时间
  finished_at         | Optional[datetime] | status=done/failed 时填        | 完成时间
  retry_count         | int            | 默认 0；上限 2                    | 重试次数 [PRD:NF-009]
  error_code          | Optional[str]  | status=failed 时填                | 错误码
[实体关系]
  DE-003 → DE-001 (N:1) ：属于某个 URL
  DE-003 → DE-011 (N:1) ：可选关联 Cookie 配置
[来源标注] [PRD:F-003/F-012/NF-004/NF-009] [调研:S-101/S-102]
```

---

### DE-004 CacheEntry

```
[实体编号] DE-004
[实体名称] 缓存条目
[实体描述] 视频摄入结果缓存，支持双键查询避免陈旧笔记。40-80 字。
[字段列表]
  字段名             | 字段类型        | 约束                              | 描述
  --------------------+----------------+-----------------------------------+------------------------------
  cache_key           | str            | 必填，唯一                        | sha256(url) + ":" + etag_or_last_modified
  url_sha256          | str (64)       | 必填                              | 单纯 URL 哈希
  etag_or_lm          | Optional[str]  | 服务端返回时填                    | etag 或 last_modified
  video_id            | str            | 必填                              | 关联视频
  note_path           | Path           | 必填                              | raw/<topic>/<video-id>.md 路径
  transcript_path     | Path           | 必填                              | 转写稿路径
  summary_path        | Path           | 必填                              | 总结 JSON 路径
  created_at          | datetime       | 必填                              | 缓存写入时间
  ttl_seconds         | int            | 默认 2592000（30 天）             | 缓存有效期 [SA推断:TTL 默认 30 天]
  hit_count           | int            | 默认 0                            | 命中次数（监控）[SA推断:可观测性字段]
  etag_revalidate_at  | Optional[datetime]| YouTube 视频时填                | YouTube 强制 revalidate 时刻 [SA洞察#3]
[实体关系]
  DE-004 → DE-001 (N:1) ：URL 关联
  DE-004 → DE-009 (1:1) ：缓存的笔记
[来源标注] [PRD:F-009] [调研:S-102] [SA推断:TTL=30d/hit_count] [SA洞察#3:YouTube etag 抖动]
```

---

### DE-005 Transcript

```
[实体编号] DE-005
[实体名称] 视频转写稿
[实体描述] 转写引擎输出，含段落级时间戳与文本。30-70 字。
[字段列表]
  字段名             | 字段类型              | 约束                          | 描述
  --------------------+-----------------------+-------------------------------+----------------------
  transcript_id       | str (uuid)            | 必填，唯一                    | 转写稿唯一标识
  video_id            | str                   | 必填                          | 关联视频
  segments            | List[Segment]         | 必填                          | 段落列表
  total_duration      | int (秒)              | 必填                          | 总时长
  model_size          | enum                  | 必填                          | tiny/base/small/medium/large [PRD:F-014]
  transcriber_engine  | enum                  | 必填                          | whisper/bcut/groq
  language            | str                   | 默认 "zh"                     | 主语言
  cer_estimate        | Optional[float]       | 监控字段                      | 字符错误率估算 [SA推断:监控]
  cache_hit           | bool                  | 默认 False                    | 是否命中二级缓存 [PRD:F-004.AC-3]
  created_at          | datetime              | 必填                          | 转写时间
  audio_fingerprint   | str (sha256, 64)      | 必填                          | 音频指纹（用于二级缓存）[SA推断:用于二级缓存]
[子结构 Segment]
  start: int (毫秒)    | 必填
  end: int (毫秒)      | 必填
  text: str            | 必填
[实体关系]
  DE-005 → DE-001 (N:1) ：URL 关联
  DE-005 → DE-006 (1:1) ：转写稿被总结
[来源标注] [PRD:F-004/F-014] [调研:S-004] [SA推断:cer_estimate/audio_fingerprint]
```

---

### DE-006 LLMSummary

```
[实体编号] DE-006
[实体名称] LLM 总结结果
[实体描述] LLM 输出的 Markdown + YAML front matter 总结结果。40-100 字。
[字段列表]
  字段名             | 字段类型        | 约束                              | 描述
  --------------------+----------------+-----------------------------------+------------------------------
  summary_id          | str (uuid)     | 必填，唯一                        | 总结唯一标识
  video_id            | str            | 必填                              | 关联视频
  transcript_id       | str            | 必填                              | 关联转写稿
  markdown_body       | str            | 必填，UTF-8                       | Markdown 正文（含章节锚点 + 截图引用）
  front_matter        | Dict[str, Any] | 必填，video_ 前缀统一             | YAML 解析后的字典 [PRD:F-005.AC-4]
  style               | enum           | 必填                              | academic/casual/keypoints [PRD:F-015]
  model_used          | str            | 必填，"deepseek-v4-flash"          | 实际使用模型 [PRD:NF-010]
  fallback_used       | bool           | 默认 False                        | 是否启用 fallback [PRD:F-013]
  input_tokens        | int            | 必填，≤ 20000                     | LLM 输入 token [PRD:F-005.AC-2]
  output_tokens       | int            | 必填，≤ 4000                      | LLM 输出 token [PRD:F-005.AC-2]
  duration_ms         | int            | 必填                              | LLM 调用耗时
  chapters_count      | int            | 必填                              | 章节数（用于触发降级判断）[PRD:F-010]
  yaml_parse_ok       | bool           | 必填                              | YAML 解析是否成功
  created_at          | datetime       | 必填                              | 总结时间
[实体关系]
  DE-006 → DE-005 (N:1) ：基于某个转写稿
  DE-006 → DE-007 (1:N) ：含多个 Chapter（解析后）
  DE-006 → DE-009 (1:1) ：注入 PipelineNote
[来源标注] [PRD:F-005/F-013/F-015/NF-010] [调研:S-003/S-005/S-008] [SA推断:input_tokens/output_tokens/duration_ms 为可观测性字段]
```

---

### DE-007 Chapter

```
[实体编号] DE-007
[实体名称] 视频章节
[实体描述] 单个章节对象，含标题、起止时间戳和 Markdown 锚点。30-60 字。
[字段列表]
  字段名             | 字段类型        | 约束                              | 描述
  --------------------+----------------+-----------------------------------+------------------------------
  chapter_id          | str (uuid)     | 必填，唯一                        | 章节唯一标识
  video_id            | str            | 必填                              | 关联视频
  index               | int            | 必填，≥ 0                         | 章节序号
  title               | str            | 必填                              | 章节标题
  start_ts            | str (hh:mm:ss) | 必填，格式校验 [PRD:F-010.AC-2]    | 开始时间戳
  end_ts              | str (hh:mm:ss) | 必填                              | 结束时间戳
  start_ms            | int            | 必填，≤ total_duration            | 毫秒级开始（用于校验越界）[调研:RR-012]
  end_ms              | int            | 必填，≤ total_duration            | 毫秒级结束
  anchor              | str            | 必填                              | Markdown 锚点 [hh:mm:ss]
  is_fallback         | bool           | 默认 False                        | 是否为等距切片降级 [PRD:F-010.AC-2]
  source              | enum           | 必填                              | llm/equidistant/single_full [SA推断:来源枚举]
[实体关系]
  DE-007 → DE-002 (N:1) ：属于某个视频
  DE-007 → DE-005 (N:1) ：可对应转写稿段落
[来源标注] [PRD:F-010] [调研:S-202/RR-012] [SA推断:source 枚举用于追溯]
```

---

### DE-008 ScreenshotFrame

```
[实体编号] DE-008
[实体名称] 关键帧截图
[实体描述] ffmpeg 抽取的关键帧截图，含时间戳和大小约束。30-60 字。
[字段列表]
  字段名             | 字段类型        | 约束                              | 描述
  --------------------+----------------+-----------------------------------+------------------------------
  frame_id            | str (uuid)     | 必填，唯一                        | 截图唯一标识
  video_id            | str            | 必填                              | 关联视频
  file_path           | Path           | 必填                              | 截图文件路径
  timestamp_ms        | int            | 必填                              | 在视频中的时间位置
  size_kb             | int            | 必填，≤ 200                       | 文件大小（KB）[PRD:F-018.AC-1]
  is_compressed       | bool           | 默认 False                        | 是否被自动压缩 [SA推断:压缩标记]
  index               | int            | 必填，0-4                         | 序号（≤ 5 张）[PRD:F-018.AC-1]
[实体关系]
  DE-008 → DE-002 (N:1) ：属于某个视频
[来源标注] [PRD:F-018] [调研:S-201] [SA推断:is_compressed 用于监控]
```

---

### DE-009 PipelineNote

```
[实体编号] DE-009
[实体名称] 管道笔记文件
[实体描述] 落 raw/ 的 Markdown 文件，触发既有 5 阶段管道。30-70 字。
[字段列表]
  字段名             | 字段类型        | 约束                              | 描述
  --------------------+----------------+-----------------------------------+------------------------------
  note_id             | str            | 必填，等于 video_id               | 笔记唯一标识
  topic               | str            | 必填                              | 主题（路径前缀）
  file_path           | Path           | 必填，raw/<topic>/<video-id>.md   | 落盘路径 [PRD:F-006.AC-1]
  front_matter        | Dict[str, Any] | 必填                              | video_ 前缀字段集
  markdown_body       | str            | 必填                              | 完整 Markdown（含 front matter）
  schema_valid        | bool           | 必填                              | 是否通过既有管道 schema 校验 [PRD:F-006.AC-2]
  pipe_stages_run     | List[str]      | 必填                              | 5 阶段实际跑过的列表
  pipe_success        | bool           | 必填                              | 5 阶段是否全跑通
  tags                | List[str]      | 默认 []                           | 知识树 tags [PRD:F-016]
  video_source_url    | str            | 必填                              | 报告引用源 [PRD:F-020]
  created_at          | datetime       | 必填                              | 落盘时间
[实体关系]
  DE-009 → DE-013 (1:N) ：含多个知识树 tag
  DE-009 → DE-002 (1:1) ：含 video meta
[来源标注] [PRD:F-006/F-016/F-020] [调研:S-005]
```

---

### DE-010 ErrorRecord

```
[实体编号] DE-010
[实体名称] 错误记录
[实体描述] 错误码登记与中间产物保留对象，支持 3 段式错误信息。30-60 字。
[字段列表]
  字段名             | 字段类型        | 约束                              | 描述
  --------------------+----------------+-----------------------------------+------------------------------
  error_id            | str (uuid)     | 必填，唯一                        | 错误记录唯一标识
  task_id             | str            | 必填                              | 关联任务
  code                | str            | 必填                              | E_DL_001 / E_DL_001_DENO_MISSING / E_DL_BILI_403 / E_DL_002_VERSION_TOO_OLD / E_TR_001 / E_LLM_001 / E_LLM_002_CHAPTERS_FALLBACK / E_CK_001 / E_PIPE_001 / E_DL_META_001 / E_FM_001 / E_SYS_001
  phenomenon          | str            | 必填                              | 现象描述（错误信息第 1 段）[PRD:NF-003]
  cause               | str            | 必填                              | 原因分析（第 2 段）[PRD:NF-003]
  suggestion          | str            | 必填                              | 下一步建议（第 3 段）[PRD:NF-003]
  intermediate_files  | List[Path]     | 必填                              | 中间产物路径（音频/转写稿/部分 front matter）[PRD:F-011.AC-1]
  raw_traceback_hash  | Optional[str]  | 调试用                            | 堆栈哈希
  retry_count         | int            | 默认 0                            | 重试次数
  occurred_at         | datetime       | 必填                              | 发生时间
  cli_exit_code       | int            | 必填，≠ 0                         | CLI 退出码
[实体关系]
  DE-010 → DE-001 (N:1) ：关联 URL
  DE-010 → DE-003 (1:1) ：关联下载任务
[来源标注] [PRD:F-011/NF-003/NF-009] [调研:错误码字典]
```

---

### DE-011 CookieConfig

```
[实体编号] DE-011
[实体名称] Cookie 配置
[实体描述] 用户提供的 Cookie 文件解析结果，用于突破登录视频。30-60 字。
[字段列表]
  字段名             | 字段类型        | 约束                              | 描述
  --------------------+----------------+-----------------------------------+------------------------------
  cookie_id           | str (uuid)     | 必填，唯一                        | 配置唯一标识
  file_path           | Path           | 必填                              | Cookie 文件路径 [PRD:F-012]
  file_format         | enum           | 必填                              | netscape/cookies_txt
  file_size_bytes     | int            | 必填                              | 文件大小
  file_permission     | int (octal)    | 必填，== 0o600                    | 文件权限 [PRD:F-012.AC-4]
  parsed_ok           | bool           | 必填                              | 是否解析成功
  domain_count        | int            | 必填，> 0                         | 解析出的域数
  is_template         | bool           | 默认 False                        | 是否为默认空模板 [PRD:F-012.AC-2]
[实体关系]
  DE-011 → DE-003 (1:N) ：被下载任务引用
[来源标注] [PRD:F-012] [调研:S-001/S-101]
```

---

### DE-012 PreflightReport

```
[实体编号] DE-012
[实体名称] 运行环境预检报告
[实体描述] preflight check 4 项检查结果，决定后续下载是否继续。30-60 字。
[字段列表]
  字段名             | 字段类型        | 约束                              | 描述
  --------------------+----------------+-----------------------------------+------------------------------
  report_id           | str (uuid)     | 必填，唯一                        | 报告唯一标识
  deno_version        | Optional[str]  | 缺则 null                        | Deno 版本号
  deno_pass           | bool           | 必填                              | ≥ 2.0 ？
  node_version        | Optional[str]  | 缺则 null                        | Node 版本号
  node_pass           | bool           | 必填                              | ≥ 18.0.0 ？
  ffmpeg_version      | Optional[str]  | 缺则 null                        | ffmpeg 版本
  ffmpeg_pass         | bool           | 必填                              | ≥ 6.0 ？
  whisper_model_path  | Optional[Path] | 缺则 null                        | Whisper medium 模型路径
  whisper_model_pass  | bool           | 必填                              | 模型文件存在？
  all_pass            | bool           | 必填                              | 4 项全通过？
  missing_items       | List[str]      | 默认 []                           | 缺失项清单（用于打印安装命令）[PRD:F-003.AC-2]
  checked_at          | datetime       | 必填                              | 检查时间
  ttl_seconds         | int            | 默认 60                           | 报告有效期（并发任务共享）[SA洞察#1]
[实体关系]
  DE-012 → DE-001 (1:N) ：作为 BP-002 输出
[来源标注] [PRD:G-007/AC-E2E-08/F-003.AC-2] [调研:S-002/S-007] [SA洞察#1:并发共享状态 TTL]
```

---

### DE-013 KnowledgeTag

```
[实体编号] DE-013
[实体名称] 知识树标签
[实体描述] 知识树合并用的 tag，支持跨视频并集去重。30-50 字。
[字段列表]
  字段名             | 字段类型        | 约束                              | 描述
  --------------------+----------------+-----------------------------------+------------------------------
  tag_id              | str (uuid)     | 必填，唯一                        | tag 唯一标识
  topic               | str            | 必填                              | 主题（与 raw/ 路径对齐）
  tag_name            | str            | 必填                              | tag 名
  source_video_ids    | List[str]      | 必填                              | 来源视频 ID 列表（用于追溯）
  is_duplicate_resolved | bool         | 默认 False                        | 是否处理过冲突 [SA推断:冲突标记]
  created_at          | datetime       | 必填                              | 创建时间
  updated_at          | datetime       | 必填                              | 更新时间
[实体关系]
  DE-013 → DE-009 (N:1) ：被笔记引用
  DE-013 → DE-001 (N:M) ：跨视频共享
[来源标注] [PRD:F-016] [SA推断:is_duplicate_resolved 用于冲突追溯]
```

---

### DE-014 LogEntry

```
[实体编号] DE-014
[实体名称] 结构化日志条目
[实体描述] JSON Lines 格式日志，供可观测性使用。20-40 字。
[字段列表]
  字段名             | 字段类型        | 约束                              | 描述
  --------------------+----------------+-----------------------------------+------------------------------
  ts                  | datetime       | 必填                              | 时间戳
  level               | enum           | 必填                              | DEBUG/INFO/WARN/ERROR
  module              | str            | 必填                              | 模块名
  task_id             | str            | 必填                              | 关联任务
  url_hash            | str (64)       | 必填                              | URL 哈希
  step                | str            | 必填                              | 流程步骤
  duration_ms         | int            | 默认 0                            | 步骤耗时
  code                | Optional[str]  | 错误时填                          | 错误码
  msg                 | str            | 必填                              | 日志消息
[实体关系]
  DE-014 → DE-001 (N:1) ：关联 URL
[来源标注] [PRD:NF-007]
```

---

## 2. 数据实体关系图（ER 简化）

```
DE-001 (URL) ──1:1── DE-003 (DownloadTask)
DE-001 ──1:1── DE-004 (CacheEntry) ──1:1── DE-009 (PipelineNote)
DE-003 ──N:1── DE-011 (CookieConfig)
DE-005 (Transcript) ──1:1── DE-006 (LLMSummary)
DE-006 ──1:N── DE-007 (Chapter)
DE-002 (VideoMeta) ──1:N── DE-007
DE-002 ──1:N── DE-008 (ScreenshotFrame)
DE-006 ──1:1── DE-009 (PipelineNote)
DE-009 ──1:N── DE-013 (KnowledgeTag)
DE-010 (ErrorRecord) ──1:1── DE-003
DE-012 (PreflightReport) ──1:N── DE-001
DE-014 (LogEntry) ──N:1── DE-001
```

**数据引用完整率**：100%（16 流程 × 14 实体，所有引用均可追溯）。

---

## 3. SA 洞察（数据层）

1. **[数据孤岛风险]** DE-013 KnowledgeTag.source_video_ids 是反向引用——BP-010 落盘时未显式回填，导致 tags 追溯链断裂。**修复建议**：在 BP-010 步骤 4 显式写入 source_video_ids。[SA推断:反向引用未闭环]

2. **[字段命名一致性]** DE-006 LLMSummary.front_matter 字段名前缀依赖 LLM 自觉，缺少强校验。**修复建议**：在 notes_schema 模块加白名单校验：只接受 video_ 前缀字段，其他视为污染字段并触发 LLM 重试。[SA洞察#2 联动]

---

> **本文件结束**。14 实体全部覆盖 16 流程所有数据引用。
