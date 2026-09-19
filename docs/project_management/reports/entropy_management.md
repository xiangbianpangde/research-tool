# research-tool · 熵管理（Entropy Management）

> 原则：长期项目必须周期性降低熵增（清理过时文档 / 删除死代码 / 修复规范漂移）

## 熵增来源（已观察到）

| 来源 | 当前体积/数量 | 影响 |
|---|---|---|
| 历史计划未归档 | `docs/plan/后续升级计划/` 1.8 MB + 152 文件 + 147 TODO | 拖慢认知 |
| 双份 CLAUDE.md | `AGENTS.md = CLAUDE.md` (6 416 B) | 漂移风险 |
| 未跟踪源码 | 6 个 untracked（含 2 生产代码） | 不可被外部审查 |
| 工作区脏 | 12 modified + 6 untracked | 潜在贡献者困惑 |
| 死错误码 | 4 个常量 | 装饰性代码 |
| lint 失效 | 10×PLR0915 + 60×BLE001 | 规范与代码互相撒谎 |
| 调试残留 | `work/` 19 MB | 本地垃圾 |
| 大文件 | cli.py 1264 / transcriber.py 1139 / downloader.py 996 | 维护性 |
| 跨层偏差 | 4 处有意偏差 | 分层纯洁性 |
| 测试覆盖 | src 69 / tests 59；34 模块无专属测试 | 重构安全网薄 |
| 测试数矛盾 | STATUS 454 vs CLAUDE 417 | 信任度 |
| 文档漂移 | 命令数 11 vs 13；后端数 12 vs 18 | 信任度 |

## Entropy Reduction Point

### Round 7（1 周内）— 紧急熵减

- 解开 `addopts` 注释 + 加 pytest-cov（`pyproject.toml:64`）
- Rotate 4 个真实密钥 + 迁移 `~/.research/.env`
- `cli.py:1073` 默认值改 `./research-output`
- 17 处 `except ... pass` 加 logger
- 删 10 × `noqa: PLR0915`
- 归档 `docs/plan/后续升级计划/` → `docs/archive/`
- 软链 AGENTS.md → CLAUDE.md
- 加 LICENSE/CHANGELOG.md/CONTRIBUTING.md
- **熵减得分**：9 个动作，估 -45 熵单位

### Round 8（2 周）— 架构熵减

- 拆 `cli.py` → `cli/{commands,render,redact}.py`
- 拆 `domain/models.py` → `configs.py/results.py/video.py`
- 新增 `application/services.py`（收回 3 处偏差）
- 18 后端工厂 → 装饰器自注册
- 22 个同名函数 → `common/hashing.py` + `common/text.py`
- Stage 延迟 import 提升到 `__init__`
- **熵减得分**：6 个动作，估 -35 熵单位

### Round 9（3 周）— ingest 熵减

- 拆 `transcriber.py` → `transcriber/{engines,runner,cache}.py`
- 拆 `downloader.py`
- 60 × `noqa: BLE001` 灰度分类
- `tempfile.NamedTemporaryFile` 替换共享 `/tmp`
- 18 search backend 聚合 preflight
- **熵减得分**：5 个动作，估 -30 熵单位

### Round 10-12（持续）— 长期熵减

- 34 个 src 模块专属测试
- def 39.8% docstring 覆盖率提升
- 4 死错误码常量删除
- `deliverable.md` 旧 src/ 路径 → 替换
- SSRF guard 失败 metric + WARN
- `__init__.py:37-38` dotenv 失败 WARN
- **熵减得分**：6 个动作，估 -25 熵单位

## 熵监控（每收束节点对比）

| 指标 | R7 后目标 | R8 后目标 | R9 后目标 | R12 后目标 |
|------|----------|----------|----------|----------|
| 测试覆盖率 | ≥ 60% | ≥ 65% | ≥ 65% | ≥ 70% |
| 单文件最大行数 | ≤ 1000 | ≤ 800 | ≤ 700 | ≤ 500 |
| `except ... pass` 数 | ≤ 10 | ≤ 8 | ≤ 5 | ≤ 3 |
| `noqa: BLE001` 数 | ≤ 50 | ≤ 40 | ≤ 30 | ≤ 20 |
| 工作区未提交文件 | 0 | 0 | 0 | 0 |
| CI workflow 数 | ≥ 3 | ≥ 3 | ≥ 3 | ≥ 3 |
| 文档矛盾点 | ≤ 2 | ≤ 1 | 0 | 0 |
| ADR 数 | ≥ 4 | ≥ 5 | ≥ 6 | ≥ 8 |

## 不应消除的"必要熵"

- **有意偏差 4 处**：保留（标注 `@arch_exception`），是有意识简化
- **SearchBackend 别名映射**：保留（UX 友好设计）
- **filesystem 通信契约**：保留（核心创新）
- **多档部署向导**：保留（对零经验用户必要）

---

**报告完成时间**：2026-07-21