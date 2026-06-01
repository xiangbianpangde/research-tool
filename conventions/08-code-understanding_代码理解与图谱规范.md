# 代码理解与图谱规范

> **本规范是 [ai-workflow 第二步 §2.9 工具链增强](ai-workflow_AI协作开发流程/04-第二步_迭代开发.md) 的细化**，定义「代码理解需要什么图谱、谁来用、怎么建」。
> 它同时收拢了原先散落在 01/02/05/06 的调用图相关内容（调用关系标注、影响分析、代码地图、依赖黑洞检测），是这些主题的**唯一权威来源**。

---

## 核心洞察：不是一套图谱，是两套

代码理解有两个截然不同的消费者，它们需要的东西完全不同：

| 维度 | AI | 人 |
|------|-----------|-----|
| **怎么读** | 结构化查询（"谁调用了这个函数？"） | 可视化浏览（"这个项目长什么样？"） |
| **粒度** | 函数/类/变量级 | 模块/架构级 |
| **更新频率** | 实时（保存即同步） | 按需（PR/发布/onboarding） |
| **交互方式** | MCP 工具 7 个 API | 交互式 Dashboard |
| **核心指标** | token 省多少、tool call 少多少 | 新人多久理解架构、重构有没有漏影响 |

**结论**：给 AI 的和给人的是两套独立系统，各有各的工具、各有各的维护策略。本规范管这两套。

---

## 启用门槛（分线）

| 条件 | 给 AI — CodeGraph | 给人 — Understand-Anything |
|------|-------------------|--------------------------|
| **必须引入** | 中大型项目：> 10 模块 或 > 5 万行 | **只要有代码** |
| **建议引入** | 任何用 AI 编码助手的项目（收益随规模递增） | — |
| **不必引入** | 轻量项目（单文件脚本、< 1 万行） | 纯文档项目（无代码可分析） |

---

## 一、AI 图谱 — CodeGraph

### 1.1 定位

给 AI 编码助手（Claude Code / Codex / Cursor / Copilot / Gemini CLI 等）用的**预索引代码知识图谱**。替代 grep/glob/read 循环——AI 不再扫描文件，而是直接查询索引。

> 项目地址：[colbymchenry/codegraph](https://github.com/colbymchenry/codegraph) · 29.8k stars · MIT

### 1.2 原理

```
源码 ──tree-sitter 解析──▶ AST ──语言特定查询──▶ 节点（函数/类/方法）
                                    │              边（调用/导入/继承/实现）
                                    │
                                    ▼
                           SQLite + FTS5 全文搜索
                                    │
                                    ▼
                           MCP Server（7 个工具）
                                    │
                                    ▼
                           AI 助手直接查询
```

- **提取**：tree-sitter 解析成 AST，语言特定查询抽取节点和边
- **存储**：SQLite 单文件数据库（`.codegraph/codegraph.db`），FTS5 全文搜索
- **解析**：引用消解——函数调用→定义、导入→源文件、类继承
- **同步**：原生 OS 文件监视器（FSEvents/inotify/ReadDirectoryChanges），2 秒 debounce，增量同步

### 1.3 MCP 工具速查

| 工具 | 用途 | 何时用 |
|------|------|--------|
| `codegraph_context` | 组合搜索+节点+调用者+被调用者，一次调用构建上下文 | 理解一个功能/区域时的第一步 |
| `codegraph_trace` | "X 怎么到达 Y"——完整调用路径，每跳含源码 | 跟踪执行流程 |
| `codegraph_explore` | 批量查看多个相关符号的源码 | 调查一组关联符号 |
| `codegraph_search` | 按名称查找符号 | 已知函数/类名，找定义 |
| `codegraph_callers` | 谁调用了这个符号 | 向上追溯 |
| `codegraph_callees` | 这个符号调用了谁 | 向下追溯 |
| `codegraph_impact` | 修改前检查影响范围 | 重构前的安全网 |

### 1.4 实战数据

在 7 个真实开源项目上的 benchmark（Claude Code 回答同一个架构问题，有 CodeGraph vs 无 CodeGraph）：

| 代码库 | 语言 | 规模 | Token 节省 | Tool Call 减少 |
|--------|------|------|-----------|---------------|
| VS Code | TypeScript | ~10k 文件 | **78%** | **85%** |
| Excalidraw | TypeScript | ~640 文件 | **90%** | **96%** |
| Django | Python | ~3k 文件 | **36%** | **53%** |
| Tokio | Rust | ~790 文件 | **86%** | **92%** |
| OkHttp | Java | ~645 文件 | **13%** | **45%** |
| Gin | Go | ~110 文件 | **34%** | **40%** |
| Alamofire | Swift | ~110 文件 | **64%** | **83%** |

**平均**：35% 更便宜 · 57% 更少 token · 46% 更快 · 71% 更少 tool call。收益随代码库规模递增。

### 1.5 为什么是 SQLite+FTS5，不是图数据库

CodeGraph 刻意避开了 Neo4j/KuzuDB 路线，选择 SQLite+FTS5。理由：

| 维度 | 图数据库（Neo4j 等） | SQLite+FTS5（CodeGraph 选择） |
|------|---------------------|------------------------------|
| 部署 | 需要独立服务、运维 | 零运维，单文件 `.db` |
| 查询语言 | Cypher（需学习） | SQL + 全文搜索（通用） |
| 覆盖度 | 跨语言平均 62%、异步/异常 < 40% | tree-sitter 语法级精确，覆盖度取决于语言支持 |
| 增量更新 | KG4Code：大项目 12 分钟 | 文件监视器，2 秒 debounce |
| AI 交互 | AI 输出 Cypher → 图库执行 → 返回结果 | AI 通过 MCP 工具查询，不直接写 SQL |

**核心设计**：CodeGraph 不要求 AI 学 Cypher——它把图遍历封装在 MCP 工具里。AI 说"找调用者"，CodeGraph 在 SQLite 里做邻接表遍历，把结果返回。对 AI 来说，这就是一个"会回答代码结构问题的本地 API"。

### 1.6 部署

```bash
# 一键安装（macOS / Linux）
curl -fsSL https://raw.githubusercontent.com/colbymchenry/codegraph/main/install.sh | sh

# Windows (PowerShell)
irm https://raw.githubusercontent.com/colbymchenry/codegraph/main/install.ps1 | iex

# 或通过 npm
npx @colbymchenry/codegraph

# 项目初始化
cd your-project
codegraph init -i
```

安装器自动检测已安装的 AI 助手（Claude Code / Cursor / Codex / Copilot / Gemini CLI 等），写入 MCP 配置。重启助手后，AI 会自动使用 CodeGraph 工具。

---

## 二、人的图谱 — Understand-Anything

### 2.1 定位

给人看的**交互式代码知识图谱**。不是"画一张炫酷的图让你感叹代码好复杂"，而是"安静地教你每个模块怎么拼在一起"。

> 项目地址：[Lum1104/Understand-Anything](https://github.com/Lum1104/Understand-Anything) · 39.8k stars · MIT · "Graphs that teach > graphs that impress"

### 2.2 原理

```
源码 ──tree-sitter（确定性）──▶ 结构事实（导入/导出/函数/类/调用/继承）
  │                                 │
  └── LLM（语义）────────▶ 语义理解（摘要/标签/架构分层/业务域/导览）
                                    │
                                    ▼
                           knowledge-graph.json（可提交 Git）
                                    │
                                    ▼
                           交互式 Web Dashboard
```

**混合策略**：tree-sitter 负责可重复的结构提取（同输入同输出），LLM 负责解析器做不了的——英文摘要、架构分层、业务域映射、引导式导览。结构侧可复现，语义侧有意图。

**多 Agent 流水线**：

| Agent | 职责 |
|-------|------|
| project-scanner | 发现文件、检测语言和框架 |
| file-analyzer | 提取函数/类/导入，生成图谱节点和边 |
| architecture-analyzer | 识别架构分层 |
| tour-builder | 生成引导式导览 |
| graph-reviewer | 验证图谱完整性和引用一致性 |
| domain-analyzer | 提取业务域、流程、步骤（`/understand-domain` 触发） |

文件分析器并行运行（最多 5 并发，每批 20-30 文件）。支持增量更新——仅重新分析变更文件。

### 2.3 功能速查

| 功能 | 命令 | 说明 |
|------|------|------|
| 结构图 | `/understand` | 交互式知识图谱——每个文件/函数/类是节点，可点击、搜索、探索 |
| 业务域视图 | `/understand-domain` | 业务域→流程→步骤的水平图 |
| 引导式导览 | 自动生成 | 按依赖排序的架构走读 |
| 语义搜索 | Dashboard 内 | "哪些部分处理认证？" → 跨图返回相关结果 |
| 差异影响分析 | `/understand-diff` | 提交前看清变更的涟漪效应 |
| 架构分层着色 | 自动 | API / Service / Data / UI / Utility 按层分组+颜色标注 |
| 语言概念讲解 | 自动 | 12 种编程模式（泛型/闭包/装饰器等）在上下文中解释 |
| 交互式问答 | `/understand-chat` | "支付流程怎么走？" |
| 新人指南 | `/understand-onboard` | 自动生成 onboarding 文档 |

### 2.4 团队共享

图谱是 JSON（`.understand-anything/knowledge-graph.json`），可提交 Git：

```bash
# 提交图谱（让队友跳过分析流水线）
git add .understand-anything/
# 排除中间产物
echo ".understand-anything/intermediate/" >> .gitignore
echo ".understand-anything/diff-overlay.json" >> .gitignore

# 大图（>10 MB）用 git-lfs
git lfs track ".understand-anything/*.json"

# 每次 commit 后自动更新图谱
/understand --auto-update
```

队友 clone 后直接 `/understand-dashboard`，不需要重新跑分析。

> **⚠️ 迁移生成产物时，真正的源头是生成它的脚本**：移动/重命名入库的生成物（如 `CODE_MAP.md`、图谱 HTML）后，若只 `git mv` 而不改生成器，**下次一跑生成器就把文件写回老位置，迁移等于白做**。迁移清单：① 生成器的写出路径 ② 生成器的 docstring/日志 ③ 所有引用（CLAUDE.md / README / dashboard 等）④ `.gitignore` 注释 ⑤ `meta/FILE_GRAPH.md`。**迁移后跑一次生成器验证落点正确。**

### 2.5 为什么是交互式 Dashboard，不是 Mermaid 手写图

| 维度 | 手写 Mermaid + Markdown（原方案） | 交互式 Dashboard（Understand-Anything） |
|------|----------------------------------|----------------------------------------|
| 生成方式 | 人手写或从图数据库导出 | 多 Agent 自动分析 |
| 保真风险 | 代码改动人忘同步 → 漂移 | 增量更新 + `--auto-update` 自动保持同步 |
| 交互性 | 静态图，无法点击/搜索/过滤 | 拖拽缩放、点击节点看源码、语义搜索 |
| 粒度 | 模块级（粗） | 文件→函数→变量（可下钻） |
| 业务视角 | 无 | 域视图：业务流程映射到代码 |
| 维护成本 | 随架构变更同步更新 | 重新跑 `/understand`（增量，只分析变更文件） |
| 团队共享 | 另存图片/嵌入文档 | JSON 入 Git，队友直接打开 Dashboard |

### 2.6 部署

```bash
# Claude Code 插件
/plugin marketplace add Lum1104/Understand-Anything
/plugin install understand-anything

# 其他平台（Codex / Cursor / Copilot / Gemini CLI / 等 12+ 平台）
curl -fsSL https://raw.githubusercontent.com/Lum1104/Understand-Anything/main/install.sh | bash

# 分析项目
/understand

# 打开 Dashboard
/understand-dashboard
```

---

## 三、两种图谱的协同

### 3.1 分工明确，互不替代

```
                      代码库
                        │
        ┌───────────────┴───────────────┐
        ↓                               ↓
   CodeGraph                       Understand-Anything
   （AI 图谱）                      （人的图谱）
        │                               │
   tree-sitter 自动              tree-sitter + LLM 自动
   增量同步（2s）                 增量更新（按需/commit hook）
        │                               │
        ↓                               ↓
   MCP 工具（7个）               Web Dashboard
   AI 直接查询                   人交互式浏览
        │                               │
        ↓                               ↓
   用途：                            用途：
   · AI 回答架构问题              · 新人 onboarding
   · 重构前影响分析              · 架构评审
   · 调用链追踪                  · 业务域理解
   · 避免 grep/read 循环         · PR 差异影响预览
```

**AI 不打开 Dashboard**——它通过 MCP 工具查询 CodeGraph 的索引。**人不用写 Cypher**——打开 Dashboard 拖拽浏览。两套系统服务于两种消费者，各有各的接口。

### 3.2 纯文档项目的退路

纯文档项目（无代码可分析）不需要引入图谱工具。替代方案：用了代码内标注辅助理解：

- 关键模块放 `CALL_GRAPH.md`：列「对外暴露的接口（被谁调用）」+「外部依赖（调用谁）」
- 关键函数用代码标注：

```python
# @entrypoint — 订单创建入口，由 OrderController.create() 触发
def place_order(user_id: str, items: list[Item]) -> Order:
    """下单主流程。
    @callers: OrderController.create(), BatchOrderService.import_orders()
    @calls:   validate_stock(), calculate_price(), payment_service.charge()
    """
```

这些标注让 AI（通过 read_file 读取）和人（看代码时）都能快速理解函数在系统中的位置。**但只要有代码就应该用 Understand-Anything**——标注只是纯文档项目的退路。

---

## 四、检查清单

### CodeGraph（AI 图谱）

- [ ] 项目规模达到启用门槛（> 10 模块 或 > 5 万行）
- [ ] 已运行 `codegraph init -i` 构建初始索引
- [ ] AI 助手已重启，MCP Server 已加载
- [ ] `.codegraph/` 已加入 `.gitignore`（索引不入库，CI 重建）
- [ ] `codegraph status` 确认索引覆盖所有预期模块
- [ ] 文件监视器正常运行（非沙箱环境）
- [ ] AI 回答架构问题时优先使用 CodeGraph 工具而非 grep/read

### Understand-Anything（人的图谱）

- [ ] 项目有代码（非纯文档项目）
- [ ] 已运行 `/understand` 完成初始分析
- [ ] Dashboard 可正常打开，节点和关系完整
- [ ] `.understand-anything/knowledge-graph.json` 已提交 Git（团队共享）
- [ ] `.understand-anything/intermediate/` 和 `diff-overlay.json` 已 `.gitignore`
- [ ] 新人 onboarding 时使用 Dashboard + Guided Tour
- [ ] 重构前使用 `/understand-diff` 预览影响范围

### 协同检查

- [ ] AI 使用 CodeGraph 做影响分析，人使用 Understand-Anything 复核影响范围
- [ ] CodeGraph 的实时索引（文件监视器）与 Understand-Anything 的按需更新（commit hook）互补，不冲突
- [ ] 两个工具的 `.codegraph/` 和 `.understand-anything/` 目录互不干扰

---

## 五、关联

| 方向 | 链接 |
|------|------|
| 细化自 | [ai-workflow §2.9 工具链增强](ai-workflow_AI协作开发流程/04-第二步_迭代开发.md) |
| AI 图谱工具 | [CodeGraph](https://github.com/colbymchenry/codegraph) — MIT · 29.8k stars |
| 人的图谱工具 | [Understand-Anything](https://github.com/Lum1104/Understand-Anything) — MIT · 39.8k stars |
| 调研数据来源 | `docs/research/图谱与代码理解工具调研-output/` |
| 跨层 / 循环依赖红线 | [01-架构设计规范](01-architecture_架构设计规范.md) |
| 代码可理解性 | [02-代码编写规范](02-coding_代码编写规范.md) |
| 影响分析驱动的增量测试 | [05-测试规范](05-testing_测试规范.md) |
| 代码地图 / 知识互联（轻量退路） | [06-文档规范](06-documentation_文档规范.md) |
| 收束节点审计 | [ai-workflow 第三步 §3.3](ai-workflow_AI协作开发流程/06-第三步_收束节点.md) |

---

## 更新记录

| 日期 | 版本 | 变更说明 |
|------|------|---------|
| 2026-05-28 | v3.0 | **范式重构**：从「图数据库+Cypher+Mermaid」单一范式转为「CodeGraph（AI）+ Understand-Anything（人）」双图谱；删除 Cypher 查询示例、存储选型表、工具列表、CODE_MAP 手写要求 |
| 2026-05-27 | v2.0 | 重构为 §2.9 的细化；设启用门槛；删全部报告引用与固化数字；收拢 01/02/05/06 的调用图内容成唯一权威 |
| 2026-05-27 | v1.0 | 初稿，8 章结构 |
