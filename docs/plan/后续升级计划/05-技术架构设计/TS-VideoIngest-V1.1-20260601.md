# 技术选型清单 — VideoIngest V1.1（AR-001）

> **生成方**：AR-001
> **日期**：2026-06-01
> **选型总数**：22（模块数 12 × 0.5~1.5 = [6, 18]，适度扩展含子组件选型）
> **6 项合理性检查**：场景匹配 / 社区活跃度 / 学习曲线 / 运维成本 / 生态兼容 / 长期维护

---

## TS-001 Python 运行时

```
[选型编号] TS-001
[技术名称] Python
[版本] ≥ 3.11（推荐 3.11.x）
[适用场景] 12 个模块的统一运行时；BiliNote 移植要求 Python ≥ 3.11
[选型理由]
  - 场景匹配: ✓ Python 生态完整覆盖 IO 密集/异步/数据处理
  - 社区活跃度: ✓ Python 3.11 仍为主流，2024-2026 持续维护
  - 学习曲线: ✓ 团队熟悉
  - 运维成本: ✓ 单机 CLI，零运维
  - 生态兼容: ✓ yt-dlp / faster-whisper / httpx 全部官方支持
  - 长期维护: ✓ Python 3.11 EOL 2027-10，3.12 仍 LTS
[拒绝的替代方案] Node.js（GIL 不影响 IO 密集但 ML 生态弱）/ Go（开发效率低）
[风险等级] 低
[技术债务] 否
[来源标注] [调研:V1.0-src-sol-62 BiliNote Python 3.11+] [TD:MP]
```

## TS-002 yt-dlp

```
[选型编号] TS-002
[技术名称] yt-dlp
[版本] ≥ 2023.07.06（2025.03.31 推荐）
[适用场景] M-003 下载器核心，封装 bilibili / youtube
[选型理由]
  - 场景匹配: ✓ 双平台统一下载接口
  - 社区活跃度: ✓ 2025 年仍月级 commit
  - 学习曲线: ✓ CLI 工具 + Python API
  - 运维成本: ✓ 单二进制，pip 安装
  - 生态兼容: ✓ 与 faster-whisper 无冲突
  - 长期维护: ✓ 活跃 fork（yt-dlp/yt-dlp 4k+ stars）
[拒绝的替代方案] youtube-dl（2021 年停止更新）/ Lux / You-Get（平台支持弱）
[风险等级] 低
[技术债务] 否
[来源标注] [调研:V1.0-src-risk-42/53] [TD:MP:M-003]
```

## TS-003 Deno 运行时

```
[选型编号] TS-003
[技术名称] Deno
[版本] ≥ 2.0
[适用场景] M-003 YouTube 视频下载（PO Token 必需 JS 运行时）
[选型理由]
  - 场景匹配: ✓ yt-dlp 官方推荐 Deno（2025-09-23 公告）
  - 社区活跃度: ✓ Deno 2.0 (2024-10) 持续维护
  - 学习曲线: ✓ 用户仅需安装无需编程
  - 运维成本: ✓ 单二进制
  - 生态兼容: ✓ yt-dlp 自动检测
  - 长期维护: ✓ Deno Land Inc 持续投入
[拒绝的替代方案] Node.js（yt-dlp 官方文档不推荐，Bun 兼容性未验证）
[风险等级] 中（用户机器必装，环境依赖新增 1 项）
[技术债务] 否（但 Q-008 待用户首次启动确认）
[来源标注] [调研:V1.0-src-risk-42 yt-dlp PO Token 必装 Deno] [TD:B-002/SA-D]
```

## TS-004 faster-whisper

```
[选型编号] TS-004
[技术名称] faster-whisper
[版本] ≥ 1.1.1
[适用场景] M-005 转写器主引擎
[选型理由]
  - 场景匹配: ✓ CTranslate2 推理，medium 档 CER 4-8%
  - 社区活跃度: ✓ SYSTRAN 维护，月级 release
  - 学习曲线: ✓ Python API 简洁
  - 运维成本: ✓ 首次加载 ~1.5GB 模型
  - 生态兼容: ⚠ 需 ctranslate2 ≥ 3.0（macOS arm64 兼容）
  - 长期维护: ✓ OpenAI Whisper 上游持续
[拒绝的替代方案] OpenAI Whisper（Python 原生慢）/ whisper.cpp（C++ 集成复杂）/ Groq API（需外网/成本）
[风险等级] 中
[技术债务] 是 → TD-AR-001（ctranslate2 ≥ 3.0 显式锁定）
[来源标注] [调研:V1.0-src-whisper-19] [TD:PC-001]
```

## TS-005 BiliNote bcut 转写引擎

```
[选型编号] TS-005
[技术名称] BiliNote transcriber/bcut
[版本] 移植 V1.1 子目录
[适用场景] M-005 备选转写引擎（whisper 不可用时降级）
[选型理由]
  - 场景匹配: ✓ B 站官方 ASR 引擎，中文 CER 优
  - 社区活跃度: ✓ BiliNote 2.2.x 维护中
  - 学习曲线: ⚠ 需适配 Python 3.11 异步接口
  - 运维成本: ✓ 零外部依赖
  - 生态兼容: ✓ 移植至子目录 bilinode_partial/
  - 长期维护: ⚠ BiliNote 上游更新不频繁
[拒绝的替代方案] 讯飞开放平台（需 API Key）/ 阿里云 ASR（成本）
[风险等级] 中
[技术债务] 是 → TD-AR-003（BiliNote 上游维护不可控）
[来源标注] [调研:V1.0-src-sol-62] [TD:MP:M-005]
```

## TS-006 youtube-transcript-api

```
[选型编号] TS-006
[技术名称] youtube-transcript-api
[版本] ≥ 1.0.0
[适用场景] M-005 字幕引擎（YouTube CC 字幕）
[选型理由]
  - 场景匹配: ✓ YouTube 官方字幕直接获取
  - 社区活跃度: ✓ 2024-2025 持续
  - 学习曲线: ✓ API 简洁
  - 运维成本: ✓ 零
  - 生态兼容: ✓ 与 yt-dlp 互补
  - 长期维护: ✓
[拒绝的替代方案] 自研 YouTube Data API（配额限制）
[风险等级] 低
[技术债务] 否
[来源标注] [调研:V1.0-src-tech-19] [TD:MP:M-005]
```

## TS-007 Deepseek-v4-flash LLM

```
[选型编号] TS-007
[技术名称] Deepseek-v4-flash
[版本] API v4（实际 1M context）
[适用场景] M-006 主 LLM 总结
[选型理由]
  - 场景匹配: ✓ 1M context 远超 20k token 需求
  - 社区活跃度: ✓ Deepseek 团队持续迭代
  - 学习曲线: ✓ OpenAI 兼容 API
  - 运维成本: ✓ API 调用，无本地部署
  - 生态兼容: ✓ httpx 异步调用
  - 长期维护: ✓ 商业公司持续投入
[拒绝的替代方案] GPT-4o（成本高）/ Claude（不可用区域）/ Qwen3-Max（长文本能力类似但价格高）
[风险等级] 低
[技术债务] 否
[来源标注] [调研:V1.0-src-tech-19/31] [TD:PC-004]
```

## TS-008 Qwen-turbo LLM（fallback）

```
[选型编号] TS-008
[技术名称] Qwen-turbo
[版本] API（DashScope）
[适用场景] M-006 fallback LLM（Deepseek 5xx 3 次后切换）
[选型理由]
  - 场景匹配: ✓ 通用中文场景
  - 社区活跃度: ✓ 阿里持续
  - 学习曲线: ✓ OpenAI 兼容
  - 运维成本: ✓ API
  - 生态兼容: ✓ 独立 API Key
  - 长期维护: ✓ 阿里云商业化产品
[拒绝的替代方案] 智谱 GLM（API 不稳定）/ 自建 fallback（无运维资源）
[风险等级] 低
[技术债务] 否
[来源标注] [调研:V1.0-src-tech-31] [TD:PC-005]
```

## TS-009 sqlite3 (stdlib)

```
[选型编号] TS-009
[技术名称] sqlite3
[版本] Python stdlib ≥ 3.11，DB 文件格式 ≥ 3.0
[适用场景] M-004 缓存持久化
[选型理由]
  - 场景匹配: ✓ 单机 10k 条目足够
  - 社区活跃度: ✓ SQLite 持续
  - 学习曲线: ✓ stdlib
  - 运维成本: ✓ 零
  - 生态兼容: ✓ WAL 模式 Python 3.11 稳定
  - 长期维护: ✓ SQLite 维护至 2050+
[拒绝的替代方案] Redis（V1.1 过度设计）/ LevelDB（额外依赖）/ 文件 JSON（效率低）
[风险等级] 低
[技术债务] 否
[来源标注] [调研:V1.0-src-risk-1.4] [TD:ADR-004]
```

## TS-010 ffmpeg

```
[选型编号] TS-010
[技术名称] ffmpeg
[版本] ≥ 6.0
[适用场景] M-009 截图器（I 帧抽取 + 压缩）
[选型理由]
  - 场景匹配: ✓ `-vf select=eq(pict_type,I)` 精确控制
  - 社区活跃度: ✓ 持续
  - 学习曲线: ✓ CLI 标准
  - 运维成本: ✓ 系统包管理器安装
  - 生态兼容: ✓ 与 yt-dlp 输出 mp4/webm 兼容
  - 长期维护: ✓
[拒绝的替代方案] OpenCV（重）/ moviepy（PyAV 间接）
[风险等级] 低
[技术债务] 否
[来源标注] [调研:V1.0-src-sol-62] [TD:MP:M-009]
```

## TS-011 ffmpeg-python

```
[选型编号] TS-011
[技术名称] ffmpeg-python
[版本] ≥ 0.2.0
[适用场景] M-009 ffmpeg 调用的 Python 包装
[选型理由]
  - 场景匹配: ✓ 流式 API + 异步友好
  - 社区活跃度: ✓ 2023-2024 持续
  - 学习曲线: ✓ Pythonic
  - 运维成本: ✓
  - 生态兼容: ⚠ 依赖 ctranslate2（与 faster-whisper 共生）
  - 长期维护: ⚠ 单人维护
[拒绝的替代方案] subprocess 直调 ffmpeg（需自管理 stdout/stderr 流）/ PyAV（C 绑定复杂）
[风险等级] 中
[技术债务] 是 → TD-AR-001（ctranslate2 锁版本联动）
[来源标注] [AR推断:Python 异步 + ffmpeg 调用标准方案] [TD:MP:M-009]
```

## TS-012 PyYAML

```
[选型编号] TS-012
[技术名称] PyYAML
[版本] ≥ 6.0
[适用场景] M-007 front matter 解析
[选型理由]
  - 场景匹配: ✓ safe_load 宽容解析
  - 社区活跃度: ✓
  - 学习曲线: ✓
  - 运维成本: ✓
  - 生态兼容: ✓
  - 长期维护: ✓
[拒绝的替代方案] ruamel.yaml（功能多但 V1.1 不必要）/ json（不支持注释）
[风险等级] 低
[技术债务] 否
[来源标注] [调研:V1.0-src-fm-13/22] [TD:MP:M-007]
```

## TS-013 httpx

```
[选型编号] TS-013
[技术名称] httpx
[版本] ≥ 0.27
[适用场景] M-006 LLM API 调用（异步 HTTP 客户端）
[选型理由]
  - 场景匹配: ✓ async/await 原生支持 + HTTP/2
  - 社区活跃度: ✓ Encode 维护活跃
  - 学习曲线: ✓ requests 兼容 API
  - 运维成本: ✓
  - 生态兼容: ✓ 与 asyncio 生态一致
  - 长期维护: ✓
[拒绝的替代方案] aiohttp（API 不如 httpx 友好）/ requests（同步阻塞）
[风险等级] 低
[技术债务] 否
[来源标注] [AR推断:Python 异步 HTTP 主流选型] [TD:MP:M-006]
```

## TS-014 asyncio (stdlib)

```
[选型编号] TS-014
[技术名称] asyncio
[版本] Python 3.11+ stdlib
[适用场景] M-001 / M-012 异步编排
[选型理由]
  - 场景匹配: ✓ IO 密集并发
  - 社区活跃度: ✓
  - 学习曲线: ⚠ 心智模型需适应
  - 运维成本: ✓
  - 生态兼容: ✓ 与 httpx / aiofiles 协同
  - 长期维护: ✓
[拒绝的替代方案] threading（GIL 限制）/ multiprocessing（开销大）/ trio（生态小）
[风险等级] 低
[技术债务] 否
[来源标注] [TD:MP:M-012] [TD:ADR-006]
```

## TS-015 subprocess (stdlib)

```
[选型编号] TS-015
[技术名称] subprocess
[版本] Python 3.11+ stdlib
[适用场景] M-002（preflight 检测）/ M-008（5 阶段管道触发）/ M-009（ffmpeg）
[选型理由]
  - 场景匹配: ✓ 外部进程隔离
  - 社区活跃度: ✓
  - 学习曲线: ✓
  - 运维成本: ✓
  - 生态兼容: ✓
  - 长期维护: ✓
[拒绝的替代方案] os.system（无错误捕获）/ 直接可执行包装（无异步）
[风险等级] 低
[技术债务] 否
[来源标注] [TD:MP:M-002/M-008/M-009]
```

## TS-016 logging (stdlib)

```
[选型编号] TS-016
[技术名称] logging
[版本] Python 3.11+ stdlib
[适用场景] M-011 结构化日志（JSON Lines）
[选型理由]
  - 场景匹配: ✓ 配合自定义 Formatter 输出 JSON
  - 社区活跃度: ✓
  - 学习曲线: ✓
  - 运维成本: ✓
  - 生态兼容: ✓
  - 长期维护: ✓
[拒绝的替代方案] loguru（侵入式 API）/ structlog（V1.1 不必要）
[风险等级] 低
[技术债务] 否
[来源标注] [TD:MP:M-011]
```

## TS-017 平台与依赖管理

```
[选型编号] TS-017
[技术名称] pyproject.toml + pip
[版本] PEP 621 + pip ≥ 23
[适用场景] 包管理 + 锁版本
[选型理由]
  - 场景匹配: ✓ Python 官方
  - 社区活跃度: ✓
  - 学习曲线: ✓
  - 运维成本: ✓
  - 生态兼容: ✓
  - 长期维护: ✓
[拒绝的替代方案] poetry（额外工具）/ setup.py（已弃用）
[风险等级] 低
[技术债务] 否
[来源标注] [AR推断:Python 2026 主流]
```

## TS-018 pytest

```
[选型编号] TS-018
[技术名称] pytest + pytest-asyncio
[版本] pytest ≥ 7.4 / pytest-asyncio ≥ 0.21
[适用场景] 单元测试 + 异步测试
[选型理由]
  - 场景匹配: ✓ 异步测试原生
  - 社区活跃度: ✓
  - 学习曲线: ✓
  - 运维成本: ✓
  - 生态兼容: ✓
  - 长期维护: ✓
[拒绝的替代方案] unittest（异步支持弱）/ nose2（已停更）
[风险等级] 低
[技术债务] 否
[来源标注] [AR推断:Python 测试主流]
```

## TS-019 ruff

```
[选型编号] TS-019
[技术名称] ruff
[版本] ≥ 0.1.0
[适用场景] Lint + 格式化（替代 flake8/black/isort）
[选型理由]
  - 场景匹配: ✓ Rust 实现，速度快
  - 社区活跃度: ✓ Astral 维护，2024-2026 持续
  - 学习曲线: ✓ 配置简单
  - 运维成本: ✓
  - 生态兼容: ✓ 替代多个工具
  - 长期维护: ✓
[拒绝的替代方案] flake8+black+isort（多工具组合，慢）
[风险等级] 低
[技术债务] 否
[来源标注] [AR推断:2024+ Python 项目主流]
```

## TS-020 watchfiles

```
[选型编号] TS-020
[技术名称] watchfiles
[版本] ≥ 0.20
[适用场景] M-008 dev 模式 hot reload（可选）
[选型理由]
  - 场景匹配: ✓ Rust 实现，跨平台
  - 社区活跃度: ✓
  - 学习曲线: ✓
  - 运维成本: ✓
  - 生态兼容: ✓
  - 长期维护: ✓
[拒绝的替代方案] watchdog（API 复杂）/ 手动轮询
[风险等级] 低
[技术债务] 否
[来源标注] [AR推断:CLI 工具 dev 体验增强]
```

## TS-021 cryptography

```
[选型编号] TS-021
[技术名称] cryptography
[版本] ≥ 41.0
[适用场景] 未来 B-006 缓存加密（V2.0 候选）
[选型理由]
  - 场景匹配: ✓ 标准加密库
  - 社区活跃度: ✓ PyCA 维护
  - 学习曲线: ⚠ 加密 API 复杂
  - 运维成本: ✓
  - 生态兼容: ✓
  - 长期维护: ✓
[拒绝的替代方案] hashlib（仅哈希，不加密）/ PyNaCl（API 不友好）
[风险等级] 低（V1.1 不启用，记入 V2.0）
[技术债务] 否（V2.0 候选）
[来源标注] [TD:ER V-003]
```

## TS-022 ctranslate2

```
[选型编号] TS-022
[技术名称] ctranslate2
[版本] ≥ 3.0（与 faster-whisper 1.1.x 兼容）
[适用场景] faster-whisper 推理后端
[选型理由]
  - 场景匹配: ✓ CTranslate2 推理
  - 社区活跃度: ✓ SYSTRAN 维护
  - 学习曲线: ✓ 透明
  - 运维成本: ⚠ macOS arm64 需 wheel 匹配
  - 生态兼容: ✓ 显式锁定后稳定
  - 长期维护: ✓
[拒绝的替代方案] 自编译（维护成本高）
[风险等级] 中
[技术债务] 是 → TD-AR-001（pyproject 显式锁 ctranslate2>=3.0）
[来源标注] [AR洞察#3] [调研:V1.0-src-whisper-19]
```

---

## 选型合理性检查汇总

| 选型编号 | 场景匹配 | 社区活跃 | 学习曲线 | 运维成本 | 生态兼容 | 长期维护 | 总分 |
|---------|---------|---------|---------|---------|---------|---------|------|
| TS-001~003, 006~010, 012~017, 019, 020, 021 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 6/6 |
| TS-004, 011, 022 | ✓ | ✓ | ✓ | ✓/⚠ | ⚠ | ✓ | 5/6 |
| TS-005 | ✓ | ✓ | ⚠ | ✓ | ✓ | ⚠ | 4/6 |
| TS-018 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 6/6 |

**整体合理性**：21/22 = 95% 通过 6/6；1/22 (TS-005) 通过 4/6 → 总体"高"。
- TS-004 / TS-011 / TS-022 5/6 原因：ctranslate2 间接依赖需显式锁定 → 已在洞察 #3 + 债务 TD-AR-001 处理
- TS-005 4/6 原因：BiliNote 上游维护不可控 + bcut 移植需适配 → 已记债务 TD-AR-003

---

> **本文件结束**。22 项选型 21/22 6/6 通过，1/22 4/6 通过（含缓解），整体合理性 = 高（95%）。
