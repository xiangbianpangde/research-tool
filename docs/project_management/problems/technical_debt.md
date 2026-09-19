# research-tool · 技术债务评级

> **审计日期**：2026-07-21 / **关联**：
> - 架构：`project_management/reports/architecture_review.md`
> - 代码质量：`project_management/problems/code_quality.md`
> **来源**：STATUS.md 已知债务 + Phase 3 (code_quality.md) + Phase 4 (architecture_review.md)

---

## 评级总表（25 条债务）

### P0 · 必须解决（影响安全/稳定/核心功能）

| ID | 路径 | 债务描述 | 影响范围 | 修复成本 | 建议回合 |
|----|------|---------|---------|---------|---------|
| **TD-01** | `pyproject.toml:64` + `.github/workflows/` 仅 gitleaks | `addopts` 注释 + 无 pytest CI；覆盖率门禁用 | 全部 PR | M | Round 7 |
| **TD-02** | `.env:4-7` | 真实 API 密钥明文（sk-cp/ghp_/tvly_），gitignore 已排除但未 rotate | 全工作流 | L（rotate + 文档） | Round 7 立即 |
| **TD-03** | `cli.py:1073` | `Path("/Volumes/项目/research-output")` 硬编码 macOS 路径 | 全部非 macOS 用户 + CI | S（1 行） | Round 7 |
| **TD-04** | 17 处 `except ... pass`（CS-06 子集） | 视频摄取 / 临时文件清理 / 探测退化全路径静默失败 | ingest 全部 | M | Round 7-8 |
| **TD-05** | `application/pipeline.py:585-632` | `_exec()` 大 if-elif + `_stage_output` 字典双单点 | 阻碍 Stage 扩展 | M（2-3 天） | Round 8 |
| **TD-06** | `infrastructure/ingest/pipeline_adapter.py:228` | 基础设施上引 application 层，循环依赖隐患 | 阻碍 application 拆分 | M（1-2 天） | Round 8 |
| **TD-07** | `application/pipeline.py:288-401` | `_stream_forward()` 单方法 113 行 | 维护性 / 测试粒度 | M（2 天） | Round 8 |

### P1 · 重要优化（影响扩展性/纯洁性）

| ID | 路径 | 债务描述 | 修复成本 | 建议回合 |
|----|------|---------|---------|---------|
| **TD-08** | `cli.py:1264` | god module；12 个命令 + 编排 | L | Round 8 |
| **TD-09** | `transcriber.py:1139` + `downloader.py:996` | 单文件巨型 | L | Round 9 |
| **TD-10** | `presentation/webui.py:78` | webui→infra 直连，绕过 application 层 | L | Round 8 |
| **TD-11** | `common/translate.py:13` | 公共层上引 infrastructure | L | Round 8 |
| **TD-12** | `infrastructure/search/__init__.py:9-73` | 18 个 if-elif 手写工厂，缺 entry_points/装饰器自注册 | M | Round 8 |
| **TD-13** | `application/pipeline.py:404-551` | `_run_talk_enrichment` 内嵌 clean→extract→organize→report 重跑循环 | M | Round 9 |
| **TD-14** | `infrastructure/stages/collector.py:279,328,329,406` | stage 内多处延迟 import（experts/github/x_backend） | M | Round 8 |
| **TD-15** | 22 个同名私有函数（`_sha256` ×3 / `_chunk` ×2） | 跨模块缓存键不一致风险 | M | Round 8 |
| **TD-16** | 10 × `noqa: PLR0915` vs `max-statements=50` | lint 与代码互相撒谎 | S（删 noqa 或调阈值到 80） | Round 7 |
| **TD-17** | 60 × `noqa: BLE001` | 失败可观测性差 | L | Round 9-10 |
| **TD-18** | `research_tool/infrastructure/ingest/transcriber.py:755-757` | 共享 `/tmp` 竞态 | M | Round 9 |
| **TD-19** | 18 search backend 缺聚合 preflight | CLI 缺失时静默失败 | M | Round 9 |
| **TD-20** | 34 个 src 模块无专属测试（核心 stages 6 件套首当其冲） | 重构安全网缺失 | L | Round 10-12 |

### P2 · 长期优化

| ID | 路径 | 债务描述 | 修复成本 | 建议回合 |
|----|------|---------|---------|---------|
| **TD-21** | `domain/models.py:17-622` | 单文件 623 行混合 4 类契约 | L | Round 10 |
| **TD-22** | `infrastructure/llm/base.py:42-48, 134-137` | 4 个 OpenAI 兼容 provider 走同一 client，endpoint 默认值写死 | S | Round 10 |
| **TD-23** | `application/pipeline.py:147-243` | 反向循环与 talk enrichment 两条路径各自实现 `_invalidate_after_collect`（line 493-504 vs 572-583） | M | Round 11 |
| **TD-24** | `infrastructure/ingest/transcriber.py:1139` | 拆 `transcriber/{engines,runner,cache}.py` | L | Round 11 |
| **TD-25** | def 39.8% docstring 覆盖率 | onboarding 成本 | M（interrogate CI） | Round 12 |

---

## 修复成本图例

- **S** = ≤2 行/单文件
- **M** = ≤1 工作日 / 1-3 文件
- **L** = 1+ 周 / 跨文件重构

---

## 建议回合节奏

| 回合 | 时长 | 解决项 |
|------|------|--------|
| Round 7 | 1 周内 | TD-01/02/03/04/16（5 个 P0 + 1 个 S） |
| Round 8 | 2 周 | TD-05/06/07/08/10/11/12/14/15（架构 + god module 拆分） |
| Round 9 | 3 周 | TD-09/13/17/18/19（video ingest 拆分 + BLE001 治理） |
| Round 10-12 | 持续 | TD-20/21/22/23/24/25（测试 + 文档 + 模型拆分） |

---

## 风险声明

- TD-02（密钥 rotate）属外部依赖协调，需先与 owner 确认 Anthropic / MiniMax / GitHub / Tavily 四个厂商的 rotate 流程。
- TD-09 与 Round 9 的 video ingest 架构债务（Agent 2 负责）可能交叉，建议 Round 9 开始前联合评审。
- TD-17（60 处 BLE001）是大头，强行拆分会引入回归。建议灰度：先用 monkeypatch 在 `talk_linker.py` / `pipeline.py` 等关键路径加指标收集，下个回合再拆。

---

## 已知 STATUS.md 债务（已收录到本表）

| STATUS.md 项 | 对应 TD | 状态 |
|---|---|---|
| pytest-cov 未安装 | TD-01 | 未解决 |
| commitlint 钩子未落地 | （不在 audit 范围） | 未解决 |
| 4 个死错误码常量 | （不在 audit 范围） | 未解决 |
| `deepen.run` 函数过长 | TD-25（已涵盖） | 未解决 |
| webui 长函数 | TD-08（已涵盖） | 未解决 |
| SSRF 封禁表可配置 | （P2） | 未解决 |
| 桥接脚本 `--use-minimax-summary` 冗余 | （已在 R8 处理） | 未解决 |
| vestigial register_error 覆盖保留 | （需独立设计轮） | 未解决 |
| `deliverable.md` 含旧 src/ 路径 | file_audit.md #29 | 未解决 |
| P3-1 受批偏差列表扩展 | （已在 architecture_review §4） | 未解决 |
| P3-2 `ocr_cmd --` 分隔符 | （低风险，未列入） | 未解决 |

---

**报告完成时间**：2026-07-21
**关联文件**：
- `project_management/reports/architecture_review.md`（架构部分）
- `project_management/problems/code_quality.md`（代码质量部分）