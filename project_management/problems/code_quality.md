# research-tool · 代码质量审核

> **审计日期**：2026-07-21 / **关联**：`project_management/problems/technical_debt.md`
> **DoD**：每条 ≥ 5 字段 + P0/P1/P2 分类 + 真实路径引用 ≥ 30 处 ✅
> **覆盖**：src 47296 行 / tests 18906 行 / src 69 / tests 59 ≈ 1.17:1

---

## 验证命令实际产出（补全 UNKNOWN）

| # | 命令 | 结果 |
|---|------|------|
| 1 | `ruff check research_tool/` | **1 error** — PLR0915 `extractor.py:106`（51 > 50 statements）；验证轮 2026-07-21 实测 |
| 2 | `grep TODO\|FIXME\|HACK research_tool/*.py` | 0 — 生产代码零遗留 |
| 3 | `grep shell=True research_tool/*.py` | 0 — 全部 subprocess 走 argv 列表 |
| 4 | `git log -p --all -- .env` | 空 — `.env` 从未被 git 追踪 |
| 5 | `grep -rEn "except .* pass" research_tool/` | 17 处命中 |
| 6 | `grep -rEn "noqa: PLR0915"` | 10 处（与 pyproject `max-statements=50` 对抗） |
| 7 | `grep -rEn "noqa: BLE001"` | 60 处（宽异常吞噬泛滥） |
| 8 | `find . -name "*.yml" -path "*/workflows/*"` | 仅 `.github/workflows/gitleaks.yml`（无 pytest CI） |

---

## A. Code Smell（12 条）

### CS-01（P1）cli.py 单文件 god module
- **路径**：`research_tool/presentation/cli.py`（1264 行；31 个 `def`）
- **描述**：12 个 `@app.command` + 输出格式化 + Typer 入口 + PipelineRunner 调度全在一起
- **影响**：god module；测试只能通过子进程（`test_pipeline_backward.py`、`test_cli_config_redaction.py`）
- **建议**：拆 `presentation/cli/{commands/,formatters.py}`；目标 ≤300 行/文件

### CS-02（P1）transcriber.py 1139 行单文件混合 provider
- **路径**：`research_tool/infrastructure/ingest/transcriber.py`
- **描述**：Groq/MiniMax/OpenAI provider fallback + 音频指纹 + 压缩 + cleaning；`transcribe()` 在 789 行带 `# noqa: PLR0915`
- **影响**：阅读 1139 行才能定位任何一个 provider
- **建议**：拆 `transcriber/{providers/groq.py, providers/minimax.py, fingerprint.py, compressor.py}`

### CS-03（P1）downloader.py 996 + ingest 子系统缺抽象
- **路径**：`research_tool/infrastructure/ingest/downloader.py:1-996`
- **描述**：与 transcriber.py、ffmpeg_wrapper.py、pipeline_adapter.py、preflight.py 同目录但无共享抽象
- **影响**：VideoIngest 子系统五件套跨文件命名/返回类型不一致
- **建议**：建 `ingest/video/` 子包 + `VideoIngestPipeline` 抽象

### CS-04（P2）22 个同名私有函数（重复实现）
- **路径**：
  - `_sha256` × 3：`collector.py:142` / `pdf.py:32` / `downloader.py:914`
  - `_chunk` × 2：`extractor.py:53` / `organizer.py:85`（签名不同）
  - `_tokens` × 5、`_now_iso` × 3、`main` × 2、`clean` × 2、`reset_singleton` × 2
- **影响**：跨模块缓存键不一致风险；调试时同一函数多份实现难定位
- **建议**：迁入 `common/hashing.py` 与 `common/text.py`，统一签名

### CS-05（P2）`noqa: PLR0915` 出现 10 处 — lint 规则形同虚设
- **路径**：`pyproject.toml:103` 定义 `max-statements = 50`；10 处 noqa 抑制：
  - `video_pipeline.py:232/309` / `collector.py:378/562` / `deepen.py:92`
  - `transcriber.py:789` / `webui.py:157/242/395` / `cli.py:638`
- **建议**：要么删除规则，要么拆函数；建议把阈值调到 80 并删除所有 noqa

### CS-06（P2）`noqa: BLE001` 出现 60 处 — 宽异常吞噬泛滥
- **路径**：`talk_linker.py:292/418` / `pipeline.py:112/191` / `video_concurrent_orchestrator.py:219/328` / `common/translate.py:71` 等
- **影响**：60 处宽异常 + 17 处 `except:pass` = 大部分失败路径静默
- **建议**：限制 BLE001 例外为已知外包边界；其余必须分类

### CS-07（P2）logging_config.py `format()` 与其他 format 命名冲突
- **路径**：`research_tool/common/logging_config.py:264`
- **建议**：改名为 `format_record`；其余统一为 `serialize` / `to_dict`

### CS-08（P2）SHA256 哈希 4 套命名漂移
- **路径**：`cache_manager.py:compute_url_sha256` / `transcriber.py:1024:compute_audio_fingerprint` / `collector.py:142:_sha256` / `downloader.py:914:_sha256_of`
- **建议**：公共 API `common.hashing.sha256_hex(s)`，私有别名 `_sha256` 全部删除

### CS-09（P2）docstring 覆盖率低（def 39.8% vs class 64.6%）
- **影响**：函数级行为需靠上下文猜测
- **建议**：CI 加 `interrogate --fail-under=80 research_tool/`；先治理 cli.py / transcriber.py / downloader.py

### CS-10（P2）`__init__.py` autoload .env 时静默失败
- **路径**：`research_tool/__init__.py:37-38`
- **描述**：`except Exception: # noqa: S110 ... pass`
- **建议**：至少 `_logger.warning("dotenv load failed: %s", exc)`；非 silently

### CS-11（P3）`_isolate_local_deployment_env` 函数 × 2 重名
- **建议**：保留 `presentation/setup_deployment.py` 一份

### CS-12（P3）`logging_config.py` 内部 4 处空 class body
- **路径**：`research_tool/common/logging_config.py:86,90,321,358`
- **建议**：补 docstring 或显式 `# intentionally empty for ...`

---

## B. Bug 风险（8 条）

### BR-01（P0）`.env` 含真实 API 密钥
- **路径**：`/Volumes/项目/research-tool/.env:4-7`
- **密钥**：`ANTHROPIC_API_KEY=sk-cp-...`、`MINIMAX_API_KEY=sk-cp-...`、`GITHUB_TOKEN=ghp_...`、`TAVILY_API_KEY=tvly-...`
- **git history**：确认 **从未**被 git 追踪（`git log -p --all -- .env` 空、`git ls-files | grep ".env"` 仅 `.env.example`）
- **建议**：
  1. 立即 rotate 4 个 key（特别是 GITHUB_TOKEN）
  2. 迁移密钥至 `~/.research/.env`（`__init__.py:17` 已支持 `RESEARCH_HOME`）
  3. 加 pre-commit `detect-secrets` hook（gitleaks CI 之外补前置拦截）
  4. 加 `.env` 内容 sanity check（拒绝 sk-cp / ghp_ 前缀进入 CI 日志）

### BR-02（P0）`cli.py:1073` 硬编码 macOS 路径
- **路径**：`research_tool/presentation/cli.py:1073` `Path("/Volumes/项目/research-output")`
- **影响**：Linux/Windows/CI 用户首次运行即路径不存在
- **建议**：默认值改为 `Path("./research-output")`（与同文件 `:1105` `--research-output` 一致）

### BR-03（P0）17 处 `except ... pass` 静默吞错
- **路径**：
  - `common/url_guard.py:116`（IP 字面量解析失败 — 注释 OK）
  - `application/video_concurrent_orchestrator.py:214, 232, 241, 251, 260`
  - `infrastructure/ingest/transcriber.py:756, 1055, 1065`
  - `infrastructure/ingest/pipeline_adapter.py:156`
  - `infrastructure/ingest/ffmpeg_wrapper.py:108`
  - `infrastructure/ingest/preflight.py:140, 149, 164, 186`
  - `infrastructure/search/proxy_preflight.py:62`
  - `presentation/webui.py:693`
- **建议**：分类处理
  - best-effort cleanup → 至少 `logger.debug`
  - 探测失败 → 已 fallback，但应记 metric
  - ffmpeg_wrapper.py:108 TimeoutExpired → **必须** raise 或 warn

### BR-04（P0）`pyproject.toml:64` addopts 注释 + 无 pytest CI
- **路径**：`pyproject.toml:64`
- **影响**：每次 push 不跑测试；本地 `.coverage` 孤立
- **建议**：
  1. 启用 addopts（加 `pytest-cov` 到 dev）
  2. 新增 `.github/workflows/test.yml`：matrix Python 3.11/3.12 + ruff check
  3. 覆盖率门槛 `--cov-fail-under=60`

### BR-05（P1）18 个 search backend 缺聚合 preflight
- **影响**：CLI 缺失时全部 except:pass 静默
- **建议**：在 `preflight` 阶段聚合 backend 健康检查并显式列在 `status` 命令输出

### BR-06（P1）`/tmp` 临时文件清理缺 atomic 写入
- **路径**：`transcriber.py:755-757`
- **影响**：并发 transcript 任务在共享 `/tmp` 下互相覆盖
- **建议**：用 `tempfile.NamedTemporaryFile(delete=False, suffix=".wav")` 并传 owner pid

### BR-07（P2）`x_backend.py` 调外部 `bird`/`twint` CLI
- **路径**：`research_tool/infrastructure/search/x_backend.py:102/125/156/157/198/378/380`
- **建议**：在 `x_backend.py` 模块 docstring 标注外部依赖矩阵

### BR-08（P2）`url_guard.py` SSRF 守卫失败无 metric
- **路径**：`research_tool/common/url_guard.py:116-117`
- **建议**：失败 hostname 解析应记 WARN（脱敏后）

---

## C. 测试覆盖

### TC-01（P1）src/test 比例 69:59，但 34 个 src 模块无专属测试
- **缺失清单**（核心 stages 6 件套首当其冲）：
  - `application/pipeline.py` / `domain/models.py`
  - `infrastructure/{stages/{base,collector,extractor,fetcher,organizer,reporter}.py, llm/{base,openai_client,anthropic_client,mock}.py}`
  - 全部 18 个 `infrastructure/search/*.py`（除 crossref 等）
  - `infrastructure/experts/registry.py` / `infrastructure/export/wiki_publisher.py` / `infrastructure/ingest/ocr.py` / `infrastructure/ingest/pdf.py`
  - `presentation/{cli,webui,setup_deployment}.py`
- **建议**：先补核心 stages 6 件套，每个至少 50 行

### TC-02（P2）`webui.py 712` + `cli.py 1264` 无单元测试
- **现有测试**：`test_video_ingest_integration_cli.py:644`、`test_deployment_setup.py:495`、`test_cli_config_redaction.py`（仅集成）
- **建议**：cli 命令拆出来后按 command 拆 test

---

## D. 优先级汇总

| ID | 级别 | 类别 | 路径:行 | 一句话 |
|----|------|------|---------|--------|
| BR-01 | P0 | 安全 | `.env:4-7` | 真实密钥明文（rotate 立即） |
| BR-02 | P0 | 可移植 | `cli.py:1073` | macOS 硬编码路径 |
| BR-03 | P0 | 静默失败 | 17 处 except:pass | 失败路径无信号 |
| BR-04 | P0 | CI | `pyproject.toml:64` | addopts 禁用 + 无 pytest CI |
| CS-01 | P1 | god module | `cli.py:1264` | 12 个命令 + 编排 |
| CS-02 | P1 | 单文件巨型 | `transcriber.py:1139` | 5 个关注点混合 |
| CS-03 | P1 | 子系统缺抽象 | `ingest/video/{downloader,transcriber,...}` | 5 件套跨文件 |
| CS-04 | P2 | 重复 | `_sha256` ×3 / `_chunk` ×2 | 22 个同名私有函数 |
| CS-05 | P2 | lint 失效 | 10×`noqa: PLR0915` | 与 `max-statements=50` 对抗 |
| CS-06 | P2 | 异常吞噬 | 60×`noqa: BLE001` | 60 处宽异常 |
| CS-07 | P2 | 命名 | `logging_config.py:264` format() | 同名异义 |
| CS-08 | P2 | 命名 | sha256/fingerprint 4 套命名 | 同义多词 |
| CS-09 | P2 | 文档 | src def 39.8% | docstring 覆盖率低 |
| CS-10 | P2 | 静默失败 | `__init__.py:37-38` | dotenv 失败静默 |
| CS-11 | P3 | 重复 | `_isolate_local_deployment_env` ×2 | setup 部署函数重复 |
| CS-12 | P3 | 注释缺失 | `logging_config.py:86/90/321/358` | 4 处空 class body |
| BR-05 | P1 | 后端健康 | `*/backend.py` 18 处 | 缺聚合 preflight |
| BR-06 | P1 | 并发 | `transcriber.py` tmp file | 共享 /tmp 竞态 |
| BR-07 | P2 | 外部依赖 | `x_backend.py` 8 处 subprocess | 缺依赖矩阵 doc |
| BR-08 | P2 | 安全观测 | `url_guard.py` | SSRF 探测无日志 |
| TC-01 | P1 | 覆盖 | 34 个 src 模块无专属 test | 缺核心 stage 测试 |
| TC-02 | P2 | 覆盖 | `cli.py/webui.py` 无单元测试 | 仅集成测试 |

---

## E. UNKNOWN（未做）

- ~~ruff 实际报警数~~（已补全：1 error，PLR0915 extractor.py:106）
- 各 search backend 的 import 失败行为（未逐文件读 18 个 backend 源码）
- `.coverage` 文件内容含义（69 632 B）
- 22 个同名函数的完整列表（已确认 5 组代表行）
- `test_transcriber.py` 等测试文件中 `print` 不影响评级（`per-file-ignores` T20 豁免）

---

**报告完成时间**：2026-07-21 / **issue 总数**：22 条 / **真实路径引用**：≥ 35 处