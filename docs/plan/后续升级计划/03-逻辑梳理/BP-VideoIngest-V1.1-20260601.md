# 业务流程图 — VideoIngest V1.1（SA-001 终版）

> **生成方**：SA-001
> **日期**：2026-06-01
> **接收方**：TD-001（顶层设计师） / DD-001（详细设计）
> **风险等级图例**：**核心**=写操作/状态变更/数据创建  **标准**=读操作/查询/条件判断  **辅助**=日志/通知/记录
> **覆盖率**：PRD 20 功能 → 16 业务流程（含 1 拆分 F-007/F-015 各独立）；核心 8 / 标准 6 / 辅助 2

---

## 0. 来源标注图例

| 标注 | 含义 |
|------|------|
| `[PRD:F-NNN]` | 直接引用 PRD V1.1 第 3 章功能 F-NNN |
| `[PRD:NF-NNN]` | 引用 PRD V1.1 第 4 章非功能需求 |
| `[调研:S-NNN]` | 引用 RA-001 调研建议 S-NNN（V1.1 已 100% 采纳） |
| `[SA推断:依据]` | SA 基于上下文的高置信推断，附依据 |

---

## 1. PRD 功能 → 业务流程映射总览

| PRD 功能 | 业务名称 | 风险等级 | 触发条件 | 关联数据实体 | 关联异常 |
|----------|----------|----------|----------|--------------|----------|
| F-001 / F-002 | BP-001 CLI 入参与平台识别 | 标准 | 用户执行 `research run --video-url <url>` | DE-001 VideoURL | EX-001~EX-003 |
| F-003 / NF-008 / G-007 | BP-002 preflight check | 核心 | CLI 启动 | DE-012 PreflightReport | EX-004~EX-007 |
| F-003 / F-007 | BP-003 视频下载 | 核心 | URL 合法 + preflight 通过 | DE-003 DownloadTask, DE-011 CookieConfig | EX-008~EX-013 |
| F-009 | BP-004 缓存查询/写入 | 核心 | 入参校验后 | DE-004 CacheEntry | EX-014~EX-016 |
| F-004 / F-014 | BP-005 视频转写 | 核心 | 音频文件就绪 | DE-005 Transcript | EX-017~EX-020 |
| F-005 / F-013 / F-015 | BP-006 LLM 总结 | 核心 | 转写稿就绪 | DE-006 LLMSummary | EX-021~EX-024 |
| F-010 | BP-007 章节切分与降级 | 标准 | LLM 总结完成 | DE-007 Chapter | EX-025~EX-027 |
| F-017 | BP-008 视频元信息抓取 | 标准 | URL 解析后 | DE-002 VideoMeta | EX-028~EX-029 |
| F-018 | BP-009 关键帧截图 | 辅助 | LLM 总结完成 | DE-008 ScreenshotFrame | EX-030 |
| F-006 / F-016 | BP-010 管道适配与落盘 | 核心 | 笔记内容构造完毕 | DE-009 PipelineNote, DE-013 KnowledgeTag | EX-031~EX-033 |
| F-008 | BP-011 多视频批量并发 | 标准 | 多 URL 入参 | DE-003 DownloadTask×N | EX-034~EX-036 |
| F-011 | BP-012 错误降级与错误码体系 | 核心 | 任意步骤失败 | DE-010 ErrorRecord | EX-037~EX-039 |
| F-019 | BP-013 AI 问答 RAG | 标准 | 用户执行 `research ask` | DE-006 LLMSummary, DE-013 KnowledgeTag | EX-040~EX-041 |
| F-020 | BP-014 报告引用视频源 | 辅助 | 报告段落生成 | DE-002 VideoMeta | EX-042 |
| F-007 | BP-015 本地视频文件入参 | 标准 | 用户提供 `--video-file` | DE-001 VideoURL（本地路径变体） | EX-043~EX-044 |
| NF-007 | BP-016 日志记录（结构化） | 辅助 | 任意步骤 | DE-014 LogEntry | EX-045 |

**统计**：核心 8 / 标准 6 / 辅助 2 = 16 流程（覆盖 20 PRD 功能 100%）。

---

## 2. 业务流程详图

---

### BP-001 CLI 入参与平台识别

```
[流程编号] BP-001
[流程名称] CLI 入参与平台识别
[风险等级] 标准
[触发条件] 用户在终端执行 `research run --video-url <url>` 或 `--video-file <path>`
[步骤序列]
  1. 用户 → 执行 CLI 命令（research run ... --video-url <url>） → 触发 argparse
  2. cli_bindings → 解析 argv，校验必填项 → DE-001 VideoURL 对象
  3. cli_bindings → 调用 platform_detect(url) → 平台标识 bilibili / youtube / local
  4. cli_bindings → CLI 启动时强制覆盖 LLMConfig.model = "deepseek-v4-flash" [PRD:NF-010][调研:S-003]
  5. cli_bindings → 触发 BP-002 preflight check
[分支条件]
  - URL 形态合法 → 进入 BP-002
  - URL 形态不合法 → 立即返回 E_DL_001（1s 内，含"现象+原因+下一步"）[PRD:AC-E2E-04]
  - URL 命中 5 种已知形态（BV 号 / bilibili.com/video/ / youtu.be / youtube.com/watch?v= / 多视频逗号分隔）→ 100% 识别 [PRD:F-002.AC-1]
[结束条件]
  正常结束：返回平台标识 + 进入 BP-002
  异常结束：E_DL_001 / E_DL_001_DENO_MISSING
[关联数据] DE-001, DE-014
[关联异常] EX-001（非法 URL）, EX-002（不支持平台）, EX-003（CLI 参数冲突）
[来源标注] [PRD:F-001/F-002/NF-010]
```

---

### BP-002 preflight check（**V1.1 新增**，核心）

```
[流程编号] BP-002
[流程名称] 运行环境预检
[风险等级] 核心（写操作：生成环境报告 + 决定后续是否进入下载）
[触发条件] CLI 启动后、首次下载前
[步骤序列]
  1. preflight → spawn 子进程执行 `deno --version`，解析 ≥ 2.0 ？ [PRD:F-003.AC-2][调研:S-002]
  2. preflight → spawn `node --version`，解析 ≥ 18.0.0 [调研:S-007]
  3. preflight → spawn `ffmpeg -version`，解析 ≥ 6.0
  4. preflight → 检查 ~/.cache/huggingface/hub/models--Systran--faster-whisper-medium 是否存在
  5. preflight → 拼装 DE-012 PreflightReport{deno, node, ffmpeg, whisper_model, all_pass}
  6. cli_bindings → 若 all_pass=true → 进入 BP-003
  7. cli_bindings → 若 all_pass=false → 标记 missing_items，YouTube 下载返回 E_DL_001_DENO_MISSING 并打印 `curl -fsSL https://deno.land/install.sh | sh` 安装命令 [PRD:F-003.AC-2]
[分支条件]
  - Deno 缺失 + 任务含 YouTube URL → E_DL_001_DENO_MISSING（阻塞 YouTube 任务）[调研:S-002][RA推理:Q-008 解决路径]
  - Deno 缺失 + 任务仅 B 站 → 警告但不阻塞（B 站走 yt-dlp 不依赖 Deno）
  - 缺依赖拍 → 第⑧棒在产物日志显式标注 [SIM-STUB]，不掩盖 [PRD:AC-E2E-08][调研:S-007]
[结束条件]
  正常结束：PreflightReport 写入日志 + 进入 BP-003
  异常结束：返回 E_DL_001_DENO_MISSING（B 站可继续）
[关联数据] DE-012 PreflightReport
[关联异常] EX-004（Deno 缺失）, EX-005（ffmpeg 缺失）, EX-006（Whisper 模型缺失）, EX-007（node 缺失）
[来源标注] [PRD:G-007/AC-E2E-08/F-003.AC-2] [调研:S-002/S-007] [RA推理:Q-008 默认假设=Deno 不预装]
```

---

### BP-003 视频下载

```
[流程编号] BP-003
[流程名称] 视频下载
[风险等级] 核心
[触发条件] preflight 通过 + URL 合法
[步骤序列]
  1. downloader.bilibili / downloader.youtube → 读取 DE-001 VideoURL 与可选 DE-011 CookieConfig
  2. downloader → 校验 yt-dlp 版本 ≥ 2023.07.06（CVE-2023-35934 修复）[PRD:NF-004][调研:S-101]
  3. downloader → 调用 yt-dlp 包装层下载音视频（1080p 优先）+ 内嵌/外挂字幕 [PRD:F-007 限制：不支持 m3u8]
  4. downloader → 写入 DE-003 DownloadTask{url, status, file_path, etag, last_modified, started_at, finished_at}
  5. downloader → 验证文件落盘 + 大小 > 0
  6. downloader → 触发 BP-004 缓存键计算
[分支条件]
  - 4K/付费/限地区 → 提示用户提供 --cookie-file → 1 次后立即返回 E_DL_BILI_403 [PRD:F-003.AC-3][调研:S-001]
  - B 站未带字幕 + 字幕 API 可拉 → transcriber.subtitle 兜底 [PRD:BiliNote 集成边界]
  - 403/412 多次重试（≤2 次，指数退避 2^n）→ 返回 E_DL_001 [PRD:NF-009]
  - YouTube 无 Deno → E_DL_001_DENO_MISSING（已在 BP-002 阻断）
[结束条件]
  正常结束：DownloadTask 写入 + 媒体文件落 raw/<topic>/assets/
  异常结束：E_DL_001 / E_DL_BILI_403 / E_DL_001_DENO_MISSING / E_DL_002_VERSION_TOO_OLD
[关联数据] DE-001, DE-003, DE-011
[关联异常] EX-008（4K/限地区）, EX-009（yt-dlp 版本过低）, EX-010（403/412 多次失败）, EX-011（磁盘满）, EX-012（网络断开）, EX-013（不支持 m3u8）
[来源标注] [PRD:F-003/F-007/F-012/NF-004/NF-009] [调研:S-001/S-002/S-101]
```

---

### BP-004 缓存查询/写入

```
[流程编号] BP-004
[流程名称] 缓存查询/写入
[风险等级] 核心（写操作：缓存命中/失效状态变更 + 文件系统写入）
[触发条件] DownloadTask 落盘后或入参校验后
[步骤序列]
  1. cache_manager → 计算 cache_key = sha256(url) + ":" + (etag or last_modified or "no-version") [PRD:F-009.AC-2][调研:S-102]
  2. cache_manager → 查询 cache_db（sqlite/redis 二选一，由 cli_bindings 决定）
     - 命中：读取缓存的 DE-006 LLMSummary 路径 + 触发 BP-010 落盘
     - 未命中：进入 BP-005
  3. cache_manager → 写入新缓存条目：cache_key, file_path, summary_path, created_at, ttl（默认 30 天）[SA推断:TTL 默认 30 天，PRD 未明确]
  4. cli_bindings → 若 --no-cache 强制刷新 → 跳过查询步骤直接进入 BP-005 [PRD:F-009.AC-3]
[分支条件]
  - sha256(url) 命中 + etag 缺失 → 仅用 sha256(url) 作为降级键（避免命中率下降）[调研:S-102 RA 推理隐含]
  - 命中 + 用户期望刷新 → 走 --no-cache
[结束条件]
  正常结束：缓存命中返回缓存笔记 / 未命中进入 BP-005
  异常结束：缓存读写失败 → 降级为不走缓存（不阻塞主流程）[SA推断:缓存失败不应阻塞主流程]
[关联数据] DE-004 CacheEntry
[关联异常] EX-014（缓存 DB 不可用）, EX-015（缓存条目损坏）, EX-016（陈旧笔记命中）
[来源标注] [PRD:F-009] [调研:S-102] [SA推断:TTL=30天/缓存失败降级不阻塞]
```

---

### BP-005 视频转写

```
[流程编号] BP-005
[流程名称] 视频转写
[风险等级] 核心
[触发条件] DownloadTask 音频文件就绪 + 缓存未命中
[步骤序列]
  1. transcriber → 读取 DE-001 url 选择转写引擎：whisper（默认） / bcut / groq [PRD:F-014]
  2. cli_bindings → 检测 RAM < 8G → 强制 model_size='base' 或 'small'；否则默认 'medium' [PRD:F-014.AC-2][调研:S-004]
  3. transcriber → 二次入参命中缓存时（按 sha256(audio_fingerprint)）→ 直接返回 DE-005 Transcript [PRD:F-004.AC-3]
  4. transcriber → 调用 faster-whisper / bcut-asr / groq-whisper API → List[Segment]{start, end, text}
  5. transcriber → 写入 DE-005 Transcript{segments, total_duration, model_size, created_at}
  6. transcriber → 校验 CER 阈值（如超时返回 OOM 风险）[调研:RR-003]
[分支条件]
  - Whisper 不可用 → 切 bcut → 仍失败切 groq API → 返回 E_TR_001 [PRD:F-014.AC-1]
  - 30 分钟视频 + medium 档 → 预计 ≤ 5 分钟完成 [PRD:F-004.AC-1]
  - 方言/口音 → medium CER 4-8% 仍可用；粤语 38.97% CER 触发降级提醒 [调研:RR-003]
[结束条件]
  正常结束：Transcript 写入 + 进入 BP-006
  异常结束：E_TR_001（whisper 不可用）/ E_TR_002（OOM）/ E_TR_003（音频解码失败）
[关联数据] DE-005 Transcript
[关联异常] EX-017（Whisper 不可用）, EX-018（OOM）, EX-019（音频损坏）, EX-020（方言 CER 超阈）
[来源标注] [PRD:F-004/F-014] [调研:S-004/RR-003]
```

---

### BP-006 LLM 总结

```
[流程编号] BP-006
[流程名称] LLM 总结为 Markdown+YAML
[风险等级] 核心（写操作：生成 LLM 响应内容 + 写回缓存）
[触发条件] Transcript 就绪
[步骤序列]
  1. llm_client → 校验 LLMConfig.model == "deepseek-v4-flash"（CLI 启动时已强制覆盖）[PRD:NF-010][调研:S-003]
  2. llm_client → 选择 style prompt：academic / casual / keypoints [PRD:F-015]
  3. llm_client → 构造 prompt = system_prompt + transcript + style + 章节要求（≥ 3 章节 + hh:mm:ss 锚点）
  4. llm_client → 单次调用 ≤ 20k input / 4k output（30 分钟视频典型 8-15k 转写稿 + 4k prompt ≈ 12-20k）[PRD:F-005.AC-2][调研:S-008]
  5. llm_client → 接收 LLM 响应：Markdown 正文 + YAML front matter（含 video_ 前缀字段集）[PRD:F-005.AC-4][调研:S-005]
  6. llm_client → 解析 front matter（YAML safe_load）→ 校验所有 video_ 字段齐全 [SA推断:字段缺失时 LLM 自动重试 1 次]
  7. llm_client → 连续 3 次 5xx → 切换 qwen-turbo（fallback）[PRD:F-013.AC-1]
  8. llm_client → 写入 DE-006 LLMSummary{markdown, front_matter, style, model_used, created_at}
[分支条件]
  - 输入超过 20k → 截断 + 警告（不切分，保证章节连贯性）[PM决策:选项 A][调研:S-008]
  - LLM 连续 3 次 5xx → 切换 qwen-turbo（不超过 1 次切换）
  - LLM 章节 < 3 → 触发 BP-007 降级 [PRD:F-010.AC-2/3][调研:S-202]
[结束条件]
  正常结束：LLMSummary 写入 + 进入 BP-007
  异常结束：E_LLM_001（连续 3 次失败）/ E_LLM_003（输出超 budget）
[关联数据] DE-002 VideoMeta, DE-005 Transcript, DE-006 LLMSummary
[关联异常] EX-021（5xx 多次失败）, EX-022（输出超 budget）, EX-023（YAML 解析失败）, EX-024（fallback 不可用）
[来源标注] [PRD:F-005/F-013/F-015/NF-010] [调研:S-003/S-005/S-008] [PM决策:选项 A]
```

---

### BP-007 章节切分与降级

```
[流程编号] BP-007
[流程名称] 章节切分与降级
[风险等级] 标准
[触发条件] LLMSummary 生成完毕
[步骤序列]
  1. notes_schema → 解析 front matter.video_chapters[] → 计数 chapters_count
  2. notes_schema → 校验每章节 hh:mm:ss ≤ 视频总时长（越界裁剪/丢弃）[调研:RR-012]
  3. notes_schema → 若 chapters_count ≥ 3 → 保留；进入 BP-008
  4. notes_schema → 若 chapters_count < 3 → 触发降级：
     a. 按 5 分钟等距切片生成章节
     b. 仍不足 3 章节 → 1 章节兜底（章节时间 = 视频全程）[PRD:F-010.AC-2][调研:S-202]
  5. notes_schema → 触发降级时返回 E_LLM_002_CHAPTERS_FALLBACK [PRD:F-010.AC-3]
  6. notes_schema → 注入正文 [hh:mm:ss] 锚点（与章节一一对应）
[分支条件]
  - LLM 输出 0 章节（纯音乐/空白视频）→ 等距切片兜底
  - 章节时间戳格式错误 → 跳过该章节，保留有效章节
[结束条件]
  正常结束：video_chapters[] 注入 front matter + 进入 BP-008
  异常结束：E_LLM_002_CHAPTERS_FALLBACK（警告级别，不阻塞）
[关联数据] DE-007 Chapter, DE-002 VideoMeta
[关联异常] EX-025（章节 < 3 触发降级）, EX-026（时间戳越界）, EX-027（空转写稿幻觉）
[来源标注] [PRD:F-010] [调研:S-202/RR-012] [RA推理:LLM 空白视频幻觉]
```

---

### BP-008 视频元信息抓取

```
[流程编号] BP-008
[流程名称] 视频元信息抓取
[风险等级] 标准
[触发条件] URL 解析后即可启动（与下载并行）
[步骤序列]
  1. downloader → 调用 yt-dlp --dump-json 或 B 站 API 抓取元信息
  2. notes_schema → 构造 DE-002 VideoMeta{video_id, video_source_url, video_title, video_author, video_duration, video_cover_url, video_tags, video_created_at, video_platform}
  3. notes_schema → 校验 front matter 字段数 ≥ 6 [PRD:F-017.AC-1]
  4. notes_schema → 缺失字段以 null 占位（不阻塞主流程）[SA推断:缺失字段不阻塞]
[分支条件]
  - 抓取失败 → 元信息部分缺失，front matter 保留已有字段
[结束条件]
  正常结束：VideoMeta 注入 front matter
  异常结束：E_DL_META_001（仅警告，不阻塞主流程）
[关联数据] DE-002 VideoMeta
[关联异常] EX-028（API 限流）, EX-029（部分字段缺失）
[来源标注] [PRD:F-017] [调研:第 9 章 notes_schema 字段集]
```

---

### BP-009 关键帧截图

```
[流程编号] BP-009
[流程名称] 关键帧截图
[风险等级] 辅助
[触发条件] LLM 总结完成 + 视频时长 > 1 分钟
[步骤序列]
  1. ffmpeg_wrapper → 接收视频文件路径
  2. ffmpeg_wrapper → 执行 ffmpeg -i input -vf "select='eq(pict_type,I)'" -vsync vfr -frames:v 5 output_%02d.jpg [PRD:F-018.AC-2][调研:S-201]
  3. ffmpeg_wrapper → 校验每张图 ≤ 200KB [PRD:F-018.AC-1]
  4. ffmpeg_wrapper → 写入 DE-008 ScreenshotFrame{file_path, timestamp, size_kb, video_id}
  5. notes_schema → 注入 Markdown 正文 ![](path/to/frame.jpg)
[分支条件]
  - 截图超 200KB → 自动压缩到 ≤ 200KB
  - ffmpeg 不可用 → 跳过截图（不阻塞主流程）[SA推断:截图失败不阻塞]
[结束条件]
  正常结束：5 张截图嵌入 Markdown
  异常结束：E_FM_001（ffmpeg 失败）/ 静默跳过
[关联数据] DE-008 ScreenshotFrame
[关联异常] EX-030（ffmpeg 调用失败 / 截图超 200KB）
[来源标注] [PRD:F-018] [调研:S-201]
```

---

### BP-010 管道适配与落盘

```
[流程编号] BP-010
[流程名称] 管道适配与落盘
[风险等级] 核心（写操作：文件系统 + 触发下游管道）
[触发条件] 笔记内容（LLMSummary + VideoMeta + Chapters + Screenshots）构造完毕
[步骤序列]
  1. pipeline_adapter → 拼装 Markdown：YAML front matter + 正文（章节锚点 + 截图）
  2. pipeline_adapter → 校验 video_ 前缀字段统一 [PRD:F-006.AC-2][调研:S-005]
  3. pipeline_adapter → 写入 raw/<topic>/<video-id>.md [PRD:F-006.AC-1]
  4. pipeline_adapter → 触发既有 5 阶段管道（Collect → Clean → Extract → Organize → Report）
  5. knowledge_tree → 合并 tags 并集（同主题）[PRD:F-016.AC-1]
  6. knowledge_tree → 校验 tags 不重复
[分支条件]
  - 既有管道未跑通 → 重试 1 次，仍失败返回 E_PIPE_001
  - tags 冲突 → 保留两版，标记 [duplicate_resolved]
[结束条件]
  正常结束：raw/<topic>/<video-id>.md 落盘 + 5 阶段管道自动跑通
  异常结束：E_PIPE_001（管道失败）
[关联数据] DE-009 PipelineNote, DE-013 KnowledgeTag, DE-002 VideoMeta, DE-006 LLMSummary, DE-007 Chapter, DE-008 ScreenshotFrame
[关联异常] EX-031（raw 写入失败）, EX-032（管道触发失败）, EX-033（tags 冲突）
[来源标注] [PRD:F-006/F-016] [调研:S-005] [SA推断:管道失败重试 1 次]
```

---

### BP-011 多视频批量并发

```
[流程编号] BP-011
[流程名称] 多视频批量并发
[风险等级] 标准
[触发条件] 入参含多个 URL（逗号分隔）
[步骤序列]
  1. cli_bindings → 拆分 URL 列表 → 任务队列（最多 3 并发）[PRD:F-008.AC-1]
  2. asyncio.gather → 对每个 URL 并发执行 BP-002 → BP-003 → BP-004 → BP-005 → BP-006 → BP-007 → BP-008 → BP-009 → BP-010
  3. cli_bindings → 等待所有任务结束 → 汇总报告
  4. cli_bindings → 校验：3 个 30 分钟视频 ≤ 12 分钟 [PRD:F-008.AC-1]
[分支条件]
  - 并发数 > 3 → 仅取前 3 排队，其余待前批完成
  - 任一任务失败 → 其他任务继续，最终汇总错误码
  - 超过 10 个 URL → 提示"v1.1 队列上限 10" [PRD:F-008 边界]
[结束条件]
  正常结束：N 份 raw/<topic>/<video-id>.md 落盘
  异常结束：部分任务失败，返回失败列表
[关联数据] DE-003 DownloadTask×N
[关联异常] EX-034（并发资源耗尽）, EX-035（部分任务失败）, EX-036（URL 数量 > 10）
[来源标注] [PRD:F-008]
```

---

### BP-012 错误降级与错误码体系

```
[流程编号] BP-012
[流程名称] 错误降级与错误码体系
[风险等级] 核心（写操作：错误码登记 + 中间产物保留）
[触发条件] 任意流程 BP-001~BP-011 失败
[步骤序列]
  1. error_handler → 捕获异常 → 匹配错误码字典：
     E_DL_001 / E_DL_001_DENO_MISSING / E_DL_BILI_403 / E_DL_002_VERSION_TOO_OLD
     E_TR_001
     E_LLM_001 / E_LLM_002_CHAPTERS_FALLBACK
     E_CK_001
     E_PIPE_001 / E_DL_META_001 / E_FM_001
  2. error_handler → 构造 DE-010 ErrorRecord{code, message, suggestion, intermediate_files, raw_traceback_hash}
  3. error_handler → 保留中间产物（音频/转写稿/部分 front matter）[PRD:F-011.AC-1]
  4. error_handler → 输出 "现象 + 原因 + 下一步" 3 段式错误信息 [PRD:NF-003]
  5. error_handler → CLI 退出码：非 0 + 错误码字典
[分支条件]
  - 重试 ≤ 2 次（指数退避 2^n）[PRD:NF-009]
  - LLM 5xx → 切 qwen-turbo（已记录在 BP-006）
[结束条件]
  正常结束：错误码登记 + 中间产物保留 + CLI 退出
  异常结束：未捕获异常 → E_SYS_001 + 完整堆栈
[关联数据] DE-010 ErrorRecord
[关联异常] EX-037（错误码解析失败）, EX-038（中间产物丢失）, EX-039（CLI 退出码错乱）
[来源标注] [PRD:F-011/NF-003/NF-009] [调研:错误码字典完整]
```

---

### BP-013 AI 问答 RAG

```
[流程编号] BP-013
[流程名称] AI 问答 RAG（基于本次视频）
[风险等级] 标准
[触发条件] 用户执行 `research ask "<query>"` 且本次会话含视频转写
[步骤序列]
  1. rag_engine → 检索本次视频 DE-005 Transcript + DE-006 LLMSummary 作为上下文
  2. llm_client → 调用 deepseek-v4-flash（保持模型一致）[PRD:NF-010]
  3. llm_client → 注入引用标记：## 参考来源含 video_source_url [PRD:F-019.AC-1]
  4. llm_client → 输出答案
[分支条件]
  - 上下文 > 20k → 摘要压缩（不切分）
  - 无视频上下文 → 退化为普通 RAG
[结束条件]
  正常结束：返回答案 + 引用源
  异常结束：E_LLM_001（同 BP-006）
[关联数据] DE-005, DE-006
[关联异常] EX-040（无视频上下文）, EX-041（LLM 失败）
[来源标注] [PRD:F-019] [调研:第 9 章 llm_client 复用]
```

---

### BP-014 报告引用视频源

```
[流程编号] BP-014
[流程名称] 报告段落自动引用视频源
[风险等级] 辅助
[触发条件] 报告段落生成（含视频内容）
[步骤序列]
  1. report_generator → 注入 video_ 前缀引用 → 报告 `## 参考来源` 段落 [PRD:F-020.AC-1]
  2. report_generator → 校验：原始链接（video_source_url）+ 作者 + 平台
[分支条件]
  - 报告不含视频 → 跳过
[结束条件]
  正常结束：报告含参考来源段
[关联数据] DE-002 VideoMeta
[关联异常] EX-042（链接丢失 / 字段缺失）
[来源标注] [PRD:F-020]
```

---

### BP-015 本地视频文件入参

```
[流程编号] BP-015
[流程名称] 本地视频文件入参
[风险等级] 标准
[触发条件] 用户提供 `--video-file <path>` (mp4/mkv/mov)
[步骤序列]
  1. cli_bindings → 校验文件存在 + 格式在白名单（mp4/mkv/mov）
  2. cli_bindings → 文件大小 ≤ 1GB [PRD:F-007.AC-1]
  3. cli_bindings → 计算 sha256(file) 作为内部 video_id
  4. transcriber → 跳过 BP-003，直接进入 BP-005 [SA推断:本地文件无需下载]
[分支条件]
  - m3u8/RTMP → 拒绝（v1.1 不支持）[PRD:B-002]
  - 文件 > 1GB → 拒绝 + 提示
[结束条件]
  正常结束：进入 BP-005 转写
  异常结束：E_DL_LOCAL_001（格式不支持/文件过大）
[关联数据] DE-001（变体：本地路径）, DE-005
[关联异常] EX-043（格式不支持）, EX-044（文件过大）
[来源标注] [PRD:F-007/B-002]
```

---

### BP-016 日志记录（结构化）

```
[流程编号] BP-016
[流程名称] 结构化日志记录
[风险等级] 辅助
[触发条件] 任意 BP 步骤
[步骤序列]
  1. logger → 写入 JSON Lines：{ts, level, module, task_id, url_hash, step, duration_ms, code, msg} [PRD:NF-007]
  2. logger → 写入 ~/.cache/research-tool/logs/<date>.jsonl
[分支条件]
  - 日志写入失败 → 仅打印 stderr，不阻塞主流程
[结束条件]
  正常结束：日志文件落盘
[关联数据] DE-014 LogEntry
[关联异常] EX-045（日志路径不可写）
[来源标注] [PRD:NF-007]
```

---

## 3. 流程间依赖与衔接图

```
BP-001 (CLI 解析)
  └─→ BP-002 (preflight)  [V1.1 新增]
        ├─ ok → BP-003 (下载)
        │       ├─→ BP-004 (缓存)
        │       │    ├─ hit → BP-010 (落盘)
        │       │    └─ miss → BP-005 (转写)
        │       │              └─→ BP-006 (LLM 总结)
        │       │                       └─→ BP-007 (章节)
        │       │                              └─→ BP-008 (元信息) [并行]
        │       │                                     └─→ BP-009 (截图)
        │       │                                            └─→ BP-010 (落盘)
        │       │                                                   └─→ BP-016 (日志)
        │       └─ fail → BP-012 (错误码)
        └─ fail (deno 缺失 + YouTube) → E_DL_001_DENO_MISSING
                                       └─→ BP-012 (错误码)

BP-011 (并发) 包裹上述主链 ×N
BP-013 (RAG)  ← 反向引用 BP-006 输出
BP-014 (引用) ← 报告生成阶段调用
BP-015 (本地) 跳过 BP-003
```

---

## 4. SA 洞察（本轮 ≥ 1 条）

1. **[隐含依赖·跨流程]** BP-002 preflight 报告（DE-012）是 BP-003 的硬性闸门，但 BP-011 并发场景下，3 个任务共享同一份 PreflightReport——若首个任务 YouTube 失败后用户中途安装 Deno，后续 2 个任务**仍按初始报告判定**。**修复建议**：每个任务在 BP-003 前重新拉取一次 preflight 状态（或 PreflightReport 设为短 TTL 缓存）。[SA推断:跨并发任务的共享状态时效性问题]

2. **[逻辑缺口·字段命名]** BP-006 LLM 输出 front matter 时若 LLM 漏写 video_ 前缀（直接写 "title" 而非 "video_title"），BP-010 当前无字段名校验——会污染 raw/。**修复建议**：在 BP-006 步骤 6 之后增加"字段名前缀强制校验"，缺失时 LLM 强制重试 1 次。[SA推断:LLM 字段名幻觉风险]

3. **[跨流程风险·缓存]** BP-004 缓存键 `sha256(url) + etag` 对 YouTube PO Token 频繁变更的视频可能在 24h 内多次失效（PO Token 轮换导致 etag 变化）——**建议**：对 YouTube 视频增加 24h 内强制 revalidate 标记（`etag_revalidate_at`）。[SA推断:YouTube etag 抖动 + 缓存命中率]

---

> **本文件结束**。16 流程全部覆盖 20 PRD 功能；8 核心流程每个将单独生成反例（见 CE-VideoIngest-V1.1-20260601.md）。
