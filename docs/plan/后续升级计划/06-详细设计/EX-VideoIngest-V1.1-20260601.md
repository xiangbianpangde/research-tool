# 异常处理策略 — VideoIngest V1.1（DD-001）

> **生成方**：DD-001
> **日期**：2026-06-01
> **边界数**：7（SEC-001~SEC-007）
> **覆盖率**：100%（7/7 含异常类型/触发条件/处理流程/降级策略/告警机制）

---

## EX-001 用户 CLI 输入边界异常处理（SEC-001）

```
[异常编号] EX-001
[关联边界] SEC-001
[异常类型] 业务异常
[触发条件]
  - 非法 URL（不在 5 种形态中）
  - 参数不在白名单 8 个中
  - --video-file 指向黑名单路径（~/.ssh / /etc / 绝对路径越界）
  - --cookie-file 权限 != 0o600
  - URL 数量 > 10
[处理流程]
  步骤1: argparse 拒绝非法参数（SystemExit）
  步骤2: 黑名单路径检查（path.resolve() + prefix 匹配）
  步骤3: Cookie 权限检查（os.stat().st_mode & 0o777 == 0o600）
  步骤4: 异常 → M-010 登记 → M-011 日志
  步骤5: 打印用户友好的错误信息
[降级策略]
  降级方式: 拒绝执行（无降级）
  降级数据: 错误码 + 安装命令指引
[告警机制]
  告警级别: ERROR
  通知方式: stderr
  通知对象: 用户
[对应错误码] E_DL_001 / E_DL_001_DENO_MISSING / E_DL_LOCAL_001 / E_LIM_001
[来源标注] [AR:SEC-001] [AR:B-001] [AR:BR-010]
```

## EX-002 外部视频平台边界异常处理（SEC-002）

```
[异常编号] EX-002
[关联边界] SEC-002
[异常类型] 网络异常 / 业务异常
[触发条件]
  - 平台返回 403 / 412（不绕过）
  - 4K / 付费 / 限地区内容
  - Cookie 失效
  - YouTube PO Token 失效
  - 网络超时（> 30s）
[处理流程]
  步骤1: subprocess 捕获 exit code
  步骤2: 解析 stderr 错误码（yt-dlp 输出）
  步骤3: 异常 → M-010 登记 → M-011 日志（URL sha256）
  步骤4: 重试 1 次（仅网络错误，不重试 403）
  步骤5: 仍失败 → 返回错误码 + 中间产物保留
[降级策略]
  降级方式: 跳过该 URL 继续其他 URL
  降级数据: 错误码 + 失败 URL 列表
[告警机制]
  告警级别: ERROR（403）/ WARN（网络）
  通知方式: stderr + M-011 日志
  通知对象: 用户 + 监控
[对应错误码] E_DL_001 / E_DL_BILI_403 / E_DL_002_VERSION_TOO_OLD / E_DL_003_NETWORK
[来源标注] [AR:SEC-002] [AR:B-002] [调研:S-001]
```

## EX-003 外部 LLM API 边界异常处理（SEC-003）

```
[异常编号] EX-003
[关联边界] SEC-003
[异常类型] 网络异常 / 安全异常
[触发条件]
  - API Key 缺失（DEEPSEEK_API_KEY / QWEN_API_KEY 未设置）
  - HTTPS 5xx（Deepseek/Qwen）
  - HTTPS 4xx（rate limit / 余额不足）
  - Token 超限（input > 20k / output > 4k）
  - Prompt 注入攻击
  - 网络超时（> 30s）
[处理流程]
  步骤1: 检查环境变量（启动时）
  步骤2: HTTPS 调用（httpx）
  步骤3: 5xx → 重试 3 次（指数退避 1s/2s/4s）
  步骤4: 仍 5xx → 切换 fallback（Deepseek → Qwen，仅 1 次切换）
  步骤5: 双模型均失败 → 截断 input（20k → 10k 再次重试）
  步骤6: 仍失败 → 返回 E_LLM_001 + 中间产物保留
  步骤7: 4xx → 不重试（rate limit 等待 60s 后 1 次重试）
[降级策略]
  降级方式: 主模型 → fallback 模型 → 截断 → 错误
  降级数据: 部分 summary（仅 front matter）或错误码
[告警机制]
  告警级别: WARN（5xx retry）/ ERROR（双模型均失败）
  通知方式: stderr + M-011 日志（token 计数 + 模型名）
  通知对象: 用户 + 监控
[对应错误码] E_LLM_001 / E_LLM_002_CHAPTERS_FALLBACK / E_LLM_003_FALLBACK
[安全考虑]
  - API Key 不入日志（敏感字段过滤）
  - Token 计数（不记录 prompt 内容）
  - 输入截断（隐私保护）
[来源标注] [AR:SEC-003] [AR:B-003] [AR:BR-001/BR-009/EX-024] [AR:TD-AR-002 vault 候选]
```

## EX-004 既有 5 阶段管道边界异常处理（SEC-004）

```
[异常编号] EX-004
[关联边界] SEC-004
[异常类型] 业务异常 / 集成异常
[触发条件]
  - Collect 配置未含 `raw/<topic>/video_*.md` 纳入规则
  - 字段污染（LLM 输出非 video_ 前缀污染既有字段）
  - subprocess 异常（管道崩溃）
  - tags 冲突未解决
[处理流程]
  步骤1: 启动时检查 Collect 配置（CE-009 注入 1 行）
  步骤2: 字段名前缀校验（API-014 → 重试 1 次 → E_LLM_001）
  步骤3: subprocess 调用（带 timeout 30s）
  步骤4: tags 冲突保留两版 + is_duplicate_resolved=true
  步骤5: 异常 → M-010 登记 → 重试 1 次 → 仍失败 → 中间产物保留
[降级策略]
  降级方式: 落盘但不触发管道（保留文件供用户手动触发）
  降级数据: 完整 md + 错误码
[告警机制]
  告警级别: ERROR
  通知方式: stderr + M-011 日志
  通知对象: 用户
[对应错误码] E_PIPE_001 / E_LLM_001（字段污染）
[来源标注] [AR:SEC-004] [AR:B-004] [AR:SR-002/SR-007] [CE-009]
```

## EX-005 文件系统输出边界异常处理（SEC-005）

```
[异常编号] EX-005
[关联边界] SEC-005
[异常类型] 系统异常 / 业务异常
[触发条件]
  - 路径遍历（写入 ~/.ssh / /etc）
  - 磁盘剩余 < 100MB
  - 工作目录无写权限
  - 文件权限 chmod 失败
[处理流程]
  步骤1: path.resolve() + 工作目录前缀校验
  步骤2: shutil.disk_usage() < 100MB → 拒绝
  步骤3: 写入失败 → 重试 1 次 → E_PIPE_001
  步骤4: chmod 0o644 失败 → 警告 + 继续（不阻塞）
  步骤5: 异常 → M-010 登记
[降级策略]
  降级方式: 拒绝写入（无降级）
  降级数据: 错误码 + 路径
[告警机制]
  告警级别: ERROR
  通知方式: stderr + M-011 日志
  通知对象: 用户
[对应错误码] E_PIPE_001 / E_FS_001_DISK_FULL / E_FS_002_PATH_DENIED
[来源标注] [AR:SEC-005] [AR:B-005] [AR:BR-020]
```

## EX-006 缓存 DB 边界异常处理（SEC-006）

```
[异常编号] EX-006
[关联边界] SEC-006
[异常类型] 系统异常 / 数据异常
[触发条件]
  - sqlite 不可用（文件损坏 / 权限不足）
  - 跨进程 WAL 锁竞争
  - DB 满（磁盘满）
  - 加密缺失（V1.1 不加密，按 B-006 §不处理范围）
[处理流程]
  步骤1: sqlite3.connect() + WAL 模式 + 0o600 权限
  步骤2: 连接失败 → E_CK_001 降级
  步骤3: 写入失败 → 重试 1 次 → E_CK_001
  步骤4: 损坏检测 → 删除条目 → 重新生成
  步骤5: 锁等待 > 100ms → WARN 日志（不阻塞）
  步骤6: 异常 → M-010 登记
[降级策略]
  降级方式: 缓存降级（不阻塞主链，继续无缓存执行）
  降级数据: 空缓存（miss 永远返回 None）
[告警机制]
  告警级别: WARN（lock wait）/ ERROR（DB fail）
  通知方式: stderr + M-011 日志
  通知对象: 用户
[对应错误码] E_CK_001 / E_CK_002_WRITE_FAIL
[V2.0 候选] 加密 cache.db（TD-AR-005）+ SQLCipher
[来源标注] [AR:SEC-006] [AR:B-006] [AR:SR-006] [调研:S-102] [AR:TD-AR-005]
```

## EX-007 日志与中间产物边界异常处理（SEC-007）

```
[异常编号] EX-007
[关联边界] SEC-007
[异常类型] 安全异常 / 系统异常
[触发条件]
  - 敏感信息泄露（API Key / Cookie / 完整 prompt 入日志）
  - URL 明文（应仅存 sha256）
  - 日志路径不可写
  - 目录权限 != 0o700
  - 中间产物（转写稿）含个人隐私
[处理流程]
  步骤1: 敏感字段过滤（API_KEY / COOKIE / PROMPT 关键词）
  步骤2: URL → sha256 替换
  步骤3: 日志路径不可写 → 降级 stderr
  步骤4: 目录权限 != 0o700 → 自动 chmod（仅 V1.1 启动时）
  步骤5: 中间产物保留时 0o700 权限
  步骤6: 异常 → M-010 登记（不阻塞）
[降级策略]
  降级方式: 日志降级 stderr（不阻塞）+ 中间产物删除
  降级数据: 仅 stderr 输出
[告警机制]
  告警级别: WARN（敏感字段命中）/ ERROR（路径不可写）
  通知方式: stderr
  通知对象: 用户
[对应错误码] E_LOG_001_WRITE_FAIL / E_LOG_002_SENSITIVE_DETECTED
[合规要求] 用户协议告知"转写稿可能含个人隐私"
[来源标注] [AR:SEC-007] [AR:B-007] [AR:BR-016] [AR:DE-014]
```

---

## 异常处理覆盖汇总

| 边界 | 异常类型数 | 错误码数 | 降级策略 | 告警机制 | 通过 |
|------|----------|---------|---------|---------|------|
| EX-001 (SEC-001) | 4 | 4 | 拒绝 | ERROR | ✓ |
| EX-002 (SEC-002) | 4 | 4 | 跳过 | ERROR/WARN | ✓ |
| EX-003 (SEC-003) | 6 | 3 | 多级降级 | WARN/ERROR | ✓ |
| EX-004 (SEC-004) | 4 | 2 | 落盘不触发 | ERROR | ✓ |
| EX-005 (SEC-005) | 4 | 3 | 拒绝 | ERROR | ✓ |
| EX-006 (SEC-006) | 4 | 2 | 缓存降级 | WARN/ERROR | ✓ |
| EX-007 (SEC-007) | 5 | 2 | stderr 降级 | WARN/ERROR | ✓ |

**覆盖率：100%（7/7 边界全部有异常处理策略）**

---

## 错误码字典汇总（25+ 错误码）

| 错误码 | 含义 | 触发模块 | 严重性 |
|--------|------|---------|--------|
| E_DL_001 | 非法 URL | M-001/M-003 | ERROR |
| E_DL_001_DENO_MISSING | Deno 缺失 | M-002 | ERROR（YouTube 阻塞） |
| E_DL_002_VERSION_TOO_OLD | yt-dlp 版本过低 | M-003 | ERROR |
| E_DL_003_NETWORK | 网络错误 | M-003 | WARN |
| E_DL_BILI_403 | B 站 403 | M-003 | ERROR |
| E_DL_LOCAL_001 | 本地文件不存在 | M-003 | ERROR |
| E_DL_LOCAL_002 | 本地文件格式不支持 | M-003 | ERROR |
| E_CK_001 | 缓存不可用 | M-004 | WARN（降级） |
| E_CK_002_WRITE_FAIL | 缓存写入失败 | M-004 | WARN（重试 1 次） |
| E_TR_001 | 转写三引擎全失败 | M-005 | ERROR |
| E_LLM_001 | LLM 双模型均失败 | M-006 | ERROR |
| E_LLM_002_CHAPTERS_FALLBACK | LLM 章节缺失降级 | M-006 | WARN |
| E_LLM_003_FALLBACK | LLM 切换 fallback | M-006 | WARN |
| E_LIM_001 | URL 数量超限 | M-001 | ERROR |
| E_PIPE_001 | 落盘/管道失败 | M-008 | ERROR |
| E_FM_001 | ffmpeg 失败 | M-009 | WARN（静默跳过） |
| E_FS_001_DISK_FULL | 磁盘满 | M-008 | ERROR |
| E_FS_002_PATH_DENIED | 路径遍历 | M-008 | ERROR |
| E_LOG_001_WRITE_FAIL | 日志写入失败 | M-011 | WARN（降级 stderr） |
| E_LOG_002_SENSITIVE_DETECTED | 敏感字段命中 | M-011 | WARN |
| E_SYS_001 | 未注册错误码 | M-010 | ERROR（内部） |

**错误码总数：21 个，覆盖 7 边界所有异常场景**。

---

> **本文件结束**。7 边界 + 21 错误码 + 7 降级策略 + 7 告警机制 100% 覆盖。
