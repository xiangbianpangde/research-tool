# 设计决策记录（DDR）— VideoIngest V1.1（DD-001）

> **生成方**：DD-001
> **日期**：2026-06-01
> **决策数**：7（DDR-001~DDR-007）
> **覆盖率**：100%（7 大类设计决策全部记录）

---

## DDR-001 核心设计模式选择：混合架构（协调器 + 策略 + 适配器）

```
[决策编号] DDR-001
[决策标题] 12 模块采用混合架构（协调器+策略+适配器）
[决策状态] 已接受
[决策内容]
  M-001/M-012 采用协调器模式（asyncio.gather + Semaphore）；
  M-005/M-009 采用策略模式（三引擎 + I 帧选择）；
  M-003/M-006/M-008 采用适配器模式（平台/LLM/管道协议适配）。
[决策理由]
  - 协调器：12 模块 DAG 单向依赖，asyncio.gather + Semaphore(3) 满足 3 并发需求
  - 策略：M-005 三引擎（whisper/bcut/groq）需统一接口 + 降级链
  - 适配器：M-003 双平台（YouTube/B站）+ M-006 双模型（Deepseek/Qwen）需协议适配
  - 数据支撑：方案 A 得分 63 vs 备选 B 41，差值 22 ≥ 5
[拒绝的替代方案]
  备选 B（threading + JSON 缓存 + OpenAI Whisper）：GIL 限制 IO 吞吐 + JSON 无事务 + 多线程异常隔离差
  备选 C（事件溯源 + CQRS）：V1.1 过度设计，单用户单机不需要审计追踪
[影响范围] 12 模块全部 + 9 dataclass + 30 API
[相关DDR] DDR-002 / DDR-003
[来源标注] [soul §4.11 多方案对比] [AR:TA §2 分层架构]
```

## DDR-002 数据结构选型：sqlite WAL + dataclass

```
[决策编号] DDR-002
[决策标题] 缓存采用 sqlite WAL + Python dataclass
[决策状态] 已接受
[决策内容]
  缓存层：sqlite3 (stdlib) + WAL 模式 + 0o600 文件权限
  数据传递：9 个 Python @dataclass（DE-001~DE-012），禁止隐式 dict
[决策理由]
  - sqlite WAL：单进程单文件足够，V1.1 数据量 ≤ 10k 条
  - dataclass：类型安全 + IDE 友好 + 序列化简单
  - 比 Redis 简单：V1.1 无分布式需求
  - 比 JSON 文件安全：有事务 + 并发安全
[拒绝的替代方案]
  Redis：V1.1 过度设计，需额外部署
  LevelDB：额外依赖，Python 生态弱
  JSON 文件：无事务，写入竞争
[影响范围] M-004 缓存 + 9 dataclass + 30 API 数据传递
[相关DDR] DDR-003
[来源标注] [AR:TS-009] [AR:ADR-004] [调研:S-102]
```

## DDR-003 接口契约格式：进程内函数调用 + dataclass

```
[决策编号] DDR-003
[决策标题] 接口契约采用进程内 Python 函数调用 + dataclass
[决策状态] 已接受
[决策内容]
  - 73% 接口（22/30）采用进程内同步 Python 函数调用
  - 10% 接口（3/30）采用进程内异步（asyncio）
  - 13% 接口（4/30）采用 subprocess（yt-dlp/管道/ffmpeg）
  - 7% 接口（2/30）采用 httpx HTTPS（LLM）
  - 数据通过 9 个 dataclass 传递，禁用隐式 dict
[决策理由]
  - 进程内函数调用：单进程单用户 CLI，无网络开销
  - 异步：M-001/M-012 并发编排 + M-005/M-006 IO 密集
  - subprocess：外部工具必须用 subprocess（yt-dlp/ffmpeg/管道）
  - dataclass：避免隐式 dict，类型安全
[拒绝的替代方案]
  HTTP/JSON：单机 CLI 无服务端，HTTP 开销浪费
  gRPC：单进程无 RPC 需求
  Protocol Buffers：序列化开销大
[影响范围] 30 API 全部
[相关DDR] DDR-001 / DDR-002
[来源标注] [AR:API §统计] [AR:TD:DF] [AR:TD:MP]
```

## DDR-004 文件组织：单层 research_tool 包 + tests/ 子目录

```
[决策编号] DDR-004
[决策标题] 文件结构采用单层 research_tool 包 + tests/ 子目录
[决策状态] 已接受
[决策内容]
  research_tool/
    cli.py / preflight.py / downloader.py / ... (12 个模块文件)
    datatypes.py (9 dataclass)
    bilinode_partial/ (BiliNote 移植子目录)
    tests/ (12 个 test_*.py + fixtures/)
  入口: research (CLI 脚本)
[决策理由]
  - 单层包：12 模块规模适中，避免过度嵌套
  - tests/ 子目录：pytest 推荐结构
  - bilinode_partial/ 独立：TS-005 BiliNote 移植隔离
  - 入口脚本 research：与 pyproject.toml [project.scripts] 对应
[拒绝的替代方案]
  src-layout（src/research_tool/）：V1.1 简单包无需
  多层（research_tool/core/, research_tool/utils/）：12 模块不需分层
[影响范围] 项目根结构 + 12 模块文件 + tests 目录
[相关DDR] DDR-005
[来源标注] [AR:DP §3 12 部署组件] [Python 3.11+ 包管理最佳实践]
```

## DDR-005 异常处理策略：分边界 + 错误码字典 + 3 段式

```
[决策编号] DDR-005
[决策标题] 异常处理按 7 边界划分 + 21 错误码字典 + 3 段式输出
[决策状态] 已接受
[决策内容]
  - 7 边界（SEC-001~SEC-007）对应 7 异常处理策略（EX-001~EX-007）
  - 21 错误码字典（E_DL_001 / E_CK_001 / E_LLM_001 等）
  - 3 段式错误信息（场景/原因/建议）
  - 退出码仲裁（403>401>500>0）
[决策理由]
  - 边界划分：与 SEC 一一对应，便于追溯
  - 错误码字典：用户可读 + 机器可处理
  - 3 段式：用户友好（场景/原因/建议清晰）
  - 退出码仲裁：3 并发混合结果时取最严重
[拒绝的替代方案]
  通用异常（BaseException 子类）：用户不友好
  国际化错误码（i18n）：V1.1 不必要
[影响范围] M-010 错误处理 + 12 模块异常捕获 + 7 边界 EX
[相关DDR] DDR-006
[来源标注] [AR:SEC §汇总] [AR:BR-013/BR-014] [AR:SR-008] [CE-010]
```

## DDR-006 代码风格：ruff + mypy + pytest 三件套

```
[决策编号] DDR-006
[决策标题] 代码风格采用 ruff (lint+format) + mypy (类型) + pytest (测试) 三件套
[决策状态] 已接受
[决策内容]
  - ruff ≥ 0.1.0：Lint + 格式化（替代 flake8+black+isort）
  - mypy ≥ 1.0：strict 类型检查
  - pytest ≥ 7.4 + pytest-asyncio ≥ 0.21：测试 + 异步测试
  - 配置：ruff.toml + mypy.ini + pytest.ini
  - 覆盖率：行 ≥ 80% / 分支 ≥ 70%（核心模块 ≥ 90%）
[决策理由]
  - ruff：Rust 实现，比 flake8+black+isort 快 10-100 倍
  - mypy strict：避免类型错误，单 dataclass 大量使用
  - pytest-asyncio：异步测试必备
  - 覆盖率：80% 是 Python 项目通用标准
[拒绝的替代方案]
  flake8 + black + isort：3 工具组合，速度慢
  pylint：慢，配置复杂
  unittest：异步支持弱
[影响范围] 12 模块 + tests/ + pyproject.toml
[相关DDR] DDR-004
[来源标注] [AR:TS-019 ruff] [AR:TS-018 pytest] [Python 最佳实践 2024+]
```

## DDR-007 设计可追溯性：100% 来源标注

```
[决策编号] DDR-007
[决策标题] 所有非 AR 原文内容标注 [DD推断:依据]
[决策状态] 已接受
[决策内容]
  - AR 原文直接引用：[AR:API-NNN/TS-NNN/SEC-NNN/DP-NNN]
  - DD 推断内容：[DD推断:依据描述]
  - 待澄清内容：[待澄清:问题描述] → CR-NNN
  - 100% 标注率（D8 达成率 = 100%）
[决策理由]
  - 便于后续追溯和复盘
  - DD 不发明技术方案（soul R1）
  - 推断与原文明确分离
  - 满足 D8 设计可追溯性 100%
[拒绝的替代方案]
  无标注：违反 soul R6
  仅标注推断：与 AR 原文混淆
[影响范围] 全部 DD 产出
[相关DDR] -
[来源标注] [soul §2.2 D8] [soul R6] [soul §4.5 推断与标注规则]
```

---

## DDR 汇总

| 决策 | 内容 | 影响 | 状态 |
|------|------|------|------|
| DDR-001 | 混合架构（协调器+策略+适配器） | 12 模块 | 已接受 |
| DDR-002 | sqlite WAL + dataclass | M-004 + 9 DE | 已接受 |
| DDR-003 | 进程内函数调用 + dataclass | 30 API | 已接受 |
| DDR-004 | 单层 research_tool 包 | 项目根 | 已接受 |
| DDR-005 | 7 边界 + 21 错误码 | M-010 + 7 EX | 已接受 |
| DDR-006 | ruff + mypy + pytest | 12 模块 | 已接受 |
| DDR-007 | 100% 来源标注 | 全部产出 | 已接受 |

**覆盖率：100%（7/7 大类设计决策全部记录）**

---

> **本文件结束**。7 项设计决策已记录并接受，可供后续复盘和追溯。
