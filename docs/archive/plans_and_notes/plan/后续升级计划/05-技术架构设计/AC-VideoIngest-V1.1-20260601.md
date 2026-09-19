# Agent 协作方案 — VideoIngest V1.1（AR-001）

> **生成方**：AR-001
> **日期**：2026-06-01
> **Agent 角色数**：12（与模块数对齐，1:1 映射）
> **覆盖完整性**：12/12 完整协作定义（分工/通信/同步/冲突/故障恢复/资源配额）

---

## 协作流程图

```
用户 CLI (argv)
  ↓
AG-001 M-001 cli_bindings ──启动──→ AG-002 M-002 preflight
  ↓ (DE-012 PreflightReport)
  ↓
  ↓───── 并发分发 (IF-003) ─────┐
  ↓                              ↓
  ↓                       AG-012 M-012 concurrent_orchestrator
  ↓                              ↓ (URL → Task 列表 + Semaphore(3))
  ↓                              ↓
  ↓                              ↓─[任务1]─→ AG-003 M-003 downloader
  ↓                              ↓            ↓ (DE-003)
  ↓                              ↓            ↓
  ↓                              ↓       AG-004 M-004 cache_manager
  ↓                              ↓            ↓ (miss)
  ↓                              ↓            ↓
  ↓                              ↓       AG-005 M-005 transcriber
  ↓                              ↓            ↓ (DE-005)
  ↓                              ↓            ↓
  ↓                              ↓       AG-006 M-006 llm_client
  ↓                              ↓            ↓ (DE-006)
  ↓                              ↓            ↓
  ↓                              ↓       AG-007 M-007 notes_schema
  ↓                              ↓            ↓ (DE-006+)
  ↓                              ↓       AG-009 M-009 ffmpeg_wrapper
  ↓                              ↓            ↓ (DE-008)
  ↓                              ↓            ↓
  ↓                              ↓       AG-008 M-008 pipeline_adapter
  ↓                              ↓            ↓ (DE-009 落盘)
  ↓                              ↓
  ↓                       [结果汇总]
  ↓
AG-001 收集 ErrorRecord[] → AG-010 M-010 error_handler (IF-027 退出码仲裁)
  ↓
所有步骤均上报 → AG-011 M-011 structured_logger (横切，JSON Lines)
```

**调用类型**：AG-001 / AG-002 / AG-012 编排型；AG-003~AG-009 业务管道型；AG-010 / AG-011 横切型
**横切关系**：AG-010 / AG-011 被 9 个 Agent 依赖（IF-026 / IF-028）[TD:MP]
**无环检测**：A→B→C 单向，无回环 → 死锁检测通过 [soul 4.15]

---

## Agent 协作详细定义

### AG-001 M-001 CLI 绑定与编排器

```
[Agent角色] AG-001 M-001 cli_bindings
[职责边界]
  负责: argparse 解析、平台识别、LLMConfig 强制覆盖、并发调度分发、退出码仲裁
  不负责: 下载/转写/LLM 调用的具体实现（由 AG-003/AG-005/AG-006 负责）
[输入接口] argv 字符串数组 (DE-001 待解析)
[输出接口] DE-001 VideoURL[] / DE-010 ErrorRecord[] / RAG answer
[通信协议] 同进程内 Python 函数调用 (IF-001~IF-005)
[状态同步] 无状态（CLI 一次性启动）
[冲突解决] 错误码优先级仲裁委托 AG-010 (IF-027)
[故障恢复]
  崩溃检测: 进程退出 = 崩溃（CLI 无持久化）
  自动重启: 不重启（CLI 调用方控制）
  状态恢复: N/A
  任务转移: N/A
[资源配额]
  CPU: ≤ 5%
  内存: ≤ 50MB
  并发任务: ≤ 10 URLs (经 AG-012 限流到 3)
  超时: 启动 < 50ms
[生命周期]
  创建: 用户执行 research 命令
  运行: 启动 → preflight → 编排 → 汇总 → 退出
  销毁: 进程结束
[来源标注] [TD:MP:M-001] [TD:SR-001/SR-008]
```

### AG-002 M-002 预检模块

```
[Agent角色] AG-002 M-002 preflight
[职责边界]
  负责: 启动时 4 项环境依赖检查（deno/node/ffmpeg/Whisper medium）
  不负责: 阻塞后的恢复（仅报告）
[输入接口] null（启动时调用）
[输出接口] DE-012 PreflightReport
[通信协议] subprocess.run(deno --version / node / ffmpeg / Whisper 模型文件) + 内置 lru_cache(ttl=60s)
[状态同步] 有状态（DE-012 TTL 60s 单进程共享，BR-027）
[冲突解决] 报告由 AG-001 决策（YouTube 阻塞/B 站降级）
[故障恢复]
  崩溃检测: N/A（无独立进程）
  自动重启: N/A
  状态恢复: lru_cache 失效后自动重检
  任务转移: N/A
[资源配额]
  CPU: ≤ 1%（spawn 子进程）
  内存: ≤ 10MB
  并发: 1 次启动 + 1 次 TTL 后重检
  超时: < 2s
[生命周期]
  创建: CLI 启动时
  运行: 启动检查 + 60s TTL 复用
  销毁: CLI 退出
[来源标注] [TD:MP:M-002] [TD:SR-001] [AR洞察#1]
```

### AG-003 M-003 下载器

```
[Agent角色] AG-003 M-003 downloader
[职责边界]
  负责: yt-dlp 调用、Cookie 注入、本地文件解析、版本校验、错误重试
  不负责: 缓存查询（AG-004 负责）
[输入接口] DE-001 VideoURL / path (IF-007 / IF-008)
[输出接口] DE-003 DownloadTask
[通信协议] subprocess.run(yt-dlp --dump-json / yt-dlp URL) 异步
[状态同步] 无状态（每次任务独立）
[冲突解决] 错误码 E_DL_001 / E_DL_BILI_403 / E_DL_002_VERSION_TOO_OLD 上报 AG-010
[故障恢复]
  崩溃检测: 进程退出码 != 0
  自动重启: 重试 1 次（NF-009）
  状态恢复: 重新下载
  任务转移: 错误码上报，AG-012 决定是否继续其他任务
[资源配额]
  CPU: ≤ 30%（下载 IO 密集）
  内存: ≤ 200MB
  并发: 经 AG-012 Semaphore(3) 限流
  超时: 视频大小相关（10-300s）
[生命周期]
  创建: AG-012 调度
  运行: 下载/解析 → 产出 DE-003
  销毁: 任务结束
[来源标注] [TD:MP:M-003] [调研:S-001/S-101]
```

### AG-004 M-004 缓存管理器

```
[Agent角色] AG-004 M-004 cache_manager
[职责边界]
  负责: 双键（sha256+etag）缓存查询/写入/失效清理
  不负责: 业务处理（命中后仅返回 DE-004）
[输入接口] DE-001 + DE-003 / DE-006 + DE-009 / ttl
[输出接口] DE-004 CacheEntry | miss
[通信协议] sqlite3 + asyncio.Lock（IF-009 / IF-010 / IF-011）
[状态同步] 有状态（sqlite 持久化）
[冲突解决] asyncio.Lock 串行化写入（V1.1 单进程足够）[TD:SR-006]
[故障恢复]
  崩溃检测: sqlite 不可用 → E_CK_001
  自动重启: N/A（主进程内）
  状态恢复: 启动时 sqlite.connect() + WAL checkpoint
  任务转移: 缓存降级 → 主链继续（不阻塞）
[资源配额]
  CPU: ≤ 5%
  内存: ≤ 50MB
  并发: 1 写 + N 读（WAL 模式）
  超时: < 50ms（query）/< 200ms（write）
[生命周期]
  创建: CLI 启动
  运行: 持续响应查询/写入
  销毁: CLI 退出（DB 连接关闭）
[来源标注] [TD:MP:M-004] [TD:SR-005/SR-006] [调研:S-102]
```

### AG-005 M-005 转写器

```
[Agent角色] AG-005 M-005 transcriber
[职责边界]
  负责: faster-whisper / bcut / groq 三引擎调度、CER 估算、RAM 探测降档、音频指纹二级缓存
  不负责: 视频下载（AG-003 负责）
[输入接口] audio_fingerprint + audio_path
[输出接口] DE-005 Transcript
[通信协议] 同进程 Python 调用（faster-whisper 是 C++ 扩展）
[状态同步] 有状态（音频指纹缓存 in-memory + 持久化可选）
[冲突解决] 引擎失败顺序：whisper → bcut → groq
[故障恢复]
  崩溃检测: 引擎异常 / OOM
  自动重启: 自动降档 base/small（RAM < 8GB 触发）
  状态恢复: 重新加载模型
  任务转移: 引擎全失败 → E_TR_001 + 中间产物保留
[资源配额]
  CPU: ≤ 80%（计算密集）
  内存: ≤ 4GB（medium 档），2GB（small），1GB（base）
  并发: 经 AG-012 限流到 3 (3 × medium = 12GB 上限)
  超时: 5-30min（30min 视频 medium 档）
[生命周期]
  创建: AG-012 调度
  运行: 加载模型 → 转写 → 产出 DE-005
  销毁: 任务结束（模型常驻内存直到 CLI 退出）
[来源标注] [TD:MP:M-005] [TD:SR-004] [调研:S-004]
```

### AG-006 M-006 LLM 客户端

```
[Agent角色] AG-006 M-006 llm_client
[职责边界]
  负责: Deepseek-v4-flash 主调用、Qwen-turbo fallback、prompt 拼装、front matter 解析、字段名前缀校验、3 段式输出
  不负责: 笔记组装（AG-007 负责）
[输入接口] DE-005 + style / front_matter dict / query
[输出接口] DE-006 LLMSummary / validated dict / answer
[通信协议] httpx async POST (HTTPS 443)
[状态同步] 无状态（每次调用独立）
[冲突解决] 5xx 3 次后切换 fallback（仅 1 次切换）
[故障恢复]
  崩溃检测: HTTP 5xx / timeout / connect error
  自动重启: 重试 3 次（5xx），切换 fallback 1 次
  状态恢复: N/A
  任务转移: 双模型均失败 → E_LLM_001 + 中间产物保留
[资源配额]
  CPU: ≤ 5%
  内存: ≤ 100MB
  并发: 经 AG-012 限流到 3
  超时: 单次调用 ≤ 30s
[生命周期]
  创建: AG-012 调度
  运行: HTTP 请求 → 重试/切换 → 产出 DE-006
  销毁: 任务结束
[来源标注] [TD:MP:M-006] [TD:SR-002/PC-005] [调研:S-003]
```

### AG-007 M-007 笔记组装器

```
[Agent角色] AG-007 M-007 notes_schema
[职责边界]
  负责: front matter 解析、VideoMeta 注入、章节降级注入、Markdown 正文组装、截图引用嵌入
  不负责: 实际 LLM 调用 / 实际截图（AG-006/AG-009 负责）
[输入接口] DE-006 + DE-002 / DE-002 / paths + md / DE-002/006/007/008 / LLM 章节数
[输出接口] DE-006+ / front_matter / md+ / 引用源 / 完整 md / fallback 策略
[通信协议] 同进程 Python 函数调用（IF-016~IF-021）
[状态同步] 无状态（每次任务独立）
[冲突解决] 章节降级 = 等距切片 5min（IF-021 扩展点 V1.2 完善）
[故障恢复]
  崩溃检测: YAML 解析失败 / 字段缺失
  自动重启: 章节降级 / 字段 fallback
  状态恢复: 局部重算
  任务转移: 关键字段缺失 → E_LLM_002_CHAPTERS_FALLBACK
[资源配额]
  CPU: ≤ 10%
  内存: ≤ 50MB
  并发: 1（任务级）
  超时: < 200ms（总装）
[生命周期]
  创建: AG-008 调度
  运行: 解析 → 注入 → 组装 → 产出 md
  销毁: 任务结束
[来源标注] [TD:MP:M-007] [TD:SR-002/SR-003] [调研:S-202]
```

### AG-008 M-008 管道适配器

```
[Agent角色] AG-008 M-008 pipeline_adapter
[职责边界]
  负责: Markdown 落盘、5 阶段管道触发、tags 合并与冲突仲裁
  不负责: 笔记内容生成（AG-007 负责）
[输入接口] md + topic + video_id / file_path / new_tags + existing
[输出接口] DE-009 + 文件路径 / stages_run / merged_tags
[通信协议] subprocess + 既有管道协议
[状态同步] 有状态（落盘文件系统）
[冲突解决] tags 冲突保留两版 + is_duplicate_resolved=true
[故障恢复]
  崩溃检测: 落盘失败 / 管道失败
  自动重启: 重试 1 次（NF-009）
  状态恢复: 重新落盘 / 重新触发
  任务转移: 仍失败 → E_PIPE_001 + 中间产物保留
[资源配额]
  CPU: ≤ 20%
  内存: ≤ 100MB
  并发: 1（任务级，但 3 URL 任务可并行）
  超时: 落盘 < 1s / 管道 1-30s
[生命周期]
  创建: AG-007 完成
  运行: 落盘 → 触发 → 汇总
  销毁: 任务结束
[来源标注] [TD:MP:M-008] [TD:SR-007]
```

### AG-009 M-009 截图器

```
[Agent角色] AG-009 M-009 ffmpeg_wrapper
[职责边界]
  负责: ffmpeg I 帧抽取、压缩 ≤ 200KB、5 张截图产出
  不负责: 截图嵌入到 md（AG-007 负责）
[输入接口] video_path + video_id
[输出接口] DE-008 ScreenshotFrame[]
[通信协议] subprocess.run(ffmpeg) 或 ffmpeg-python 异步流
[状态同步] 无状态
[冲突解决] 失败静默跳过（E_FM_001 warning 不阻塞）
[故障恢复]
  崩溃检测: ffmpeg exit != 0
  自动重启: N/A（静默跳过）
  状态恢复: 继续主链
  任务转移: 截图失败 → 笔记无图 → 用户感知
[资源配额]
  CPU: ≤ 30%
  内存: ≤ 200MB
  并发: 经 AG-012 限流
  超时: 1-3s/视频
[生命周期]
  创建: AG-007 调度
  运行: ffmpeg 调用 → 截图 → 压缩
  销毁: 任务结束
[来源标注] [TD:MP:M-009] [调研:S-201]
```

### AG-010 M-010 错误处理器

```
[Agent角色] AG-010 M-010 error_handler
[职责边界]
  负责: 异常捕获、错误码字典匹配、3 段式错误信息生成、中间产物保留、CLI 退出码仲裁
  不负责: 业务处理
[输入接口] exception + task_id / ErrorRecord[]
[输出接口] DE-010 ErrorRecord / cli_exit_code
[通信协议] 同进程内 Python 异常传递（IF-026 / IF-027）
[状态同步] 无状态
[冲突解决] 退出码仲裁 = 错误码优先级字典（403 > 401 > 500 > 0）[CE-010]
[故障恢复]
  崩溃检测: 错误码未注册 → E_SYS_001
  自动重启: N/A
  状态恢复: 完整堆栈上报
  任务转移: 错误码仲裁后 AG-001 决定是否部分任务继续
[资源配额]
  CPU: ≤ 1%
  内存: ≤ 10MB
  并发: 全局唯一（错误处理中心化）
  超时: < 50ms
[生命周期]
  创建: CLI 启动
  运行: 监听所有 Agent 异常
  销毁: CLI 退出
[来源标注] [TD:MP:M-010] [TD:SR-008]
```

### AG-011 M-011 结构化日志器

```
[Agent角色] AG-011 M-011 structured_logger
[职责边界]
 负责: JSON Lines 日志输出、URL 仅存 sha256、按日切分
 不负责: 业务处理
[输入接口] {level, module, task_id, url_hash, step, duration_ms, code, msg}
[输出接口] fsync 落盘 ~/.cache/research-tool/logs/<date>.jsonl
[通信协议] 同进程 logging.Handler（IF-028）
[状态同步] 有状态（文件句柄）
[冲突解决] 多模块并发 append（logging 内置锁）
[故障恢复]
  崩溃检测: 日志路径不可写
  自动重启: 降级 stderr（不阻塞）
  状态恢复: 持续重试写文件
  任务转移: 降级后用户可重定向 stderr
[资源配额]
  CPU: ≤ 2%
 内存: ≤ 20MB
 并发: 1（logging 内置锁）
 超时: < 5ms
[生命周期]
 创建: CLI 启动
 运行: 持续 append
 销毁: CLI 退出（flush + close）
[来源标注] [TD:MP:M-011]
```

### AG-012 M-012 并发编排器

```
[Agent角色] AG-012 M-012 concurrent_orchestrator
[职责边界]
  负责: asyncio.Semaphore(3) 限流、URL 列表 gather、CPU/MEM 资源探测降级
  不负责: 业务实现
[输入接口] URL[] + task_func / cpu / mem
[输出接口] Result[] / concurrency_level
[通信协议] asyncio.gather + Semaphore
[状态同步] 无状态（每次调度独立）
[冲突解决] 资源紧张时降级到 2 并发（IF-030）
[故障恢复]
  崩溃检测: 任务异常 (gather return_exceptions=True)
  自动重启: 任务独立异常不影响其他任务
  状态恢复: gather 收集所有结果
  任务转移: 失败任务由 AG-010 登记
[资源配额]
  CPU: ≤ 5%
  内存: ≤ 20MB
  并发: 3 (V1.1) / 2 (降级)
  超时: 启动 < 50ms
[生命周期]
  创建: AG-001 启动并发时
  运行: gather → 限流 → 资源探测
  销毁: 任务结束
[来源标注] [TD:MP:M-012] [TD:ADR-002/ADR-006] [TD:SR-004]
```

---

## 协作闭环检测（soul 4.15）

| 检测项 | 结果 | 依据 |
|--------|------|------|
| 死锁检测 | ✓ 通过 | 依赖图 DAG 无环，AG-001 → AG-012 → AG-003/005/006/009 单向 |
| 活锁检测 | ✓ 通过 | 每步有终止条件（IF-NNN 返回值 / 错误码） |
| 超时机制 | ✓ 通过 | 12 Agent 全部定义超时（最快 5ms，最慢 30min） |
| 降级策略 | ✓ 通过 | AG-002 preflight 失败 → 标记 SIM-STUB / AG-005 引擎降级 / AG-006 fallback 切换 / AG-010 退出码仲裁 |
| 状态一致性 | ✓ 通过 | 关键状态 DE-004/DE-009/DE-012/DE-013 均有显式存储与同步点 |

**协作无死锁/活锁风险** [soul R26]。

---

> **本文件结束**。12 Agent 完整协作定义，6 维度（分工/通信/同步/冲突/故障恢复/资源）全覆盖，协作闭环 5/5 检测通过。
