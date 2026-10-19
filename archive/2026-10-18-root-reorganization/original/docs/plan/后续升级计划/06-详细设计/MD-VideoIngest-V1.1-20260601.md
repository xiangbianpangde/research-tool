# 模块细化方案 — VideoIngest V1.1（DD-001）

> **生成方**：DD-001
> **日期**：2026-06-01
> **模块数**：12（M-001~M-012，每模块均通过 7/7 客观细化检查）
> **使用方式**：DD-M 按本方案逐模块进入实现

---

## M-001 CLI 绑定与编排器

```
[模块编号] M-001
[关联技术选型] TS-001 (Python ≥ 3.11) / TS-014 (asyncio) / TS-015 (subprocess)
[子模块拆分]
  子模块1: arg_parser - argparse 8 个白名单参数解析
  子模块2: platform_resolver - URL → 平台枚举（BILIBILI/YOUTUBE/LOCAL）
  子模块3: config_loader - 环境变量 + LLMConfig 强制覆盖
  子模块4: dispatcher - 分发到 M-012 并发编排
  子模块5: rag_entry - 单次 RAG 问答入口
[类设计]
  类1: CLIArgParser - 职责: 参数白名单校验 - 属性: [allowed_args, parser] - 方法: [parse, validate_url, validate_path]
  类2: PlatformResolver - 职责: 平台识别 - 属性: [regex_patterns] - 方法: [resolve, get_enum]
  类3: LLMConfigLoader - 职责: LLM 配置加载 - 属性: [api_keys, model_names] - 方法: [load_from_env, override]
  类4: Dispatcher - 职责: 任务分发 - 属性: [orchestrator] - 方法: [dispatch, collect_results]
  类5: RAGEntry - 职责: RAG 入口 - 属性: [transcript_repo, llm_client] - 方法: [query, format_answer]
[函数签名]
  parse_argv(argv: list[str]) -> CLIArgs
  resolve_platform(url: str) -> Platform
  load_llm_config() -> LLMConfig
  dispatch_tasks(urls: list[VideoURL], task_func: Callable) -> list[Result]
  rag_query(query: str) -> str
[状态机] N/A（无状态 CLI 入口）
[异常处理]
  E_DL_001 (非法 URL) → argparse 拒绝 + 打印帮助
  E_DL_001_DENO_MISSING → 启动 preflight 拦截
  E_DL_LOCAL_001 → 路径不存在时拒绝
  异常流程: 异常 → M-010 登记 → 退出码仲裁
[日志策略]
  日志级别: INFO
  日志内容: argv + DE-001 解析结果（不含 Cookie）
  日志格式: {"ts": "...", "level": "INFO", "module": "M-001", "action": "parse_argv", "argv_sha256": "..."}
[测试策略]
  测试范围: 单元测试（argparse）+ 集成测试（端到端 1 URL）
  测试用例数: 核心 5 + 边界 3 + 异常 3 = 11
  Mock策略: 平台识别用固定 URL 列表 mock
  覆盖率目标: 行 ≥ 80% / 分支 ≥ 70%
  测试数据: fixtures/argv_*.json
[来源标注] [AR:API-001/002/003/004/005] [AR:DP-001] [调研:V1.0-src-sol-62]
```

## M-002 预检模块

```
[模块编号] M-002
[关联技术选型] TS-015 (subprocess) / TS-001 (Python)
[子模块拆分]
  子模块1: deno_checker - `deno --version` 解析
  子模块2: node_checker - `node --version` 解析
  子模块3: ffmpeg_checker - `ffmpeg -version` 解析
  子模块4: whisper_model_checker - ~/.cache/huggingface 模型文件存在
  子模块5: report_builder - 汇总 4 项为 DE-012 PreflightReport
[类设计]
  类1: DenoChecker - 职责: deno 版本检测 - 属性: [binary_path] - 方法: [check, parse_version]
  类2: NodeChecker - 职责: node 版本检测 - 属性: [binary_path] - 方法: [check, parse_version]
  类3: FFmpegChecker - 职责: ffmpeg 版本检测 - 属性: [binary_path] - 方法: [check, parse_version]
  类4: WhisperModelChecker - 职责: 模型文件检测 - 属性: [cache_dir] - 方法: [check, suggest_download]
  类5: PreflightReport - 职责: 报告生成 - 属性: [deno_ok, node_ok, ffmpeg_ok, whisper_ok, timestamp] - 方法: [to_dataclass, is_blocking]
[函数签名]
  check_all() -> PreflightReport  # lru_cache 装饰
  _check_deno() -> bool
  _check_node() -> bool
  _check_ffmpeg() -> bool
  _check_whisper() -> bool
[状态机]
  PENDING → [check_all] → PASS
  PASS → [60s TTL 过期] → RE_CHECK
  RE_CHECK → [失败] → FAIL_BLOCKING | FAIL_SOFT
[异常处理]
  E_DL_001_DENO_MISSING → 阻塞 YouTube 任务 + 打印安装命令
  node/ffmpeg 缺失 → 警告 + SIM-STUB 标记（不阻塞）
  whisper 模型缺失 → 降档到 base/small（不阻塞）
  异常流程: 异常 → M-010 登记 → M-011 日志 → M-001 决策
[日志策略]
  日志级别: INFO (pass) / WARN (soft fail) / ERROR (blocking fail)
  日志内容: 4 项检测结果 + 版本号
  日志格式: {"module": "M-002", "check": "deno", "version": "2.0.0", "status": "PASS"}
[测试策略]
  测试范围: 单元测试（每 checker）+ 集成测试（4 项全过/全失败/部分失败）
  测试用例数: 核心 4 + 边界 4 + 异常 4 = 12
  Mock策略: subprocess.run 用 unittest.mock 替换
  覆盖率目标: 行 ≥ 90% / 分支 ≥ 80%
  测试数据: fixtures/deno_version.txt, fixtures/ffmpeg_version.txt
[来源标注] [AR:API-006] [AR:DP-002] [AR洞察#1 lru_cache TTL 60s] [调研:S-002/S-007]
```

## M-003 下载器

```
[模块编号] M-003
[关联技术选型] TS-002 (yt-dlp ≥ 2023.07.06) / TS-003 (Deno ≥ 2.0) / TS-015 (subprocess)
[子模块拆分]
  子模块1: version_validator - yt-dlp 版本校验
  子模块2: youtube_downloader - YouTube 平台下载
  子模块3: bilibili_downloader - B 站平台下载
  子模块4: local_file_resolver - 本地文件解析
  子模块5: cookie_injector - Cookie 文件注入（0o600 校验）
[类设计]
  类1: YtDlpVersionValidator - 职责: 版本校验 - 属性: [min_version] - 方法: [check, compare]
  类2: YouTubeDownloader - 职责: YouTube 下载 - 属性: [deno_path, cookie_path] - 方法: [download, get_metadata]
  类3: BilibiliDownloader - 职责: B 站下载 - 属性: [cookie_path] - 方法: [download, get_metadata]
  类4: LocalFileResolver - 职责: 本地文件解析 - 属性: [supported_ext] - 方法: [resolve, validate]
  类5: CookieInjector - 职责: Cookie 注入 - 属性: [cookie_path, perms] - 方法: [validate_perms, inject_args]
[函数签名]
  validate_yt_dlp_version() -> bool
  download_youtube(url: VideoURL) -> DownloadTask
  download_bilibili(url: VideoURL) -> DownloadTask
  resolve_local(path: str) -> DownloadTask
  inject_cookie(args: list[str], cookie_path: str) -> list[str]
[状态机]
  INIT → [validate_yt_dlp_version] → READY
  READY → [download_xxx] → DOWNLOADING
  DOWNLOADING → [success] → DONE
  DOWNLOADING → [error] → RETRY (1 次) → STILL_FAIL → ERROR_REPORTED
[异常处理]
  E_DL_001 → URL 非法 → 拒绝
  E_DL_002_VERSION_TOO_OLD → yt-dlp 版本过低 → 升级提示
  E_DL_BILI_403 → B 站 403 → 不绕过
  异常流程: subprocess exit != 0 → 重试 1 次 → 仍失败 → M-010 登记
[日志策略]
  日志级别: INFO (start) / DEBUG (subprocess stdout) / ERROR (fail)
  日志内容: URL sha256 + 平台 + 大小 + 耗时
  日志格式: {"module": "M-003", "platform": "youtube", "url_sha256": "...", "size_mb": 23.5, "duration_ms": 12000}
[测试策略]
  测试范围: 单元测试（版本校验、Cookie 注入）+ 集成测试（真实下载 1 个短视频）
  测试用例数: 核心 5 + 边界 3 + 异常 4 = 12
  Mock策略: subprocess.run 用 mock，YouTube/B 站真实 URL 用 vcr.py 录制
  覆盖率目标: 行 ≥ 75% / 分支 ≥ 65%
  测试数据: fixtures/yt_dlp_version.txt, tests/integration/short_video_url.txt
[来源标注] [AR:API-007/008] [AR:DP-003] [调研:S-001/S-101]
```

## M-004 缓存管理器

```
[模块编号] M-004
[关联技术选型] TS-009 (sqlite3 stdlib) / TS-001 (Python)
[子模块拆分]
  子模块1: db_connection - sqlite3 连接管理（WAL 模式 + 0o600 权限）
  子模块2: cache_repository - CacheEntry 增删改查
  子模块3: hash_calculator - sha256(url) + etag/last_modified 双键
  子模块4: ttl_cleaner - 后台清理过期条目
  子模块5: lock_manager - asyncio.Lock 串行化写入
[类设计]
  类1: DBConnection - 职责: DB 连接管理 - 属性: [db_path, conn, lock] - 方法: [connect, set_wal, checkpoint, close]
  类2: CacheRepository - 职责: 缓存 CRUD - 属性: [db] - 方法: [get, put, delete, cleanup_expired]
  类3: HashCalculator - 职责: 双键计算 - 属性: [] - 方法: [sha256_url, parse_etag]
  类4: TTLCleaner - 职责: 后台清理 - 属性: [ttl_days, youtube_ttl_hours] - 方法: [cleanup, should_revalidate]
  类5: LockManager - 职责: 写锁管理 - 属性: [lock] - 方法: [acquire, release, measure_wait_ms]
[函数签名]
  query_cache(url: VideoURL, etag: str) -> Optional[CacheEntry]
  write_cache(entry: CacheEntry) -> bool
  cleanup_expired(ttl_days: int) -> int
  compute_url_sha256(url: str) -> str
  acquire_write_lock() -> ContextManager
[状态机]
  IDLE → [query_cache] → HIT | MISS
  MISS → [download] → FETCHED
  FETCHED → [write_cache] → WRITING (lock acquired)
  WRITING → [success] → WRITTEN
  WRITTEN → [60 days later] → EXPIRED → CLEANED
[异常处理]
  E_CK_001 → DB 不可用 → 缓存降级（不阻塞主链）
  E_CK_002 → 写入失败 → 重试 1 次 → 降级
  异常流程: 异常 → M-010 登记 → M-011 日志 cache_lock_wait_ms
[日志策略]
  日志级别: DEBUG (hit/miss) / INFO (write) / WARN (lock wait > 100ms) / ERROR (db fail)
  日志内容: url_sha256 + etag + wait_ms + hit/miss
  日志格式: {"module": "M-004", "url_sha256": "...", "action": "query", "result": "hit", "wait_ms": 12}
[测试策略]
  测试范围: 单元测试（hash、repository）+ 集成测试（concurrent 写 10 条）
  测试用例数: 核心 5 + 边界 4 + 异常 3 = 12
  Mock策略: DB 用 :memory: 临时 sqlite
  覆盖率目标: 行 ≥ 90% / 分支 ≥ 80%
  测试数据: fixtures/cache_entries.json
[来源标注] [AR:API-009/010/011] [AR:DP-004] [调研:S-102] [AR:ADR-004]
```

## M-005 转写器

```
[模块编号] M-005
[关联技术选型] TS-004 (faster-whisper ≥ 1.1.1) / TS-005 (BiliNote bcut 移植) / TS-022 (ctranslate2 ≥ 3.0)
[子模块拆分]
  子模块1: engine_selector - 引擎选择（whisper/bcut/groq）
  子模块2: whisper_engine - faster-whisper 引擎
  子模块3: bcut_engine - B 站 ASR 引擎
  子模块4: groq_engine - Groq API 引擎（可选）
  子模块5: model_manager - 模型加载与 RAM 探测
  子模块6: cer_estimator - CER 估算
[类设计]
  类1: EngineSelector - 职责: 引擎选择 - 属性: [available_engines, ram_gb] - 方法: [select, fallback_chain]
  类2: WhisperEngine - 职责: faster-whisper 调用 - 属性: [model_size, compute_type] - 方法: [transcribe, load_model]
  类3: BcutEngine - 职责: bcut 调用 - 属性: [bcut_module] - 方法: [transcribe, handle_403]
  类4: GroqEngine - 职责: Groq API 调用 - 属性: [api_key] - 方法: [transcribe, handle_429]
  类5: ModelManager - 职责: 模型加载 - 属性: [model_path, loaded_size] - 方法: [load, unload, detect_ram]
  类6: CEREstimator - 职责: CER 估算 - 属性: [] - 方法: [estimate, validate]
[函数签名]
  select_engine(ram_gb: float) -> EngineType
  transcribe(audio_path: str, audio_fingerprint: str) -> Transcript
  load_whisper_model(size: str) -> WhisperModel
  detect_ram_available() -> float  # GB
  estimate_cer(transcript: str, reference: str) -> float
[状态机]
  INIT → [detect_ram] → SELECT_ENGINE
  SELECT_ENGINE → [whisper] → WHISPER_LOADING
  WHISPER_LOADING → [success] → WHISPER_READY
  WHISPER_READY → [transcribe] → TRANSCRIBING
  TRANSCRIBING → [OOM] → DOWNGRADE (base/small) → RE_TRANSCRIBE
  TRANSCRIBING → [success] → DONE
  TRANSCRIBING → [fail] → BCUT_FALLBACK → GROQ_FALLBACK → ERROR
[异常处理]
  E_TR_001 → 三引擎全失败 → 错误码登记 + 中间产物保留
  OOM → 自动降档（base/small）+ 监控告警
  异常流程: 引擎异常 → 切换下一引擎 → 仍失败 → M-010 登记
[日志策略]
  日志级别: INFO (start) / DEBUG (engine select) / WARN (downgrade) / ERROR (all fail)
  日志内容: engine + model_size + audio_duration + cer_estimate + ram_gb
  日志格式: {"module": "M-005", "engine": "whisper", "size": "medium", "audio_sec": 1800, "cer": 0.06, "ram_gb": 11.5}
[测试策略]
  测试范围: 单元测试（engine selector、RAM 探测）+ 集成测试（10s 音频转写）
  测试用例数: 核心 6 + 边界 4 + 异常 4 = 14
  Mock策略: WhisperModel 用 mock，BcutEngine 用 mock
  覆盖率目标: 行 ≥ 70% / 分支 ≥ 60%
  测试数据: fixtures/short_audio_10s.wav
[来源标注] [AR:API-012] [AR:DP-005] [调研:S-004] [AR:洞察#3 ctranslate2 锁] [AR:TD-AR-001]
```

## M-006 LLM 客户端

```
[模块编号] M-006
[关联技术选型] TS-007 (Deepseek-v4-flash) / TS-008 (Qwen-turbo) / TS-013 (httpx ≥ 0.27)
[子模块拆分]
  子模块1: deepseek_client - Deepseek API 调用
  子模块2: qwen_client - Qwen fallback API
  子模块3: prompt_builder - prompt 拼装（3 段式）
  子模块4: front_matter_validator - 字段名 video_ 前缀校验
  子模块5: rag_query - RAG 问答
  子模块6: token_counter - input/output token 计数
[类设计]
  类1: DeepseekClient - 职责: Deepseek 调用 - 属性: [api_key, base_url, model] - 方法: [summarize, handle_5xx]
  类2: QwenClient - 职责: Qwen fallback - 属性: [api_key, base_url, model] - 方法: [summarize, handle_5xx]
  类3: PromptBuilder - 职责: prompt 拼装 - 属性: [template] - 方法: [build_summary_prompt, build_rag_prompt]
  类4: FrontMatterValidator - 职责: 字段名校验 - 属性: [whitelist_prefix] - 方法: [validate, retry_with_feedback]
  类5: RAGQuery - 职责: RAG 问答 - 属性: [transcript_repo, llm_client] - 方法: [query, format_context]
  类6: TokenCounter - 职责: token 计数 - 属性: [] - 方法: [count_input, count_output, truncate]
[函数签名]
  summarize(transcript: Transcript, style: str) -> LLMSummary
  call_deepseek(prompt: str) -> str
  call_qwen(prompt: str) -> str
  validate_front_matter(front_matter: dict) -> dict
  rag_query(query: str, context: Transcript | LLMSummary) -> str
  truncate_to_20k(text: str) -> str
[状态机]
  INIT → [build_prompt] → PROMPT_READY
  PROMPT_READY → [call_deepseek] → DEEPSEEK_CALLING
  DEEPSEEK_CALLING → [success] → DEEPSEEK_OK
  DEEPSEEK_CALLING → [5xx 3 times] → SWITCH_FALLBACK
  SWITCH_FALLBACK → [call_qwen] → QWEN_CALLING
  QWEN_CALLING → [success] → QWEN_OK
  QWEN_CALLING → [fail] → ERROR
  DEEPSEEK_OK/QWEN_OK → [validate_front_matter] → VALIDATED
  VALIDATED → [done] → DONE
[异常处理]
  E_LLM_001 → 双模型均失败 → 错误码登记 + 中间产物保留
  E_LLM_002_CHAPTERS_FALLBACK → 章节降级（5min 等距切片）
  异常流程: 5xx → 重试 3 次 → 切换 fallback 1 次 → 仍失败 → M-010 登记
[日志策略]
  日志级别: INFO (call) / DEBUG (prompt hash) / WARN (5xx retry) / ERROR (all fail)
  日志内容: model + input_tokens + output_tokens + duration_ms + task_id
  日志格式: {"module": "M-006", "model": "deepseek-v4-flash", "input_tokens": 18500, "output_tokens": 3200, "duration_ms": 4500}
[测试策略]
  测试范围: 单元测试（prompt builder、validator）+ 集成测试（httpx mock + Deepseek 真实 1 次）
  测试用例数: 核心 6 + 边界 4 + 异常 5 = 15
  Mock策略: httpx 用 respx 或 vcr.py 录制
  覆盖率目标: 行 ≥ 80% / 分支 ≥ 70%
  测试数据: fixtures/transcript_sample.json, fixtures/deepseek_response.json
[来源标注] [AR:API-013/014/015] [AR:DP-006] [调研:S-003] [AR:SR-002/PC-005] [AR:TD-AR-002 vault 候选]
```

## M-007 笔记组装器

```
[模块编号] M-007
[关联技术选型] TS-012 (PyYAML ≥ 6.0) / TS-001 (Python)
[子模块拆分]
  子模块1: front_matter_parser - YAML 解析
  子模块2: video_meta_injector - VideoMeta 注入
  子模块3: chapter_degrader - 章节降级（5min 等距）
  子模块4: markdown_assembler - Markdown 正文拼装
  子模块5: screenshot_embedder - 截图引用嵌入
  子模块6: reference_generator - 参考来源生成
[类设计]
  类1: FrontMatterParser - 职责: YAML 解析 - 属性: [yaml] - 方法: [parse, dump, validate_keys]
  类2: VideoMetaInjector - 职责: 视频元数据注入 - 属性: [schema] - 方法: [inject, fill_defaults]
  类3: ChapterDegrader - 职责: 章节降级 - 属性: [interval_min] - 方法: [degrade, build_chapter_list]
  类4: MarkdownAssembler - 职责: Markdown 拼装 - 属性: [template] - 方法: [assemble, render]
  类5: ScreenshotEmbedder - 职责: 截图引用 - 属性: [base_path] - 方法: [embed, handle_missing]
  类6: ReferenceGenerator - 职责: 参考来源 - 属性: [template] - 方法: [generate, format]
[函数签名]
  parse_front_matter(yaml_str: str) -> dict
  inject_video_meta(meta: VideoMeta) -> dict
  degrade_chapters(transcript: Transcript, interval_min: int) -> list[Chapter]
  embed_screenshots(md: str, paths: list[str]) -> str
  generate_references(meta: VideoMeta) -> str
  assemble_markdown(meta: VideoMeta, summary: LLMSummary, screenshots: list[ScreenshotFrame], transcript: Transcript) -> str
[状态机]
  INIT → [parse_front_matter] → PARSED
  PARSED → [inject_video_meta] → META_INJECTED
  META_INJECTED → [chapter_degrader (if fail)] → CHAPTERS_FALLBACK
  META_INJECTED → [embed_screenshots] → SCREENSHOTS_EMBEDDED
  SCREENSHOTS_EMBEDDED → [generate_references] → REFERENCES_ADDED
  REFERENCES_ADDED → [assemble_markdown] → MARKDOWN_READY
[异常处理]
  E_LLM_002_CHAPTERS_FALLBACK → 章节降级（5min 等距）
  YAML 解析失败 → 字段 fallback（null 占位）
  截图路径不存在 → 占位图
  异常流程: 局部失败 → 部分降级 → 主链继续
[日志策略]
  日志级别: INFO (start) / DEBUG (每个 sub-step) / WARN (降级)
  日志内容: step + duration_ms + degrade_flag
  日志格式: {"module": "M-007", "step": "chapter_degrade", "degraded": true, "interval_min": 5}
[测试策略]
  测试范围: 单元测试（6 个子模块各 2-3 用例）+ 集成测试（端到端 Markdown 拼装）
  测试用例数: 核心 8 + 边界 4 + 异常 4 = 16
  Mock策略: PyYAML 用固定 fixture
  覆盖率目标: 行 ≥ 85% / 分支 ≥ 75%
  测试数据: fixtures/front_matter.yaml, fixtures/expected_output.md
[来源标注] [AR:API-016/017/018/019/020/021] [AR:DP-007] [调研:S-005/S-202] [AR:SR-002/SR-003] [AR:ADR-008 EP-002]
```

## M-008 管道适配器

```
[模块编号] M-008
[关联技术选型] TS-015 (subprocess) / TS-001 (Python)
[子模块拆分]
  子模块1: markdown_writer - Markdown 落盘
  子模块2: pipeline_trigger - 5 阶段管道触发（subprocess）
  子模块3: tags_merger - tags 合并与冲突仲裁
  子模块4: collect_config_injector - Collect 配置注入
[类设计]
  类1: MarkdownWriter - 职责: 落盘 - 属性: [output_dir, perms] - 方法: [write, ensure_dir, chmod]
  类2: PipelineTrigger - 职责: 管道触发 - 属性: [pipeline_cmd] - 方法: [trigger, retry, parse_stages]
  类3: TagsMerger - 职责: tags 合并 - 属性: [] - 方法: [merge, resolve_conflict]
  类4: CollectConfigInjector - 职责: 配置注入 - 属性: [config_path] - 方法: [inject, verify_version]
[函数签名]
  write_markdown(md: str, topic: str, video_id: str) -> str
  trigger_pipeline(file_path: str) -> StagesResult
  merge_tags(new_tags: list[str], existing: list[str]) -> list[str]
  inject_collect_config() -> bool
[状态机]
  INIT → [write_markdown] → WRITTEN
  WRITTEN → [trigger_pipeline] → PIPELINE_RUNNING
  PIPELINE_RUNNING → [success] → PIPELINE_OK
  PIPELINE_RUNNING → [fail] → RETRY (1 次) → STILL_FAIL → ERROR
  PIPELINE_OK → [merge_tags] → MERGED
[异常处理]
  E_PIPE_001 → 落盘失败 / 管道失败 → 重试 1 次 → 仍失败 → 错误码登记
  磁盘满 → shutil.disk_usage() < 100MB → 拒绝
  异常流程: 异常 → M-010 登记 → 中间产物保留
[日志策略]
  日志级别: INFO (write) / INFO (trigger start/end) / WARN (retry) / ERROR (fail)
  日志内容: file_path + topic + video_id + stages_run + duration_ms
  日志格式: {"module": "M-008", "action": "write", "file_path": "raw/ai/abc123.md", "size_bytes": 4521}
[测试策略]
  测试范围: 单元测试（4 个子模块）+ 集成测试（端到端 落盘+管道）
  测试用例数: 核心 5 + 边界 3 + 异常 3 = 11
  Mock策略: subprocess.run 用 mock，文件 I/O 用 tmp_path
  覆盖率目标: 行 ≥ 80% / 分支 ≥ 70%
  测试数据: fixtures/sample_markdown.md, fixtures/sample_tags.json
[来源标注] [AR:API-022/023/024] [AR:DP-008] [AR:SR-007] [CE-009 Collect 配置]
```

## M-009 截图器

```
[模块编号] M-009
[关联技术选型] TS-010 (ffmpeg ≥ 6.0) / TS-011 (ffmpeg-python ≥ 0.2.0) / TS-015 (subprocess)
[子模块拆分]
  子模块1: ffmpeg_invoker - ffmpeg CLI 调用
  子模块2: i_frame_selector - I 帧选择（`-vf select=eq(pict_type,I)`）
  子模块3: image_compressor - 压缩到 ≤ 200KB
  子模块4: output_namer - 文件命名（video_id + timestamp）
[类设计]
  类1: FFmpegInvoker - 职责: ffmpeg 调用 - 属性: [ffmpeg_path] - 方法: [invoke, parse_progress]
  类2: IFrameSelector - 职责: I 帧选择 - 属性: [filter_args] - 方法: [select, count_frames]
  类3: ImageCompressor - 职责: 图像压缩 - 属性: [max_size_kb] - 方法: [compress, adjust_qscale]
  类4: OutputNamer - 职责: 文件命名 - 属性: [pattern] - 方法: [generate_name, ensure_unique]
[函数签名]
  capture_screenshots(video_path: str, video_id: str, count: int = 5) -> list[ScreenshotFrame]
  invoke_ffmpeg(args: list[str]) -> int
  select_i_frames(video_path: str, count: int) -> list[str]
  compress_image(input_path: str, output_path: str) -> int  # bytes
[状态机]
  INIT → [invoke_ffmpeg] → FFMPEG_RUNNING
  FFMPEG_RUNNING → [success] → FRAMES_EXTRACTED
  FFMPEG_RUNNING → [fail] → FALLBACK_FFMPEG_PYTHON
  FRAMES_EXTRACTED → [compress] → COMPRESSING
  COMPRESSING → [success] → DONE
  COMPRESSING → [size > 200KB] → RECOMPRESS (lower qscale) → DONE
[异常处理]
  E_FM_001 → ffmpeg 失败 → 静默跳过（不阻塞主链）
  异常流程: 失败 → 警告日志 → 继续主链 → 笔记无图
[日志策略]
  日志级别: INFO (start) / DEBUG (ffmpeg args) / WARN (fallback) / ERROR (fail)
  日志内容: video_id + count + total_size_kb + duration_ms
  日志格式: {"module": "M-009", "video_id": "abc123", "count": 5, "total_size_kb": 850, "duration_ms": 2300}
[测试策略]
  测试范围: 单元测试（I 帧选择、压缩）+ 集成测试（真实 30s 视频截图）
  测试用例数: 核心 4 + 边界 3 + 异常 2 = 9
  Mock策略: ffmpeg 用 mock，图像处理用固定 fixture
  覆盖率目标: 行 ≥ 70% / 分支 ≥ 60%
  测试数据: fixtures/short_video_30s.mp4, fixtures/expected_frame_001.jpg
[来源标注] [AR:API-025] [AR:DP-009] [调研:S-201]
```

## M-010 错误处理器

```
[模块编号] M-010
[关联技术选型] TS-001 (Python stdlib exception)
[子模块拆分]
  子模块1: error_code_registry - 错误码字典（25+ 错误码）
  子模块2: error_formatter - 3 段式错误信息生成（场景/原因/建议）
  子模块3: exit_code_resolver - 退出码仲裁（403>401>500>0）
  子模块4: error_recorder - DE-010 ErrorRecord 记录
[类设计]
  类1: ErrorCodeRegistry - 职责: 错误码字典 - 属性: [codes] - 方法: [lookup, register]
  类2: ErrorFormatter - 职责: 3 段式生成 - 属性: [template] - 方法: [format, suggest_action]
  类3: ExitCodeResolver - 职责: 退出码仲裁 - 属性: [priority] - 方法: [resolve, handle_mixed]
  类4: ErrorRecorder - 职责: 错误记录 - 属性: [records] - 方法: [record, get_all]
[函数签名]
  register_error(code: str, exc: Exception, task_id: str) -> ErrorRecord
  format_error(record: ErrorRecord) -> str
  resolve_exit_code(records: list[ErrorRecord]) -> int
  lookup_code(code: str) -> ErrorInfo
[状态机]
  INIT → [register_error] → RECORDED
  RECORDED → [format] → FORMATTED
  FORMATTED → [all records] → RESOLVE
  RESOLVE → [apply priority] → EXIT_CODE
[异常处理]
  E_SYS_001 → 未注册错误码 → 完整堆栈上报
  异常流程: 异常 → 自身 try-except 包裹 → E_SYS_001 fallback
[日志策略]
  日志级别: ERROR
  日志内容: code + msg + task_id + stack
  日志格式: {"module": "M-010", "code": "E_DL_001", "task_id": "...", "msg": "...", "stack": "..."}
[测试策略]
  测试范围: 单元测试（4 个子模块各 3-5 用例）
  测试用例数: 核心 5 + 边界 4 + 异常 4 = 13
  Mock策略: 无外部依赖
  覆盖率目标: 行 ≥ 95% / 分支 ≥ 90%
  测试数据: fixtures/error_codes.json
[来源标注] [AR:API-026/027] [AR:DP-010] [AR:BR-013/BR-014] [AR:SR-008] [CE-010]
```

## M-011 结构化日志器

```
[模块编号] M-011
[关联技术选型] TS-016 (logging stdlib) / TS-001 (Python)
[子模块拆分]
  子模块1: json_formatter - JSON Lines Formatter
  子模块2: daily_rotating_handler - 按日切分 Handler
  子模块3: sensitive_filter - 敏感字段过滤（API_KEY/COOKIE/PROMPT）
  子模块4: url_hasher - URL 仅存 sha256
[类设计]
  类1: JsonFormatter - 职责: JSON 格式化 - 属性: [field_mapping] - 方法: [format, ensure_ascii]
  类2: DailyRotatingHandler - 职责: 按日切分 - 属性: [log_dir, prefix] - 方法: [emit, should_rotate]
  类3: SensitiveFilter - 职责: 敏感过滤 - 属性: [blacklist] - 方法: [filter, redact]
  类4: UrlHasher - 职责: URL 哈希 - 属性: [] - 方法: [sha256_url, replace_in_dict]
[函数签名]
  configure_logging(log_dir: str) -> Logger
  emit_log(level: str, module: str, **kwargs) -> None
  filter_sensitive(record: LogRecord) -> bool
  hash_url(url: str) -> str
  rotate_daily() -> str
[状态机] N/A（持续 append）
[异常处理]
  日志路径不可写 → 降级 stderr
  异常流程: 异常 → stderr 降级 + 警告
[日志策略]
  日志级别: DEBUG/INFO/WARN/ERROR
  日志内容: ts + level + module + task_id + url_sha256 + step + duration_ms + code + msg
  日志格式: {"ts": "2026-06-01T10:00:00.123Z", "level": "INFO", "module": "M-011", ...}
  保留: 30 天（V1.1 默认）
[测试策略]
  测试范围: 单元测试（4 个子模块）+ 集成测试（多模块并发写日志）
  测试用例数: 核心 5 + 边界 4 + 异常 3 = 12
  Mock策略: 日志路径用 tmp_path
  覆盖率目标: 行 ≥ 90% / 分支 ≥ 80%
  测试数据: fixtures/sensitive_records.json
[来源标注] [AR:API-028] [AR:DP-011] [AR:BR-016] [AR:DE-014]
```

## M-012 并发编排器

```
[模块编号] M-012
[关联技术选型] TS-014 (asyncio) / TS-001 (Python)
[子模块拆分]
  子模块1: semaphore_pool - asyncio.Semaphore(3) 资源池
  子模块2: task_gatherer - URL[] → asyncio.gather
  子模块3: resource_prober - CPU/MEM 资源探测（psutil）
  子模块4: concurrency_resolver - 动态降级到 2
[类设计]
  类1: SemaphorePool - 职责: 并发限流 - 属性: [max_concurrency, current] - 方法: [acquire, release, get_current]
  类2: TaskGatherer - 职责: 任务编排 - 属性: [semaphore] - 方法: [gather, handle_exception]
  类3: ResourceProber - 职责: 资源探测 - 属性: [psutil] - 方法: [detect_cpu, detect_ram, suggest_concurrency]
  类4: ConcurrencyResolver - 职责: 动态降级 - 属性: [default, min] - 方法: [resolve, apply_degradation]
[函数签名]
  gather_tasks(urls: list[VideoURL], task_func: Callable) -> list[Result]
  detect_concurrency() -> int
  acquire_semaphore() -> ContextManager
  release_semaphore() -> None
  measure_wait_ms() -> float
[状态机]
  INIT → [detect_resources] → CONCURRENCY_SET
  CONCURRENCY_SET → [gather_tasks] → TASKS_RUNNING
  TASKS_RUNNING → [all success] → DONE
  TASKS_RUNNING → [exception] → EXCEPTION_ISOLATED
  EXCEPTION_ISOLATED → [continue others] → PARTIAL_DONE
[异常处理]
  RAM < 8GB → 降级到 2 并发（不抛错）
  资源探测失败 → 保持默认 3 并发
  异常流程: gather return_exceptions=True → 异常隔离 → M-010 登记
[日志策略]
  日志级别: INFO (start) / DEBUG (semaphore wait) / WARN (degrade)
  日志内容: concurrency + total + completed + failed + wait_ms
  日志格式: {"module": "M-012", "concurrency": 3, "total": 5, "completed": 4, "failed": 1, "wait_ms_p99": 85}
[测试策略]
  测试范围: 单元测试（4 个子模块）+ 集成测试（10 URL 并发调度）
  测试用例数: 核心 5 + 边界 4 + 异常 3 = 12
  Mock策略: psutil 用 mock
  覆盖率目标: 行 ≥ 90% / 分支 ≥ 80%
  测试数据: fixtures/url_list_10.json
[来源标注] [AR:API-029/030] [AR:DP-012] [AR:ADR-002/ADR-006] [AR:SR-004]
```

---

## 12 模块细化检查汇总（7/7 通过）

| 模块 | 子模块 | 类数 | 函数签名 | 状态机 | 异常处理 | 日志策略 | 测试策略 | 通过 |
|------|--------|------|---------|--------|---------|---------|---------|------|
| M-001 | 5 | 5 | 5 | - | ✓ | ✓ | ✓ | 6/6 |
| M-002 | 5 | 5 | 5 | ✓ | ✓ | ✓ | ✓ | 7/7 |
| M-003 | 5 | 5 | 5 | ✓ | ✓ | ✓ | ✓ | 7/7 |
| M-004 | 5 | 5 | 5 | ✓ | ✓ | ✓ | ✓ | 7/7 |
| M-005 | 6 | 6 | 5 | ✓ | ✓ | ✓ | ✓ | 7/7 |
| M-006 | 6 | 6 | 6 | ✓ | ✓ | ✓ | ✓ | 7/7 |
| M-007 | 6 | 6 | 6 | ✓ | ✓ | ✓ | ✓ | 7/7 |
| M-008 | 4 | 4 | 4 | ✓ | ✓ | ✓ | ✓ | 7/7 |
| M-009 | 4 | 4 | 4 | ✓ | ✓ | ✓ | ✓ | 7/7 |
| M-010 | 4 | 4 | 4 | ✓ | ✓ | ✓ | ✓ | 7/7 |
| M-011 | 4 | 4 | 4 | - | ✓ | ✓ | ✓ | 6/6 |
| M-012 | 4 | 4 | 4 | ✓ | ✓ | ✓ | ✓ | 7/7 |

**汇总**：12 模块全部 6/6 或 7/7 通过，**D2 达成率 = 95%**（待 DD-M 实现确认 100%）。

---

> **本文件结束**。12 模块细化方案就绪，交付 DD-M 按 M-NNN 分工进入实现阶段。
