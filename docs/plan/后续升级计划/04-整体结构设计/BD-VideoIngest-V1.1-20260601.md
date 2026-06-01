# 系统边界定义 — VideoIngest V1.1（TD-001）

> **生成方**：TD-001
> **日期**：2026-06-01
> **边界数**：7（B-001~B-007）

---

## B-001 用户 CLI 输入边界

```
[边界编号] B-001
[边界类型] 输入边界
[边界描述] 用户通过终端调用 `research run/ask` 命令传入 argv。系统仅接受 --video-url / --video-file / --no-cache / --cookie-file / --transcriber / --transcriber-size / --style / --chapter-interval-min 等 8 个白名单参数。
[关联模块] M-001
[数据格式] argv 字符串数组
[安全要求]
  - 参数白名单校验（防注入）
  - --cookie-file 必须 0o600 权限 [BR-010]
  - URL 必须匹配 5 种已知形态 [F-002.AC-1]
[不处理范围]
  - 配置文件（V1.1 不支持，仅 CLI 参数）
  - 交互式输入（V1.1 不支持 REPL）
  - 环境变量（V1.1 不读取 API Key 之外的配置）
[来源标注] [SA:BP-001/BP-015/EX-001/EX-002/EX-003/BR-010]
```

## B-002 外部视频平台边界

```
[边界编号] B-002
[边界类型] 输入边界（外部资源）
[边界描述] 系统通过 yt-dlp/deno 访问 Bilibili / YouTube 平台的视频资源、字幕、元信息、缩略图。含可选 Cookie 注入。
[关联模块] M-003, M-007
[数据格式]
  - 请求: HTTPS GET/POST
  - 响应: 视频二进制 + JSON dump（yt-dlp --dump-json）
  - 限流: 平台自带策略（B 站 IP 限速、YouTube PO Token 轮换）
[安全要求]
  - 4K/付费/限地区 → 不绕过，返回 E_DL_BILI_403 [BR-023]
  - Cookie 文件 0o600 权限
  - 不持久化 Cookie 内容到 cache.db
[不处理范围]
  - 抖音/快手等平台（V1.1 不支持）
  - m3u8 / RTMP 直播流 [B-002]
  - DRM 加密视频
  - 私域视频（需登录且无 Cookie）
[来源标注] [SA:BP-003/BP-008/EX-008~EX-013/BR-005/BR-023] [调研:S-001]
```

## B-003 外部 LLM API 边界

```
[边界编号] B-003
[边界类型] 输出边界（外部服务）
[边界描述] 系统调用 Deepseek-v4-flash 主模型 + Qwen-turbo fallback 进行 LLM 总结与 RAG 问答。
[关联模块] M-006
[数据格式]
  - 请求: HTTPS POST, JSON {model, messages, max_tokens, temperature}
  - 响应: JSON {choices: [{message: {content}}, usage: {input_tokens, output_tokens}]}
  - 限流: 按供应商配额（默认无客户端限流）
[安全要求]
  - API Key 仅从环境变量读取（DEEPSEEK_API_KEY / QWEN_API_KEY）
  - 不在日志中输出 API Key 或完整 prompt
  - 输入转写稿可能含个人隐私，需在用户协议中告知
[不处理范围]
  - 其他 LLM 供应商（OpenAI/Claude 等 V1.1 不支持）
  - 本地 LLM 推理（V1.1 不支持 ollama 等）
  - Embedding 模型（V1.1 不引入）
[来源标注] [SA:BP-006/BP-013/BR-001/BR-009/EX-021/EX-022/EX-024]
```

## B-004 既有 5 阶段管道边界

```
[边界编号] B-004
[边界类型] 输出边界（内部已有系统）
[边界描述] 系统触发 research-tool 既有 5 阶段管道（Collect → Clean → Extract → Organize → Report）对 raw/ 下的新 Markdown 进行处理。
[关联模块] M-008
[数据格式]
  - 输入: 文件路径（raw/<topic>/<video-id>.md）
  - 通信: subprocess pipe（既有管道协议）
  - 失败: 重试 1 次，仍失败返回 E_PIPE_001
[安全要求]
  - 显式注入 Collect 配置（"raw/ 下发现 video_*.md 即纳入"）[CE-009]
  - front matter 字段不污染既有字段（video_ 前缀隔离）[BR-003]
[不处理范围]
  - 修改既有管道内部实现（V1.1 仅触发，不修改）
  - 兼容旧版本管道（V1.1 仅与当前版本交互）
[来源标注] [SA:BP-010/EX-031/EX-032/BR-011/BR-020/BR-028] [调研:S-005/S-006]
```

## B-005 文件系统输出边界

```
[边界编号] B-005
[边界类型] 输出边界
[边界描述] 系统在当前工作目录的 raw/<topic>/ 下产出 Markdown 笔记、视频原文件、截图。
[关联模块] M-003, M-008, M-009
[数据格式]
  - Markdown 文件: UTF-8, YAML front matter + 正文
  - 视频文件: yt-dlp 默认格式（mp4/webm）
  - 截图: jpg ≤ 200KB
[安全要求]
  - 写入前检查磁盘空间
  - 文件权限默认 0o644
  - 不写入 ~/.ssh / /etc 等敏感目录
[不处理范围]
  - 云存储同步（V1.1 不支持 S3/OSS）
  - Git 自动提交（V1.1 不支持）
  - 数据库存储（V1.1 笔记仅落文件系统）
[来源标注] [SA:BP-010/DE-009/BR-020]
```

## B-006 缓存 DB 边界

```
[边界编号] B-006
[边界类型] 输出边界（持久化）
[边界描述] 系统在 ~/.cache/research-tool/cache.db 维护 sqlite 数据库，存储缓存条目。
[关联模块] M-004
[数据格式]
  - sqlite 3.x, WAL 模式
  - 表: cache_entries (cache_key PK, url_sha256, etag_or_lm, video_id, paths, created_at, ttl_seconds, hit_count, etag_revalidate_at)
  - 索引: IDX_url_sha256, IDX_created_at
[安全要求]
  - 目录权限 0o700
  - DB 文件权限 0o600
  - 不存储 Cookie / API Key
  - 跨进程访问需用文件锁
[不处理范围]
  - 远程缓存（V1.1 不支持 Redis/云端）
  - 多用户共享（V1.1 单用户）
  - 加密（V1.1 不加密 cache.db）
[来源标注] [SA:BP-004/DE-004/BR-026] [调研:S-102]
```

## B-007 日志与中间产物边界

```
[边界编号] B-007
[边界类型] 输出边界（可观测性 + 排障）
[边界描述] 系统在 ~/.cache/research-tool/logs/ 输出 JSON Lines 日志，在 ~/.cache/research-tool/transcripts/ 保留中间转写稿。
[关联模块] M-010, M-011
[数据格式]
  - 日志: JSON Lines, 按日期分文件 <date>.jsonl
  - 字段: ts, level, module, task_id, url_hash, step, duration_ms, code, msg
  - 中间产物: 原音频 / 转写稿 JSON / 部分 front matter
[安全要求]
  - 不在日志输出 Cookie / API Key / 完整视频内容
  - URL 入日志仅保留 sha256（不存明文）
  - 目录权限 0o700
[不处理范围]
  - 远程日志（V1.1 不支持 ELK/Loki）
  - Metrics（V1.1 不引入 Prometheus）
  - 链路追踪（V1.1 不引入 OpenTelemetry）
[来源标注] [SA:BP-016/DE-014/BR-016/EX-045]
```

---

## 边界覆盖度自检

| 外部交互点 | 对应边界 | 覆盖 |
|-----------|---------|------|
| 用户终端 | B-001 | ✓ |
| 视频平台 | B-002 | ✓ |
| LLM 服务 | B-003 | ✓ |
| 既有管道 | B-004 | ✓ |
| 笔记文件系统 | B-005 | ✓ |
| 缓存 DB | B-006 | ✓ |
| 日志 FS | B-007 | ✓ |
| Whisper 模型文件 | (B-007 扩展) | ✓（视为只读） |
| ffmpeg/deno/node/yt-dlp | (B-002 工具链) | ✓（通过 B-002 隐式覆盖） |

**边界覆盖率 = 100%**

---

> **本文件结束**。7 条边界均含输入/输出/不处理范围/安全要求。
