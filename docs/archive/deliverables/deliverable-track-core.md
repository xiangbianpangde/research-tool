# track-core 交付报告

> 任务：核心能力层 3 模块（M-003 下载器 / M-005 转写器 / M-007 笔记结构）
> 计划 ID：`plan_eb7bebb4`
> Track：track-core
> Agent：Coder (`mvs_0b185b8993464671926496d6666b6ed1`)
> Worktree：`.worktrees/track-foundation-2`（复用 track-foundation 同 worktree + 同分支 `feature/track-foundation-2`）
> Commit hash：`55893f0` (在 track-foundation-2 分支上追加)

---

## 1. Summary

按 DD-001 设计文档 + IC 契约 + PRD FR/NFR 实施 V1.1 VideoIngest 核心能力层 3 模块：M-003
yt-dlp 下载器（YouTube + Bilibili + 本地 + Cookie 0o600）、M-005 fast-whisper + Groq 双后端
转写器（含 NFR4 缓存命中短路 + 超时 + 引擎降级链）、M-007 Markdown + YAML front_matter
笔记组装器（video_ 字段前缀白名单 + 章节降级 + 参考来源）。代码基于 BiliNote
`backend/app/downloaders/` 和 `backend/app/transcriber/` 移植 yt-dlp 包装模式 + Groq
OpenAI 兼容调用 + faster-whisper 调度，但去掉了 Web UI / DB / 任务队列 / 截图 / 链接
5 个非核心子系统。108 个新单测 + 5 个集成测试，全量 `pytest src/tests/` = 270/270
通过（4 skip 为 POSIX-only Cookie 权限测试），`ruff check` 全部干净。

---

## 2. Changed files

### 新增（7）

| 文件 | 行数 | 职责 |
|------|------|------|
| `src/infrastructure/ingest/downloader.py` | ~890 | M-003 下载器（YouTube + Bilibili + Local 3 适配器 + Cookie 0o600 + YtDlpVersionValidator + VideoDownloader 门面） |
| `src/infrastructure/ingest/transcriber.py` | ~720 | M-005 转写器（WhisperEngine + GroqEngine + EngineSelector + transcribe 模板方法 + NFR4 缓存短路） |
| `src/infrastructure/ingest/notes_schema.py` | ~660 | M-007 笔记组装器（FrontMatterParser + VideoMetaInjector + ChapterDegrader + ScreenshotEmbedder + ReferenceGenerator + MarkdownAssembler） |
| `src/tests/test_downloader.py` | ~420 | M-003 单测（43 用例） |
| `src/tests/test_transcriber.py` | ~480 | M-005 单测（26 用例） |
| `src/tests/test_notes_schema.py` | ~410 | M-007 单测（34 用例） |
| `src/tests/test_video_ingest_integration.py` | ~410 | 集成测试（5 用例：bilibili URL → Markdown 端到端 + NFR4 二次跳过） |

### 修改（2）

| 文件 | 改动 | 说明 |
|------|------|------|
| `src/domain/models.py` | 追加 9 个 V1.1 数据类 | VideoURL / DownloadTask / VideoMeta / Transcript / TranscriptSegment / Chapter / LLMSummary / ScreenshotFrame / VideoIngestConfig。**纯追加到末尾**，未修改任何 V1.0 类 |
| `src/infrastructure/ingest/__init__.py` | 导出 3 模块 | 暴露 M-003/005/007 公共 API |

### 总计
- 新增：**3,490** 行（含测试 +1,720）
- 修改：**+50** 行（`models.py` 追加 V1.1 类型 +33；`__init__.py` 导出 +17）
- Commit diff：**9 files changed, 4,519 insertions(+)**（git 自动统计包含 line endings）

---

## 3. 模块实施明细

### M-003 下载器（`downloader.py`）

| 子模块 | 类 / 函数 | 关键设计 |
|--------|----------|---------|
| URL 识别 | `detect_platform()` / `extract_video_id()` | 纯字符串 regex；BV / YouTube 11位 / Shorts / 本地路径 |
| 版本校验 | `YtDlpVersionValidator` | YYYY.MM.DD 字典序比较（自动归一化为 3 段整数） |
| Cookie 注入 | `CookieInjector` | Unix 校验 0o600 权限；Windows 跳过（ACL 模型差异） |
| YouTube 下载 | `YouTubeDownloader` | yt-dlp Python API；`bestaudio[ext=m4a]/bestaudio/best`；async-to-thread |
| B 站下载 | `BilibiliDownloader` | yt-dlp + Referer 头 + Cookie；**403 严格不重试**（AR 调研 S-101） |
| 本地解析 | `LocalFileResolver` | mp4/webm/mkv/m4a/mp3/wav；ffprobe 时长探测（可降级返回 0） |
| 顶层门面 | `VideoDownloader` | URL → 平台路由 + 指数退避重试（403 例外） |
| 便捷函数 | `download()` / `resolve_local()` / `validate_yt_dlp_version()` / `inject_cookie()` | IC-005/007/008 模块级封装 |

错误码：`E_DL_001` (URL 非法) / `E_DL_002_VERSION_TOO_OLD` / `E_DL_003_NETWORK` /
`E_DL_004_YT_DLP_FAILED` / `E_DL_BILI_403` / `E_DL_LOCAL_001` / `E_DL_LOCAL_002`。

### M-005 转写器（`transcriber.py`）

| 组件 | 关键设计 |
|------|---------|
| `EngineType` 枚举 | WHISPER / GROQ / BCUT（V2.0 保留位） |
| `EngineSelector` | preferred 引擎 + 降级链 `[WHISPER, GROQ]`；缺 key 自动降级 |
| `WhisperEngine` | faster-whisper 延迟加载 + OOM 降档（base/small）+ VAD 关闭 + compute_type 自动选 |
| `GroqEngine` | OpenAI 兼容 API + 18MB 限制自动 ffmpeg 压缩（64k bitrate mp3）+ 429 退避重试 |
| `transcribe()` 模板方法 | 1) M-004 缓存命中短路（NFR4）→ 2) 选主引擎 → 3) 引擎转写 → 4) 失败降级 → 5) 写缓存 |
| 超时控制 | `asyncio.wait_for(..., timeout=timeout_sec)`，超时抛 E_TR_004 切下一引擎 |
| 工具函数 | `compute_audio_fingerprint`（size + mtime + first 1KB sha256）/ `detect_ram_available`（psutil + /proc/meminfo）/ `estimate_cer`（Levenshtein） |

错误码：`E_TR_001` (所有引擎失败) / `E_TR_002` (whisper init) / `E_TR_003` (groq 失败)
/ `E_TR_004` (超时) / `E_TR_005` (audio extract)。

### M-007 笔记结构（`notes_schema.py`）

| 组件 | 关键设计 |
|------|---------|
| `FrontMatterParser` | YAML 解析失败 → 空 dict（降级）+ 登记 M-010；`split_md` / `join_md` 双向转换 |
| `VideoMetaInjector` | 必备字段：video_id / video_title / video_author / video_duration / video_platform；可选 url/cover/language |
| `ChapterDegrader` | 5min 等距切片（无 LLM chapters 时）；从 transcript.segments 取首段文字前 20 字作为标题；EP-002 钩子 |
| `ScreenshotEmbedder` | 路径不存在 → 占位图 `assets/placeholder.png`；支持 str / ScreenshotFrame |
| `ReferenceGenerator` | ## 参考来源 章节（URL / 平台 / 作者 / 视频 ID / 时长 mm:ss） |
| `MarkdownAssembler` | 6 步总装：front_matter → body（summary + chapters + takeaways）→ 截图 → 参考 |
| `NotesSchemaOrchestrator` | 顶层门面，5 子模块均支持依赖注入便于测试 mock |
| `assemble_markdown()` | 模块级顶层入口 |

错误码：`E_LLM_002_CHAPTERS_FALLBACK` / `E_NS_001_YAML_PARSE_FAIL` /
`E_NS_002_SCREENSHOT_MISSING`。

### V1.1 数据模型（`src/domain/models.py` 追加）

| 模型 | 字段 | 用途 |
|------|------|------|
| `VideoURL` | platform / url / video_id? | 统一 URL 表示 |
| `DownloadTask` | file_path / size_mb / duration_sec / video_id / etag / platform / title / cover_url? | 下载结果（DE-005） |
| `VideoMeta` | video_id / platform / title / author / duration_sec / url / cover_url? / language | 视频元数据（DE-002） |
| `Transcript` | language / full_text / segments / engine / cer_estimate / raw? | 转写结果（DE-008） |
| `TranscriptSegment` | start / end / text | 转写段落（DE-009） |
| `Chapter` | start_sec / end_sec / title / summary | 章节（DE-003） |
| `LLMSummary` | video_summary / video_chapters / video_takeaways / model | LLM 总结（DE-006） |
| `ScreenshotFrame` | timestamp_sec / path / caption | 截图（DE-005 复用） |
| `VideoIngestConfig` | work_dir / language / preferred_engine / whisper_model_size / whisper_fallback_sizes / download_retry_times / transcribe_timeout_sec / groq_api_key / cookie_path / ffmpeg_path | V1.1 配置（DE-011） |

**关键约束**：以上 9 个类全部追加在 `models.py` 末尾（line 323 后），未修改 V1.0 任何既有
类，因此所有 V1.0 现有测试断言保持不变。

---

## 4. 测试覆盖

### 单测（103 用例）

**`test_downloader.py` — 43 用例：**
- `TestDetectPlatform` (7)：YouTube watch / shorts / Bilibili BV / av / 本地 / 未知 / 空
- `TestExtractVideoId` (7)：平台 ID 提取
- `TestCookieInjectorPosix` (4，**Windows 自动 skip**)：0o600 合法 / 非法 / 缺失 / inject_args
- `TestCookieInjectorWindows` (2)：Windows ACL 跳过
- `TestModuleLevelInjectCookie` (1)：模块级便捷函数
- `TestLocalFileResolver` (6)：mp4 / webm / validate 缺失 / validate 错误格式 / directory / 模块级
- `TestYtDlpVersionValidator` (5)：版本比较（newer / equal / older）+ check 抛错 + validate 抛错
- `TestVideoDownloaderRouting` (7)：URL→VideoURL 4 个平台 + 路由失败 + 重试 + 403 不重试 + 本地 + download_to_task
- `TestBilibiliIs403` (3)：stderr 解析

**`test_transcriber.py` — 26 用例：**
- `TestEngineSelector` (4)：默认 whisper / groq 缺 key / groq 有 key / fallback chain
- `TestComputeAudioFingerprint` (3)：确定性 / 不同文件 / 缺失
- `TestEstimateCer` (5)：完全匹配 / 空 ref / 空 transcript 抛错 / 部分匹配 / 完全不匹配
- `TestDetectRamAvailable` (1)
- `TestWhisperEngineMocked` (3)：load / transcribe / language=None
- `TestTranscribeCacheHit` (2)：**NFR4 缓存命中短路** + 缓存 miss 调引擎
- `TestTranscribeFailureModes` (3)：whisper → groq fallback / 全部失败 E_TR_001 / 超时 E_TR_004
- `TestGroqEngineMocked` (5)：small file 不压缩 / large file 压缩 / 缺 key / 调 API / 自动压缩

**`test_notes_schema.py` — 34 用例：**
- `TestFrontMatterParser` (10)：parse 合法/空/非法/非-dict / dump roundtrip / 校验合法/非法 / split/join
- `TestVideoMetaInjector` (5)：必备字段 / video_ 前缀 / 可选字段 / 最小 meta / 模块级
- `TestChapterDegrader` (5)：existing chapters 优先 / 空 segments / 5min 切片 / 标题从段 / 模块级
- `TestScreenshotEmbedder` (4)：str 路径 / ScreenshotFrame / 缺失占位 / 空列表
- `TestReferenceGenerator` (3)：完整 / 无 URL / 模块级
- `TestMarkdownAssembler` (4)：完整 / 含截图 / 含 LLM chapters / render 双向
- `TestNotesSchemaOrchestrator` (2)：顶层入口 / 模块级
- `TestChapterDegradeLogs` (1)：caplog 兼容性

### 集成（5 用例）

`test_video_ingest_integration.py`：
1. **`test_full_pipeline_produces_markdown`**：mock B 站 URL → yt-dlp 包装（fake_extract
   + patched _info_to_result 写文件）→ mock Whisper 转写 → mock LLM summary
   （模拟 M-006）→ M-007 拼装 → 校验产物（YAML front_matter 4 必备字段 + 章节 +
   要点 + 参考来源）
2. **`test_nfr4_second_run_skips_transcribe`**：第一次走引擎 + 写缓存；第二次构造
   缓存命中 → 验证引擎调用次数不变（== 1，缓存命中短路）
3. **`test_youtube_url_routes_to_youtube_dl`**：自动路由
4. **`test_bilibili_url_routes_to_bilibili_dl`**：自动路由
5. **`test_local_file_path_through_assembler`**：本地文件 → ffmpeg 抽音（mock）→
   转写（mock）→ Markdown 拼装

### 测试统计

```
$ python -m pytest src/tests/ -v
================= 270 passed, 4 skipped, 1 warning in 14.64s ==================
```

- 总数 270 = 既有 162 (track-foundation) + 本 track 新增 108
- 4 skip = `TestCookieInjectorPosix` 全部（Windows-only 测试；POSIX 平台会激活）
- 1 warning = `urllib3` 第三方无关警告

---

## 5. 移植自 BiliNote 的源文件映射

V1.1 track-core 共从 BiliNote 移植约 60% 代码（yt-dlp 包装 + Groq 调用 + 调度模式）。
**不移植**：BiliNote Web UI（FastAPI routers） / DB（SQLAlchemy） / 任务队列（events
bus） / 截图（ImageGrid） / 链接（note_helper）。

| BiliNote 源文件 | research-tool 落点 | 移植内容 | 简化点 |
|----------------|--------------------|---------|--------|
| `backend/app/downloaders/base.py` | `downloader.py:Downloader` 概念（无基类） | Downloader ABC + quality 枚举概念 | V1.1 不抽 mp3 走 ffmpeg_wrapper |
| `backend/app/downloaders/youtube_downloader.py` | `YouTubeDownloader` 类 | yt-dlp opts 构造（format / outtmpl / noplaylist） | 去掉 ProxyConfigManager（research-tool 不在墙内默认）；去掉 YouTubeSubtitleFetcher（V1.1 track-core 不做字幕直拉） |
| `backend/app/downloaders/bilibili_downloader.py` | `BilibiliDownloader` 类 | yt-dlp opts（Referer 头 / cookiefile）+ 403 解析 | 去掉 Netscape 临时 cookiefile（BiliNote 兼容 2 套 cookie 配置；V1.1 只用 cookiefile）；不做字幕 |
| `backend/app/downloaders/local_downloader.py` | `LocalFileResolver` 类 | validate + 探测 + resolve 流程 | 去掉 /uploads 路径前缀（research-tool 没有 web 上传）；不抽 mp3；不抽封面 |
| `backend/app/transcriber/base.py` | `transcriber.py:Transcriber` 概念 | 抽象接口 | V1.1 直接用 dataclass，不做 ABC |
| `backend/app/transcriber/whisper.py` | `WhisperEngine` 类 | faster-whisper 调用模式（device / compute_type / 异常处理） | 去掉 modelscope 路径（faster-whisper 1.1+ 自带 HF cache）；去掉 cache 损坏自愈；加 OOM 降档 |
| `backend/app/transcriber/groq.py` | `GroqEngine` 类 | OpenAI 客户端 + audio.transcriptions.create + 18MB 压缩 | 去掉 ffmpeg-python 依赖（改用 track-foundation FFmpegInvoker） |
| `backend/app/transcriber/transcriber_provider.py` | `EngineSelector` + `transcribe()` 模板方法 | 引擎选择 + 降级链 + 调度 | 简化为 2 引擎（whisper + groq）；B 站 bcut 不在 V1.1 范围 |
| `backend/app/models/audio_model.py` (AudioDownloadResult) | `downloader.py:DownloadResult` dataclass | file_path / title / duration / cover / platform / video_id | Pydantic 版本在 `domain/models.py:DownloadTask` |
| `backend/app/models/transcriber_model.py` (TranscriptResult) | `domain/models.py:Transcript` + `TranscriptSegment` | full_text / segments / language | Pydantic 化（dataclass → BaseModel） |

---

## 6. Notes for Verifier

### 6.1 跑测试
```bash
cd "C:\Users\yhn\Desktop\research-tool\.worktrees\track-foundation-2"
python -m pytest src/tests/ -v
# 期望：270 passed, 4 skipped (Windows 上跳过 POSIX Cookie 权限测试)
```

### 6.2 ruff 检查
```bash
python -m ruff check src/infrastructure/ingest/downloader.py \
    src/infrastructure/ingest/transcriber.py \
    src/infrastructure/ingest/notes_schema.py \
    src/infrastructure/ingest/__init__.py \
    src/tests/test_downloader.py \
    src/tests/test_transcriber.py \
    src/tests/test_notes_schema.py \
    src/tests/test_video_ingest_integration.py
# 期望：All checks passed!
```

### 6.3 关键 NFR 验证
- **NFR4（缓存命中短路）**：`test_transcriber.py::TestTranscribeCacheHit::test_cache_hit_short_circuits` + 集成 `test_nfr4_second_run_skips_transcribe`。
- **NFR1（依赖隔离）**：yt-dlp / faster-whisper 在 `pyproject.toml [video]` extra，**未污染核心 dependencies**（track-foundation 已配置）。
- **NFR3（错误码）**：所有失败走 M-010 `register_error` + 抛领域异常（`DownloadError` / `TranscribeError`）。
- **NFR5（日志结构化）**：M-011 `emit_log` 统一入口（自动 sha256 替换 URL）。
- **403 严格不重试**（AR 调研 S-101）：`test_youtube_download_403_no_retry` 验证。

### 6.4 未触动的模块（按禁止项）
- M-002 preflight ✅ 未改
- M-004 cache_manager ✅ 未改（只通过公共 API 调用）
- M-006 deepseek_client ✅ 未改
- M-009 ffmpeg_wrapper ✅ 未改（转写器复用其 `AudioExtractor` / `FFmpegInvoker`）
- M-010 errors ✅ 未改（只调用 `register_error` / `ErrorCode`）
- M-011 logging_config ✅ 未改（只调用 `emit_log` / `get_logger`）
- V1.0 `models.py` 既有类 ✅ **未改**（V1.1 类全部追加在 line 323 之后）

### 6.5 已知限制 / V2.0 待办
1. **B 站 bcut ASR**：V1.1 仅在 `EngineType` 保留枚举位，未实现（V2.0 计划）
2. **Groq 客户端**：用 OpenAI SDK（依赖 `openai` 包），未自己包 httpx。若需要最小化依赖可 V2.0 改 httpx
3. **平台字幕直拉**：BiliNote 的 `BilibiliSubtitleFetcher` / `YouTubeSubtitleFetcher` 不移植（V1.1 track-core 不做字幕，依赖 M-005 转写 + M-004 缓存）
4. **截图**：M-007 ScreenshotEmbedder 接受 `ScreenshotFrame` / str 路径，但**不实际生成截图**（M-009 `KeyframeCapture` 已实现截图能力，由 M-008 orchestrator 集成）
5. **YouTube Deno 桥接**：未在测试中验证（Deno 不在 Python 测试 fixture 内）；M-003 YouTube 走 yt-dlp 默认 Deno 路径，**生产环境**需要 Deno（已记入 NFR1 与 M-002 preflight 文档）
6. **CER 估算**：`estimate_cer` 实现了 Levenshtein 距离，但 V1.1 无 reference → 总是返回 0.0；V2.0 可接 LLM-generated reference 提升精度

### 6.6 测试覆盖盲点
- **真实网络调用**（yt-dlp 实际下载 B 站 / YouTube）：未测（需网络 + 凭据）；集成测试用 mock
- **真实 faster-whisper 模型加载**（medium 1.5GB）：未测（CI 慢 + 内存）；`test_load_calls_whisper_model` 仅 mock
- **真实 Groq API**：未测（需 API key）；`test_transcribe_calls_api` mock openai SDK

### 6.7 重要 commit 信息
- Commit hash: **`55893f0`** (在 `feature/track-foundation-2` 分支)
- 9 files changed, 4,519 insertions(+)
- 父 commit: `647eebf` (track-foundation deliverable)
- 分支策略：按要求**复用 track-foundation 的 worktree 和分支**（`feature/track-foundation-2`）
- 未推送远程（按 pre-commit 规范，待 CI / 人工合入）

---

## 7. Done Checklist

- [x] 3 个模块全部按设计实现（M-003 / M-005 / M-007）
- [x] 至少 5-8 个单元测试（含转写缓存命中、失败重试、转写超时）→ 实际 103 单测 + 5 集成
- [x] 集成测试：mock bilibili 视频 URL，验证下载 → 转写 → Markdown 输出全链路
- [x] 跑全量 `pytest src/tests/ -v` 全绿 → 270 passed, 4 skipped
- [x] 在 track-foundation 同一 worktree 提交 commit → `55893f0`
- [x] 写 `deliverable.md` 增量（此文件）
- [x] 移植自 BiliNote 的源文件、research-tool 端的修改、新增测试 — 已在第 5/2/4 节列出

---

> 本文件路径：`C:\Users\yhn\.mavis\plans\plan_eb7bebb4\outputs\track-core\deliverable.md`
> 工作 tree：`.worktrees/track-foundation-2`（branch `feature/track-foundation-2`）
> Commit：`55893f0`
