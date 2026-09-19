# 部署架构图 — VideoIngest V1.1（AR-001）

> **生成方**：AR-001
> **日期**：2026-06-01
> **部署模式**：单机 CLI（无服务端、无 K8s、无容器编排）[TD:SA-D]
> **组件数**：12（与模块数 1:1 对应，DP-001~DP-012）

---

## 1. 部署拓扑（沿用 TD 部署视图，补充 AR 技术细节）

```
+--------------------------------------------------------------------------+
|  目标用户主机（V1.1：单机部署；V2.0 候选：容器化/Serverless）            |
|                                                                          |
|  +-----------------------------------------------------------------+   |
|  |  OS: Windows 11 / macOS 14+ / Ubuntu 22.04+                     |   |
|  |  CPU: 4+ 核                                                    |   |
|  |  RAM: ≥8GB (推荐 16GB for medium 档)                           |   |
|  |  Disk: ≥10GB 可用 (含 Whisper medium 模型 ~1.5GB)              |   |
|  +-----------------------------------------------------------------+   |
|                                                                          |
|  +-----------------------------------------------------------------+   |
|  |  Python 3.11+ runtime                                          |   |
|  |   ├── research (entry point → DP-001)                           |   |
|  |   ├── research_tool/ (核心包)                                   |   |
|  |   └── bilinode_partial/ (移植子目录)                            |   |
|  +-----------------------------------------------------------------+   |
|                                                                          |
|  +--------------------+  +--------------------+                          |
|  | 外部依赖（PATH）  |  | 本地依赖（用户态）  |                          |
|  | deno >= 2.0      |  | ~/.cache/huggingface|                          |
|  | node >= 18.0.0   |  |   /hub/models--     |                          |
|  | ffmpeg >= 6.0    |  |   Systran--faster-  |                          |
|  | yt-dlp >= 2023.  |  |   whisper-medium    |                          |
|  |   07.06          |  |   (~1.5GB)          |                          |
|  | python >= 3.11   |  |                     |                          |
|  +--------------------+  +--------------------+                          |
|                                                                          |
|  +-----------------------------------------------------------------+   |
|  |  本地文件系统布局                                                |   |
|  |   ./raw/<topic>/<video-id>.md          # BP-010 落盘 (B-005)    |   |
|  |   ./raw/<topic>/assets/*.mp4           # 视频原文件              |   |
|  |   ~/.cache/research-tool/logs/         # BP-016 日志 (B-007)   |   |
|  |   ~/.cache/research-tool/cache.db      # BP-004 sqlite (B-006) |   |
|  |   ~/.cache/research-tool/transcripts/  # 中间产物 (B-007 扩展)  |   |
|  +-----------------------------------------------------------------+   |
|                                                                          |
|  +-----------------------------------------------------------------+   |
|  |  网络出口（仅出站）                                              |   |
|  |   - api.deepseek.com        (M-006 Deepseek, 端口 443)         |   |
|  |   - dashscope.aliyuncs.com  (M-006 Qwen fallback, 443)         |   |
|  |   - api.bilibili.com        (M-003 bilibili, 443)              |   |
|  |   - www.youtube.com         (M-003 youtube, 443)               |   |
|  |   - deno.land               (首次安装)                          |   |
|  +-----------------------------------------------------------------+   |
+--------------------------------------------------------------------------+
```

[TD:SA-D] [调研:S-002]

---

## 2. 环境依赖矩阵（沿用 TD，AR 补充技术细节）

| 依赖 | 来源 | V1.1 要求 | 检测方式 | 缺失策略 | 阻塞性 | AR 补充 |
|------|------|----------|---------|---------|--------|---------|
| Python | 用户预装 | ≥ 3.11 | sys.version | 终止启动 | 是 | pyproject.toml requires-python 锁 |
| deno | 用户安装 | ≥ 2.0 | `deno --version` (BP-002) | 打印安装命令 + E_DL_001_DENO_MISSING | YouTube 阻塞 | lru_cache TTL 60s [AR洞察#1] |
| node | 用户预装 | ≥ 18.0.0 | `node --version` (BP-002) | 警告 + SIM-STUB 标记 | 否 | yt-dlp 备用 JS 运行时 |
| ffmpeg | 系统包管理器 | ≥ 6.0 | `ffmpeg -version` (BP-002) | 警告 + SIM-STUB；阻塞截图 | 截图阻塞 | ffmpeg-python ≥ 0.2.0 |
| yt-dlp | pip | ≥ 2023.07.06 | `yt-dlp --version` (BP-003) | 升级提示 + E_DL_002_VERSION_TOO_OLD | 是 | pyproject 锁版本 |
| faster-whisper | pip (含模型) | medium 档 | 文件存在 (BP-002) | 降档 bcut/groq | 否 | ctranslate2 ≥ 3.0 联动锁 |
| faster-whisper-medium 模型 | huggingface | ~1.5GB | 文件存在 (BP-002) | 降档 base/small | 否 | 首次启动自动下载 |
| sqlite | Python 内置 | ≥ 3.0 | import sqlite3 | 缓存降级不阻塞 (EX-014) | 否 | WAL 模式 |
| httpx | pip | ≥ 0.27 | import httpx | 终止启动 | 是 | pyproject 锁 |
| PyYAML | pip | ≥ 6.0 | import yaml | 终止启动 | 是 | pyproject 锁 |

**pyproject.toml 锁版本下限（AR 推断）**：
```toml
requires-python = ">=3.11"
dependencies = [
  "yt-dlp>=2023.7.6",
  "faster-whisper>=1.1.1",
  "ctranslate2>=3.0",        # AR洞察#3 联动锁
  "ffmpeg-python>=0.2.0",
  "youtube-transcript-api>=1.0.0",
  "httpx>=0.27",
  "PyYAML>=6.0",
]
```

---

## 3. 部署组件清单（DP-001 ~ DP-012）

### DP-001 M-001 CLI 绑定与编排器

```
[部署组件] DP-001
[对应模块] M-001
[运行环境] 进程内（Python 3.11+）
[资源配置] CPU ≤ 5% / 内存 ≤ 50MB / 无持久化
[配置管理] CLI argv（无配置文件，V1.2 引入 M-013）
[依赖组件] DP-002 (preflight 必须先跑) / DP-012 (并发编排)
[启动顺序] 1（用户入口）
[健康检查] N/A（CLI 一次性）
[监控告警] M-011 日志记录 argv + DE-001 解析结果
[来源标注] [TD:MP:M-001]
```

### DP-002 M-002 预检模块

```
[部署组件] DP-002
[对应模块] M-002
[运行环境] 进程内（subprocess 调用 4 个外部命令）
[资源配置] CPU ≤ 1% / 内存 ≤ 10MB / spawn 4 个子进程
[配置管理] lru_cache TTL 60s [AR洞察#1]
[依赖组件] deno / node / ffmpeg / Whisper medium 模型
[启动顺序] 1（与 DP-001 并行启动）
[健康检查] subprocess exit 0
[监控告警] 缺失时返回 E_DL_001_DENO_MISSING（YouTube 阻塞）
[来源标注] [TD:MP:M-002] [调研:S-002/S-007] [AR洞察#1]
```

### DP-003 M-003 下载器

```
[部署组件] DP-003
[对应模块] M-003
[运行环境] subprocess(yt-dlp)
[资源配置] CPU ≤ 30% / 内存 ≤ 200MB / 网络 IO 密集
[配置管理] DE-011 CookieConfig（--cookie-file 0o600）
[依赖组件] DP-002 (preflight YouTube 需 deno)
[启动顺序] 2（编排器调度）
[健康检查] yt-dlp --version ≥ 2023.07.06
[监控告警] 403 / 版本过低 → E_DL_002_VERSION_TOO_OLD
[来源标注] [TD:MP:M-003] [调研:S-001/S-101]
```

### DP-004 M-004 缓存管理器

```
[部署组件] DP-004
[对应模块] M-004
[运行环境] sqlite3 + asyncio.Lock
[资源配置] CPU ≤ 5% / 内存 ≤ 50MB / sqlite WAL 模式
[配置管理] 文件权限 0o600 / 目录权限 0o700
[依赖组件] 无（启动即打开 DB）
[启动顺序] 1（CLI 启动时连接）
[健康检查] sqlite3.connect() + WAL checkpoint
[监控告警] E_CK_001 缓存降级
[来源标注] [TD:MP:M-004] [TD:SR-005/SR-006] [调研:S-102] [TD:ADR-004]
```

### DP-005 M-005 转写器

```
[部署组件] DP-005
[对应模块] M-005
[运行环境] 进程内 Python + faster-whisper C++ 扩展
[资源配置] CPU ≤ 80% / 内存 ≤ 4GB（medium 档，3 并发 12GB）
[配置管理] ~/.cache/huggingface/ 默认路径
[依赖组件] ffmpeg（音频解码）
[启动顺序] 2（编排器调度）
[健康检查] Whisper medium 模型文件存在
[监控告警] OOM → 自动降档 base/small
[来源标注] [TD:MP:M-005] [TD:SR-004] [调研:S-004]
```

### DP-006 M-006 LLM 客户端

```
[部署组件] DP-006
[对应模块] M-006
[运行环境] httpx async HTTPS
[资源配置] CPU ≤ 5% / 内存 ≤ 100MB
[配置管理] 环境变量 DEEPSEEK_API_KEY / QWEN_API_KEY
[依赖组件] 网络出口 443
[启动顺序] 2（编排器调度）
[健康检查] HTTPS 200（Deepseek / Qwen）
[监控告警] 5xx 3 次 → 切 fallback
[来源标注] [TD:MP:M-006] [调研:S-003]
```

### DP-007 M-007 笔记组装器

```
[部署组件] DP-007
[对应模块] M-007
[运行环境] 进程内 Python（PyYAML）
[资源配置] CPU ≤ 10% / 内存 ≤ 50MB
[配置管理] 无（V1.1 固定模板）
[依赖组件] DP-006 (LLM 输出) / DP-009 (截图)
[启动顺序] 3（依赖 DP-006/009）
[健康检查] PyYAML safe_load 测试
[监控告警] YAML 解析失败 → E_LLM_001
[来源标注] [TD:MP:M-007] [调研:S-005/S-202]
```

### DP-008 M-008 管道适配器

```
[部署组件] DP-008
[对应模块] M-008
[运行环境] subprocess(既有管道)
[资源配置] CPU ≤ 20% / 内存 ≤ 100MB
[配置管理] 既有管道 Collect 配置注入 1 行 [CE-009]
[依赖组件] 既有 5 阶段管道（V1.1 不修改）
[启动顺序] 4（依赖 DP-007 落盘）
[健康检查] subprocess exit 0
[监控告警] 失败重试 1 次 → E_PIPE_001
[来源标注] [TD:MP:M-008] [TD:SR-007] [CE-009]
```

### DP-009 M-009 截图器

```
[部署组件] DP-009
[对应模块] M-009
[运行环境] subprocess(ffmpeg) / ffmpeg-python
[资源配置] CPU ≤ 30% / 内存 ≤ 200MB
[配置管理] 无（CLI 参数固定）
[依赖组件] ffmpeg ≥ 6.0
[启动顺序] 3（与 DP-006 并行）
[健康检查] ffmpeg -version
[监控告警] E_FM_001 静默跳过
[来源标注] [TD:MP:M-009] [调研:S-201]
```

### DP-010 M-010 错误处理器

```
[部署组件] DP-010
[对应模块] M-010
[运行环境] 进程内（错误码字典 + 模板）
[资源配置] CPU ≤ 1% / 内存 ≤ 10MB
[配置管理] 错误码字典（冻结）
[依赖组件] 无
[启动顺序] 1（CLI 启动）
[健康检查] N/A
[监控告警] 未注册错误码 → E_SYS_001
[来源标注] [TD:MP:M-010] [TD:SR-008]
```

### DP-011 M-011 结构化日志器

```
[部署组件] DP-011
[对应模块] M-011
[运行环境] logging.Handler + JSON Lines 文件
[资源配置] CPU ≤ 2% / 内存 ≤ 20MB
[配置管理] 目录权限 0o700
[依赖组件] 无
[启动顺序] 1（CLI 启动）
[健康检查] 日志路径可写
[监控告警] 路径不可写 → 降级 stderr
[来源标注] [TD:MP:M-011]
```

### DP-012 M-012 并发编排器

```
[部署组件] DP-012
[对应模块] M-012
[运行环境] asyncio + Semaphore
[资源配置] CPU ≤ 5% / 内存 ≤ 20MB
[配置管理] 资源探测 psutil（psutil ≥ 5.9）[AR推断]
[依赖组件] DP-001 (启动)
[启动顺序] 1（DP-001 调用）
[健康检查] Semaphore acquire/release 测试
[监控告警] RAM < 8GB → 降级 2 并发
[来源标注] [TD:MP:M-012] [TD:ADR-002/ADR-006] [TD:SR-004]
```

---

## 4. 启动顺序与依赖图

```
DP-001 (CLI 入口)
  ├─ DP-002 (preflight 并行)   ←─ 依赖 deno/node/ffmpeg/whisper
  ├─ DP-010 (错误处理)
  ├─ DP-011 (日志)
  ├─ DP-012 (并发编排)
  └─ DP-004 (sqlite 缓存)
        ↓
   DP-012 调度 (3 并发)
        ↓
   [任务链]
   DP-003 (下载) → DP-005 (转写) → DP-006 (LLM) → DP-007 (笔记)
                                                ↓
                                          DP-009 (截图 并行)
                                                ↓
                                          DP-008 (落盘+管道)
```

**关键依赖**：
- DP-002 必须在 DP-001 启动后立即执行（YouTube 阻塞性）
- DP-003 依赖 DP-002 报告 Deno 状态
- DP-005 依赖 DP-003 提供的 audio_path
- DP-006 依赖 DP-005 提供的 DE-005
- DP-007 依赖 DP-006 + DP-009
- DP-008 依赖 DP-007
- DP-010/011 横切所有节点

---

## 5. 健康检查 / 监控告警

| 监控对象 | 健康检查端点 | 检查方式 | 失败处理 |
|---------|------------|---------|---------|
| CLI 启动 | N/A（一次性） | preflight 4 项 | E_DL_001 / SIM-STUB |
| DB 连接 | N/A | sqlite3.connect() | E_CK_001 降级 |
| LLM API | N/A | HTTPS 200 (Deepseek/Qwen) | fallback 切换 |
| 转写 | N/A | 模型加载 | 降档 base/small |
| 日志 | N/A | 文件可写 | 降级 stderr |
| 内存 | N/A | psutil.virtual_memory() | 降级并发到 2 |

**V1.1 阶段监控说明**：
- 监控仅通过 M-011 JSON Lines 日志（无 Prometheus / OpenTelemetry）[TD:B-007]
- 告警通过错误码 + 退出码实现（无 PagerDuty / 邮件）[TD:SR-008]
- 容量预警通过 M-012 资源探测自适应降级实现 [TD:PC-003]

**V2.0 候选**：
- 引入 OpenTelemetry SDK（轻量）
- 引入 Prometheus pushgateway（短生命周期任务）
- 引入 ELK / Loki 日志聚合 [TD:ER V-003]

---

## 6. 部署清单（交付给 DD-001 + 第⑦棒）

- [ ] 用户文档：环境依赖安装指引（deno/node/ffmpeg/yt-dlp/Whisper 模型）
- [ ] `pyproject.toml`：锁版本下限（TS-001~TS-022 对应）
- [ ] `requirements.txt`：与 pyproject 一致（兼容 pip 直接安装）
- [ ] CI/CD：pip 包发布（V1.1 不做 Docker 镜像，V1.2 候选）
- [ ] 监控：日志文件路径、关键指标（缓存命中率、转写时长、LLM 失败率）
- [ ] 备份：raw/ + cache.db + logs/ 用户自行备份（CLI 无云端同步）
- [ ] 启动脚本：`research` (entry point → DP-001)
- [ ] preflight 文档：4 项环境依赖 + 缺失命令

[TD:SA-D §4 部署清单] [AR 补充 pyproject 锁版本细节]

---

> **本文件结束**。12 组件 100% 部署方案可实现，启动顺序明确，监控告警策略清晰。
