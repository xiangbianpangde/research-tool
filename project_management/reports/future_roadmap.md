# research-tool · 未来 10 个发展方向（按收益排序）

> 排序原则：项目内收益 × 战略价值 × 实施可行性 三因子加权
> 数据基础：技术债评级（TD-01 ~ TD-25）/ 创新点 catalog / Star 增长分析

## Top 10 发展方向

| # | 方向 | 价值 | 实施成本 | 风险 |
|---|---|---|---|---|
| **D1** | 立即修复 4 个 P0 债务（TD-01/02/03/04） | 修复安全/可移植/静默失败/CI 缺失 | S~M | 低 |
| **D2** | PyPI 发布 + CI 全套（I2 改进） | 让 `pip install research-tool` 可用；外部贡献者接入 | M | 中 |
| **D3** | StageRegistry 化（OCP 化管道，TD-05/07/14） | 解决 `_exec()` 双单点；为 CVPR/视频摄取铺路 | L | 中 |
| **D4** | 拆分超大文件（TD-08/09） | 提升可维护性；解决 god module 与 1100+ 行单文件 | L | 中 |
| **D5** | README 首页重做（I1 改进） | 5 秒价值主张 + 视频/GIF demo + Quick Start | S | 低 |
| **D6** | Conference Ingest 实施（CVPR/CVF 链路） | CVPR/CVF 论文 + Poster/Oral/YouTube 进入 raw/ 契约 | XL | 高 |
| **D7** | Expert Library 实施（低星优先） | 解决 star 排序埋没低星高质仓库 | L | 中 |
| **D8** | 补充核心 stage 单元测试（TD-20） | 补 34 个 src 模块专属测试；为后续重构铺安全网 | L | 低 |
| **D9** | 异常分类与可观测性（TD-17 + 60× BLE001） | 失败路径有信号；灰度方案：先关键路径 metric | XL | 高 |
| **D10** | 撤销 presentation→infra 直连（TD-06/10/11） | 收回分层纯洁性；application 演化时 webui/cli 不必同步改 | L | 中 |

## 收益排序

- **短期（≤ 1 季度）**：D1 + D2 + D5
- **中期（1-2 季度）**：D3 + D4 + D8 + D10
- **长期（≥ 2 季度）**：D6 + D7 + D9

## D1 详细动作
- (1) `pyproject.toml:64` 解开 `addopts` 注释 + 加 `pytest-cov`
- (2) `.env` rotate 4 个真实 key（`ANTHROPIC_API_KEY` / `MINIMAX_API_KEY` / `GITHUB_TOKEN` / `TAVILY_API_KEY`）+ 迁移至 `~/.research/.env`
- (3) `cli.py:1073` 默认值改 `Path("./research-output")`
- (4) 17 处 `except ... pass` 加 `logger.debug/warn`

## D2 详细动作
- `pyproject.toml` 补 `[project.urls]`（Homepage / Repository / Docs）
- 加 `.github/workflows/release.yml`（PyPI trusted publisher）
- 加 `.github/workflows/test.yml`（matrix Python 3.11/3.12 + ruff + pytest）
- 加 `.github/dependabot.yml`
- `gh repo edit --add-topic "deep-research,llm-agent,knowledge-graph,research-tool,pdf-ingest,video-transcript"`

## D3 详细动作
- `infrastructure/stages/base.py` 加 `StageMeta` dataclass（name, llm_required, output_dir, output_patterns, optional）
- 每 stage 导出 `STAGE_META`
- `pipeline.py` 引入 `_REGISTRY` 替换 `_exec()` 大 if-elif

## D4 详细动作
- `presentation/cli.py:1264` → `cli/{commands,render,redact}.py`
- `domain/models.py:622` → `domain/{configs,results,video}.py`
- `infrastructure/ingest/transcriber.py:1139` → `transcriber/{engines,runner,cache}.py`

## D5 详细动作
- 第 1 段改为 "From topic to cited report in 3 commands"
- 嵌入 V1.1 视频摄取产物截图
- `pip install research-tool && research ui`
- 补英文 README.md

## D6 / D7 详细
- D6：新增 `infrastructure/search/cvpr_openaccess.py` + `cvpr_virtual.py`；`application/conference_pipeline.py` 编排
- D7：完善 `infrastructure/experts/registry.py` + `repo_ranker.py`（multi-factor）

## D8 详细动作
- 优先级：`infrastructure/stages/{collector,extractor,organizer,reporter}.py` 各 50-100 行
- `infrastructure/llm/{base,openai_client,anthropic_client}.py` 各 30-50 行

## D9 详细动作
- 灰度：先在 `talk_linker.py` / `pipeline.py` 等关键路径用 monkeypatch 加 metric
- 关键路径异常分类 ≥ 5 类（网络/超时/认证/资源/业务）

## D10 详细动作
- 新增 `application/services.py`（`create_research_service`, `run_video_ingest`）
- `webui.py:78` + `cli.py:22,35` 改 import
- `common/translate.py` 移至 `application/translation.py`
- `grep -rn "from ..infrastructure" research_tool/presentation/` 命中数 → 0

## 验证实验

| D | 验证 |
|---|---|
| D1 | 跑全量 `pytest --cov=research_tool`，覆盖率 ≥ 60%；rotate key 后无 secrets 泄漏 |
| D2 | `pip install research-tool` 在新 venv 中成功；PyPI 下载量 ≥ 100/周 |
| D3 | 新增 1 个 no-op stage（注册即可）+ 走完 6 阶段 + backward + talk 全流程 |
| D4 | 每个拆分后 `pytest` 全绿；`wc -l` 每个新文件 ≤ 400 行 |
| D5 | 邀请 5 个外部开发者看首屏 30 秒；30 天 Star 增长率 +30% |
| D6 | 跑 `research run "CVPR 2026 VGGT" --conference cvpr --conference-year 2026 --with-videos` |
| D7 | 召回 10 个低星高质仓库在 top 20；precision@10 提升 ≥ 30% |
| D8 | `pytest --cov` 覆盖率 ≥ 70%；6 阶段每 stage 至少 10 个白盒测试 |
| D9 | 关键路径异常分类 ≥ 5 类；运行日志含分类标签 |
| D10 | `grep` 命中数 → 0；分层纯洁性恢复 |

## 与已有计划的边界

- D6/D7 是 `docs/plan/research-tool后续优化计划-CVPR视频与专家库-20260708.md` 已规划内容，本表仅按收益重排
- D9 是新增（在 audit 中暴露的 60× BLE001 宽异常吞噬）
- D1-D5、D8、D10 都是基于 Phase 3-5 的技术债评级

---

**报告完成时间**：2026-07-21
**关联文件**：`reports/star_growth_roadmap.md`（Star 增长专项）