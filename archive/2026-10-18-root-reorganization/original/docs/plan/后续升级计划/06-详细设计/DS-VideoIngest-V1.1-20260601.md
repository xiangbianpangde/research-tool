# 数据结构设计 — VideoIngest V1.1（DD-001）

> **生成方**：DD-001
> **日期**：2026-06-01
> **存储数**：2 大存储（sqlite cache.db + filesystem）+ 9 dataclass
> **覆盖率**：100%

---

## DS-001 sqlite cache.db

```
[结构编号] DS-001
[关联技术选型] TS-009 (sqlite3 stdlib) / TS-001 (Python)
[存储类型] 关系型数据库（SQLite 嵌入式）
[存储名称] cache.db（路径：~/.cache/research-tool/cache.db）
[表结构/字段定义]
  表名: cache_entries
  字段1: id INTEGER PRIMARY KEY AUTOINCREMENT 11 必填 自增 主键 ID
  字段2: url_sha256 TEXT 64 必填 "" 唯一索引 IDX_url_sha256 URL sha256 哈希
  字段3: etag TEXT 128 必填 "" 索引 IDX_etag ETag / Last-Modified
  字段4: platform TEXT 16 必填 "" 索引 IDX_platform 平台枚举
  字段5: video_id TEXT 64 必填 "" 视频 ID
  字段6: transcript_path TEXT 512 可选 NULL 转写稿路径
  字段7: llm_summary_path TEXT 512 可选 NULL LLM 总结路径
  字段8: output_md_path TEXT 512 可选 NULL 最终 md 路径
  字段9: input_tokens INTEGER 11 可选 0 LLM 输入 token
  字段10: output_tokens INTEGER 11 可选 0 LLM 输出 token
  字段11: cer_estimate REAL 4 可选 NULL CER 估算
  字段12: created_at TEXT 32 必填 "" 索引 IDX_created_at 创建时间（ISO8601）
  字段13: updated_at TEXT 32 必填 "" 更新时间
  字段14: ttl_seconds INTEGER 11 必填 2592000 TTL（30 天）
[主键/唯一索引]
  主键: id
  唯一索引1: UNIQUE(url_sha256, etag)  # 防止重复
  索引2: IDX_created_at(created_at)  # 加速 TTL 清理
  索引3: IDX_platform(platform)  # 加速平台过滤
[外键/关联关系] 无（单表）
[约束条件]
  约束1: CHECK(length(url_sha256) = 64)  # sha256 固定 64
  约束2: CHECK(platform IN ('bilibili', 'youtube', 'local'))  # 平台枚举
  约束3: CHECK(ttl_seconds > 0)
[数据量预估]
  初始量: 0 条
  增长速率: ~10 条/天（单用户）
  峰值: 10,000 条（V1.1 一年内）
  文件大小: ~50MB（10k 条）
[分片策略] 无（单机 sqlite 单文件）
[WAL 模式] journal_mode=WAL; synchronous=NORMAL
[文件权限] 0o600（DB） / 0o700（目录）
[来源标注] [AR:TS-009] [AR:API-009/010/011] [AR:DP-004] [调研:S-102] [AR:ADR-004]
```

## DS-002 filesystem raw/

```
[结构编号] DS-002
[关联技术选型] TS-001 (Python pathlib) / TS-015 (subprocess)
[存储类型] 文件系统
[存储名称] ./raw/（工作目录相对路径）
[目录结构]
  raw/
    <topic>/
      <video-id>.md          # 笔记 Markdown
      assets/
        <video-id>.mp4       # 视频原文件（可选）
        <video-id>-frame-{1-5}.jpg  # 截图
[文件命名规则]
  笔记: <video-id>.md  # 视频 ID 为 yt-dlp 提取的 ID
  视频: <video-id>.<ext>  # ext 为 mp4/webm/mkv
  截图: <video-id>-frame-{1-5}.jpg  # 5 张
[文件权限]
  笔记: 0o644
  视频: 0o644
  截图: 0o644
[主键/唯一索引] video-id 唯一
[外键/关联关系] assets/ 隶属于 <video-id>.md 同 topic
[约束条件]
  约束1: topic ∈ {"general", "ai", "tech", "tutorial", ...}（用户自定义）
  约束2: video-id 非空 + 仅含 [a-zA-Z0-9_-]
  约束3: 文件大小 ≤ 500MB（V1.1 软上限）
[数据量预估]
  初始量: 0 个 topic 目录
  增长速率: ~10 个目录/天
  峰值: 5,000 个目录（V1.1 一年内）
  总大小: ~50GB（含视频原文件）
[分片策略] 按 topic 分目录
[磁盘检查] shutil.disk_usage() < 100MB → 拒绝写入
[来源标注] [AR:B-005] [AR:DP-008] [AR:API-022/025]
```

## DS-003 filesystem logs/

```
[结构编号] DS-003
[关联技术选型] TS-016 (logging stdlib)
[存储类型] 文件系统
[存储名称] ~/.cache/research-tool/logs/
[文件命名]
  <YYYY-MM-DD>.jsonl  # 按日切分
[文件格式] JSON Lines（每行 1 条日志 dict）
[文件权限] 0o700（目录）
[保留期] 30 天（V1.1 默认）
[字段] ts / level / module / task_id / url_sha256 / step / duration_ms / code / msg
[来源标注] [AR:B-007] [AR:API-028] [AR:BR-016] [AR:DE-014]
```

## DS-004 filesystem transcripts/

```
[结构编号] DS-004
[关联技术选型] TS-001 (Python pathlib)
[存储类型] 文件系统
[存储名称] ~/.cache/research-tool/transcripts/
[文件命名]
  <video-id>.json  # 转写稿 JSON
[文件格式] JSON（DE-005 Transcript dataclass 序列化）
[文件权限] 0o700（目录）
[保留期] 30 天（或缓存 TTL）
[字段] video_id / segments[] / engine / model_size / cer_estimate / created_at
[来源标注] [AR:B-007 扩展] [DD推断:基于 M-005 中间产物保留]
```

## DE-001 VideoURL dataclass

```
[结构编号] DE-001
[关联技术选型] TS-001 (Python dataclass)
[类型] Python dataclass
[字段定义]
  url: str  # URL 或本地路径
  platform: Platform  # BILIBILI / YOUTUBE / LOCAL / UNKNOWN
  video_id: str  # 平台提取的视频 ID
  cookie_path: Optional[str]  # Cookie 文件路径
  is_local: bool  # 是否本地文件
[使用模块] M-001 / M-003 / M-004
[来源标注] [AR:API-001/002/005/007/008] [AR:TD:DF]
```

## DE-002 VideoMeta dataclass

```
[结构编号] DE-002
[关联技术选型] TS-001
[类型] Python dataclass
[字段定义]
  video_id: str
  title: str
  author: str
  duration_sec: int
  platform: Platform
  upload_date: str  # YYYYMMDD
  description: str
  thumbnail_url: str
[使用模块] M-007 / M-008
[来源标注] [AR:API-017/019/020]
```

## DE-003 DownloadTask dataclass

```
[结构编号] DE-003
[关联技术选型] TS-001
[类型] Python dataclass
[字段定义]
  video_id: str
  file_path: str
  size_mb: float
  duration_sec: int
  format: str
  etag: str
  last_modified: str
[使用模块] M-003 / M-004
[来源标注] [AR:API-007] [AR:TD:DE-004]
```

## DE-004 CacheEntry dataclass

```
[结构编号] DE-004
[关联技术选型] TS-001
[类型] Python dataclass
[字段定义]
  url_sha256: str
  etag: str
  platform: str
  video_id: str
  transcript_path: Optional[str]
  llm_summary_path: Optional[str]
  output_md_path: Optional[str]
  input_tokens: int
  output_tokens: int
  cer_estimate: Optional[float]
  created_at: str
  updated_at: str
  ttl_seconds: int
[使用模块] M-004
[对应表] cache_entries (DS-001)
[来源标注] [AR:API-009/010/011] [AR:TD:DE-004]
```

## DE-005 Transcript dataclass

```
[结构编号] DE-005
[关联技术选型] TS-001
[类型] Python dataclass
[字段定义]
  video_id: str
  audio_fingerprint: str  # sha256(audio_path)
  engine: str  # whisper / bcut / groq
  model_size: str  # medium / small / base
  segments: list[Segment]  # 含 start / end / text
  cer_estimate: Optional[float]
  created_at: str
[使用模块] M-005 / M-006
[来源标注] [AR:API-012] [AR:TD:DF]
```

## DE-006 LLMSummary dataclass

```
[结构编号] DE-006
[关联技术选型] TS-001
[类型] Python dataclass
[字段定义]
  video_id: str
  model: str  # deepseek-v4-flash / qwen-turbo
  style: str
  front_matter: dict  # 含 video_* 字段
  body: str  # Markdown 正文
  chapters: list[Chapter]
  input_tokens: int
  output_tokens: int
  created_at: str
[使用模块] M-006 / M-007
[来源标注] [AR:API-013/014/016] [AR:TD:DF]
```

## DE-007 Chapter dataclass

```
[结构编号] DE-007
[关联技术选型] TS-001
[类型] Python dataclass
[字段定义]
  title: str
  start_sec: int
  end_sec: int
[使用模块] M-007
[来源标注] [AR:API-016/021]
```

## DE-008 ScreenshotFrame dataclass

```
[结构编号] DE-008
[关联技术选型] TS-001
[类型] Python dataclass
[字段定义]
  video_id: str
  frame_index: int  # 1-5
  file_path: str
  size_kb: int
  timestamp_sec: int  # 视频内时间点
[使用模块] M-009 / M-007
[来源标注] [AR:API-025]
```

## DE-009 OutputFile dataclass

```
[结构编号] DE-009
[关联技术选型] TS-001
[类型] Python dataclass
[字段定义]
  video_id: str
  topic: str
  file_path: str
  size_bytes: int
  created_at: str
  pipe_stages_run: list[str]
  pipe_success: bool
[使用模块] M-008
[来源标注] [AR:API-022/023] [AR:TD:DE-009]
```

## DE-010 ErrorRecord dataclass

```
[结构编号] DE-010
[关联技术选型] TS-001
[类型] Python dataclass
[字段定义]
  code: str  # E_DL_001 / E_TR_001 / ...
  task_id: str
  module: str
  msg: str
  exception_type: str
  stack: str
  created_at: str
[使用模块] M-010 / M-001
[来源标注] [AR:API-026/027] [AR:BR-013/BR-014]
```

## DE-011 CookieConfig dataclass

```
[结构编号] DE-011
[关联技术选型] TS-001
[类型] Python dataclass
[字段定义]
  cookie_path: str
  perms: int  # 0o600
  validate: bool
[使用模块] M-003
[来源标注] [AR:API-007] [AR:BR-010]
```

## DE-012 PreflightReport dataclass

```
[结构编号] DE-012
[关联技术选型] TS-001
[类型] Python dataclass
[字段定义]
  deno_ok: bool
  deno_version: Optional[str]
  node_ok: bool
  node_version: Optional[str]
  ffmpeg_ok: bool
  ffmpeg_version: Optional[str]
  whisper_ok: bool
  whisper_model_size: Optional[str]  # medium / small / base
  timestamp: str
  ttl_seconds: int  # 60
[使用模块] M-002 / M-001
[来源标注] [AR:API-006] [AR:BR-027]
```

---

## 数据结构覆盖汇总

| 存储/dataclass | 关联模块 | 字段数 | 索引数 | 约束数 | 来源标注 |
|--------------|---------|--------|--------|--------|----------|
| DS-001 sqlite cache.db | M-004 | 14 | 3 | 3 | [AR:TS-009] |
| DS-002 filesystem raw/ | M-008/M-009 | - | - | 3 | [AR:B-005] |
| DS-003 filesystem logs/ | M-011 | - | - | - | [AR:B-007] |
| DS-004 filesystem transcripts/ | M-005 | - | - | - | [DD推断] |
| DE-001 VideoURL | M-001/M-003 | 5 | - | - | [AR:DF] |
| DE-002 VideoMeta | M-007 | 8 | - | - | [AR:DF] |
| DE-003 DownloadTask | M-003/M-004 | 7 | - | - | [AR:DF] |
| DE-004 CacheEntry | M-004 | 13 | - | - | [AR:DF] |
| DE-005 Transcript | M-005/M-006 | 7 | - | - | [AR:DF] |
| DE-006 LLMSummary | M-006/M-007 | 9 | - | - | [AR:DF] |
| DE-007 Chapter | M-007 | 3 | - | - | [AR:DF] |
| DE-008 ScreenshotFrame | M-009/M-007 | 5 | - | - | [AR:DF] |
| DE-009 OutputFile | M-008 | 7 | - | - | [AR:DF] |
| DE-010 ErrorRecord | M-010 | 7 | - | - | [AR:DF] |
| DE-011 CookieConfig | M-003 | 3 | - | - | [AR:DF] |
| DE-012 PreflightReport | M-002/M-001 | 10 | - | - | [AR:DF] |

**覆盖率：100%（16/16 全部有定义 + 来源标注）**

---

> **本文件结束**。2 存储 + 12 dataclass + 9 DE 数据结构设计就绪。
