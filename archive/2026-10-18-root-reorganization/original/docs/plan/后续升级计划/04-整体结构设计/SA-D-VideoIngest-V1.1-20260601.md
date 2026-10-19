# 系统架构图-部署视图 — VideoIngest V1.1（TD-001）

> **生成方**：TD-001
> **日期**：2026-06-01
> **核心元素**：服务器/容器/集群/网络拓扑（本期为单机 CLI，不引入容器化）

## 1. 部署拓扑

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
|  |   ├── research (entry point)                                    |   |
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

## 2. 环境依赖矩阵

| 依赖 | 来源 | V1.1 要求 | 检测方式 | 缺失策略 | 阻塞性 |
|------|------|----------|---------|---------|--------|
| Python | 用户预装 | ≥ 3.11 | sys.version | 终止启动 | 是 |
| deno | 用户安装 | ≥ 2.0 | `deno --version` (BP-002) | 打印安装命令 + E_DL_001_DENO_MISSING | YouTube 阻塞；B 站不阻塞 |
| node | 用户预装 | ≥ 18.0.0 | `node --version` (BP-002) | 警告 + SIM-STUB 标记 | 否 |
| ffmpeg | 系统包管理器 | ≥ 6.0 | `ffmpeg -version` (BP-002) | 警告 + SIM-STUB；阻塞截图 | 截图阻塞 |
| yt-dlp | pip | ≥ 2023.07.06 | `yt-dlp --version` (BP-003) | 升级提示 + E_DL_002_VERSION_TOO_OLD | 是 |
| faster-whisper | pip (含模型) | medium 档 | 文件存在 (BP-002) | 降档 bcut/groq | 否 |
| faster-whisper-medium 模型 | huggingface | ~1.5GB | 文件存在 (BP-002) | 降档 base/small | 否 |
| sqlite | Python 内置 | ≥ 3.0 | import sqlite3 | 缓存降级不阻塞 (EX-014) | 否 |

[来源标注] [SA:BP-002/BR-005/BR-006/BR-007/BR-017/EX-004~EX-007]

## 3. 网络拓扑

| 流 | 协议 | 端口 | 出/入 | 鉴权 | 频率限制 |
|---|------|------|------|------|---------|
| CLI → Deepseek | HTTPS | 443 | 出 | API Key（环境变量） | 按 LLM 配额 |
| CLI → Qwen | HTTPS | 443 | 出 | API Key | 按 LLM 配额 |
| CLI → bilibili | HTTPS | 443 | 出 | 可选 Cookie（B-002） | 平台策略 |
| CLI → youtube | HTTPS | 443 | 出 | Deno + yt-dlp PO Token | 平台策略 |
| CLI → deno.land | HTTPS | 443 | 出 | 无（首次安装） | 无 |

[TD洞察·部署层] 当前 V1.1 全部依赖在用户主机上，无服务端部署。**演进路径 V2.0** 候选：(a) Whisper 推理迁移到 GPU 服务器（REST）；(b) LLM 代理加 Redis 限流；(c) Cache DB 迁移到云端 Supabase——见 ER-VideoIngest-V1.1-20260601.md V-002/V-003。

## 4. 部署清单（交付给 AR-001 + DevOps）

- [ ] 用户文档：环境依赖安装指引（deno/node/ffmpeg/yt-dlp/Whisper 模型）
- [ ] CI/CD：pip 包发布 + Docker 镜像（可选 V1.2）
- [ ] 监控：日志文件路径、关键指标（缓存命中率、转写时长、LLM 失败率）
- [ ] 备份：raw/ + cache.db + logs/ 用户自行备份（CLI 无云端同步）

[来源标注] [TD推断:基于 V1.1 单机定位 + V2.0 演进预留]

---

> **本文件结束**。单机部署，无服务端组件。下一视图：数据视图 SA-DA。
