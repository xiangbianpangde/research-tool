# research-tool · 项目事实表（Project Facts）

> **采集日期**：2026-07-21
> **采集方式**：命令行实证（非猜测）
> **关联**：`project_management/status/current_status.md`

---

## 基本信息

| 维度 | 值 | 证据命令 |
|------|-----|---------|
| 项目路径 | `/Volumes/项目/research-tool` | — |
| 项目名称 | research-tool | `pyproject.toml:6` |
| 版本 | 0.1.1 | `pyproject.toml:7` |
| 许可证 | MIT | `pyproject.toml:11` |
| 远程仓库 | `https://github.com/xiangbianpangde/research-tool` | `git remote -v` |
| 当前分支 | master | `git branch --show-current` |

---

## 代码规模

| 维度 | 值 | 证据命令 |
|------|-----|---------|
| Python 文件总数 | 128 | `find research_tool -name "*.py" \| wc -l` |
| 源码文件数（非测试） | 69 | `find research_tool -name "*.py" -not -path "*/tests/*" \| wc -l` |
| 源码行数（非测试） | 18,906 | `find ... \| xargs wc -l` |
| 测试文件数 | 59 | `find research_tool/tests -name "test_*.py" \| wc -l` |
| 测试代码行数 | 13,918 | `find research_tool/tests -name "*.py" \| xargs wc -l` |
| 项目总文件数（ excl venv/cache/git） | 2,749 | `find . -type f -not -path ...` |
| 项目总目录数 | 233 | `find . -type d -not -path ...` |

---

## 技术栈

| 维度 | 选型 | 证据 |
|------|------|------|
| Language | Python >=3.11（实测运行环境 3.12.7） | `pyproject.toml:10` / `.venv/bin/python --version` |
| Build | hatchling + uv | `pyproject.toml:1-3` / `uv.lock` |
| CLI | Typer >=0.12 + Rich >=13.7 | `pyproject.toml:16-17` |
| LLM SDK | openai >=1.0 + anthropic >=0.40 | `pyproject.toml:25` |
| HTTP | httpx >=0.27 + defusedxml >=0.7 | `pyproject.toml:15,20` |
| 数据模型 | Pydantic >=2.6 | `pyproject.toml:13` |
| Web UI | Gradio >=4.0（optional） | `pyproject.toml:33` |
| Test | pytest >=8.0 + pytest-asyncio >=0.23 | `pyproject.toml:50` |
| Lint/Format | ruff（E/F/N/T20/T100/S/PLR0915） | `pyproject.toml:78-103` |
| Secret scan | gitleaks（pre-commit + GitHub Action） | `.pre-commit-config.yaml` |
| 数据库 | 无（文件系统通信）；SQLite 仅视频转写缓存 | `infrastructure/ingest/cache_manager.py` |

---

## 构建与运行

| 维度 | 方式 | 证据 |
|------|------|------|
| 安装 | `pip install -e .` 或 `uv pip install -e .` | `pyproject.toml` hatchling |
| CLI 入口 | `research = research_tool.presentation.cli:app` | `pyproject.toml:53` |
| Web UI | `research ui` 命令启动 Gradio | `cli.py` |
| 部署向导 | `research setup`（3 档：minimal/recommended/full） | `cli.py` |

---

## 测试

| 维度 | 值 | 证据命令 |
|------|-----|---------|
| 测试收集数 | **840** | `.venv/bin/python -m pytest --collect-only -q` |
| 测试目录 | `research_tool/tests/` | `pyproject.toml:63` |
| asyncio 模式 | auto | `pyproject.toml:62` |
| 覆盖率 | UNKNOWN（pytest-cov 未安装，addopts 被注释） | `pyproject.toml:64` |

---

## Lint 状态

| 维度 | 值 | 证据命令 |
|------|-----|---------|
| ruff check 错误数 | **1** | `.venv/bin/ruff check research_tool/` |
| 唯一错误 | PLR0915 `extractor.py:106`（51 > 50 statements） | ruff 输出 |
| noqa BLE001 数量 | 60 | `grep -rn "BLE001" research_tool/ \| wc -l` |
| noqa PLR0915 数量 | 10 | `grep -rn "PLR0915" research_tool/ \| wc -l` |
| TODO/FIXME/HACK（src） | **0** | `grep -rn "TODO\|FIXME\|HACK" research_tool/ --include="*.py"` |

---

## Git 状态

| 维度 | 值 | 证据命令 |
|------|-----|---------|
| 总 commit 数 | 58 | `git log --oneline \| wc -l` |
| 贡献者 | root, xiangbianpangde | `git log --format="%an" \| sort -u` |
| 首次提交 | 2026-05-20 16:50:06 +0800 | `git log --reverse --format="%ai" \| head -1` |
| 最近提交 | 2026-07-16 15:31:30 +0800 | `git log -1 --format="%ai"` |
| 最近 commit | `f7222ea3 feat: expand research workflows and diagnostics` | `git log --oneline -1` |
| Modified 文件 | 13 | `git status --short` |
| Untracked 文件 | 10（含 project_management/） | `git status --short` |

---

## 功能规模

| 维度 | 值 | 证据 |
|------|-----|------|
| CLI 命令数 | **14** | `grep -c "@app.command" cli.py`（collect/ingest-pdf/ocr-engines/clean/extract/organize/report/run/wiki-stage/publish-wiki/status/ui/config/setup） |
| SearchBackend 具体实现 | **15** | `grep "class.*Backend" search/*.py`（排除抽象基类 SearchBackend + 装饰器 CachingBackend） |
| LLM Provider | 5（DeepSeek/OpenAI/Anthropic/Ollama/MiniMax） | `STATUS.md` / `llm/base.py` |
| 管道阶段 | 6（Collect→Deepen→Clean→Extract→Organize→Report） | `STATUS.md` |
| 错误码 | 26（VideoIngest 体系） | `STATUS.md` / `domain/errors.py` |

---

## 已知问题（实测确认）

| # | 问题 | 证据 |
|---|------|------|
| 1 | STATUS.md 标 454 passed / CLAUDE.md 标 417 / 实测 **840** | pytest --collect-only |
| 2 | STATUS.md 标 11 命令 / 实测 **14** | grep @app.command |
| 3 | STATUS.md 标 12 后端 / 实测 **15** 具体实现 | grep class.*Backend |
| 4 | ruff 仅 1 错（PLR0915 extractor.py:106） | ruff check |
| 5 | 60 处 BLE001 宽异常 noqa | grep |
| 6 | pytest-cov 未启用 | pyproject.toml:64 注释 |
| 7 | CI 仅 gitleaks（无 pytest workflow） | `.github/workflows/` |
| 8 | 13 modified + 10 untracked 未提交 | git status |

---

## UNKNOWN（无法确认）

- pytest 实际通过数（未跑完整 pytest，仅 collect-only）
- 覆盖率百分比（pytest-cov 未安装）
- GitHub Star 数（需网络 API，前次审计记录为 0）
- `.env` 中密钥是否已 rotate（无法读取确认）

---

**采集完成时间**：2026-07-21
