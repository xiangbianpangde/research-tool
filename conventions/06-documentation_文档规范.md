# 文档规范

> **本规范是 ai-workflow 中文档产出环节的细化**：
> - 细化 [第二步 §2.5 更新记录](ai-workflow_AI协作开发流程/04-第二步_迭代开发.md)（worklog / STATUS / CLAUDE）与 [§2.8 + 07-汇报](ai-workflow_AI协作开发流程/07-汇报.md)。
> - 细化交付物文档（README / CHANGELOG / 设计文档）；并支撑[第三步·收束节点](ai-workflow_AI协作开发流程/06-第三步_收束节点.md)的「文档一致性检查」。
>
> **模板权威在 [docs/templates/](../docs/templates/README-模板索引.md)**——本规范只定标准与红线，模板正文不在此重抄。
> **档位裁剪**：轻量（README + worklog）/ 标准（+ CHANGELOG + 设计文档）/ 团队（+ CONTRIBUTING + 代码地图）。

---

## 一、红线（必守）

| # | 红线 | 怎么验证 |
|---|------|---------|
| 1 | 项目根有 README，含「快速开始」（≤5 条命令跑起来） | 新人按 README 能独立启动 |
| 2 | 每次发布更新 CHANGELOG（Keep a Changelog 格式） | 发布检查 |
| 3 | 文档与代码在同一个 PR 中改（不脱节、不分离到 Wiki） | 审查 diff |
| 4 | 公共 API 有 docstring（参数/返回/异常/示例） | 审查 + 文档 lint |
| 5 | 注释与代码行为一致，过时注释立即删 | 审查 |

---

## 二、落地：模板复用 + 文档随码同步

所有文档从 [docs/templates/](../docs/templates/README-模板索引.md) 复制起步，不手搓：

| 要写的文档 | 用哪个模板 |
|-----------|-----------|
| 工作日志 | [worklog模板](../docs/templates/worklog模板.md) |
| 功能点 / 收束汇报 | [汇报模板](../docs/templates/汇报模板.md) |
| README / CHANGELOG / 设计文档 | 见模板索引 |

**docstring 范例（公共 API 必须）：**

```python
def create_order(user_id: str, product_id: str, quantity: int) -> Order:
    """创建订单。
    Args:    quantity: 购买数量，必须 > 0
    Returns: 订单对象，状态 pending
    Raises:  ValueError 库存不足 / UserNotFoundError / ProductNotFoundError
    """
```

**CHANGELOG 六分类**：Added / Changed / Fixed / Deprecated / Removed / Security——用**用户视角**写「这次更新对我有什么影响」，不是 git log 复制。

---

## 三、决策表 / 速查

### 何时写设计文档

| 写 | 不写 |
|----|------|
| 需求不清、需多方对齐 | 简单 CRUD |
| 复杂功能（≥3 模块） | 纯 UI 调整 |
| 重大架构变更 | 已有明确方案的小改 |

设计文档五段：背景 / 目标 / 方案（含 Mermaid 图）/ 影响范围 / 风险。放 `docs/plan/design/`。

### 文件命名

| 类型 | 格式 | 示例 |
|------|------|------|
| 普通文档 | kebab-case | `deployment-guide.md` |
| 日期相关 | `YYYY-MM-DD-描述` | `2026-05-26-会议纪要.md` |
| 工作日志 | `YYYY-MM-DD_描述.md` | `2026-05-26_功能A开发.md` |

> 禁空格 / 特殊字符，只允许字母数字 `-` `_` `.`。

### 知识互联（标准/团队档位）

| 规则 | 要点 |
|------|------|
| 文档间双向链接 | 设计文档 ↔ BDD ↔ 规范原文 互相引用 |
| 关键文档入 README 索引 | 超过 5 份文档时必须有分类索引 |

### 收束时的文档维护

| 规则 | 要点 |
|------|------|
| 收束节点检查文档一致性 | 逐份对照代码现状（[第三步](ai-workflow_AI协作开发流程/06-第三步_收束节点.md)） |
| 过时文档移 `archive/`，标废弃日期 | `> ⚠️ 已废弃: YYYY-MM-DD，替代: xxx` |

---

## 四、反模式（保留文档独有）

### ❌ README 有名无实
`# my-project / A cool project. / Usage: See code.` → 读者连项目做什么都不知道。
✅ 30 秒内回答 5 问：做什么 / 怎么跑 / 有什么功能 / 技术栈 / API 在哪。

### ❌ CHANGELOG 是 git log 复制
`- feat: add avatar / - chore: update deps / - refactor: clean up` → 用户不关心重构和依赖。
✅ 按 Added/Fixed/Security 写用户能感知的影响。

### ❌ 注释与代码不符
`"""通过用户名查询"""` 但实际查 `id` → 读者不知信注释还是信代码，比没注释更危险。
✅ 改代码同步改注释，参数名 / 字段名 / 行为描述三者一致。

---

## 五、检查清单

- [ ] 根目录有 README，含「快速开始」（拷贝即跑）
- [ ] CHANGELOG 按 Keep a Changelog 维护，发布即更新
- [ ] 公共 API 有完整 docstring
- [ ] 注释与代码行为一致，无过时注释
- [ ] 文件命名 kebab-case，无空格 / 特殊字符
- [ ] 复杂功能（≥3 模块）有五段设计文档
- [ ] 文档间双向链接（设计 ↔ BDD ↔ 规范）
- [ ] 过时文档已更新或归档
- [ ] 中大型项目有代码地图（见 08）

---

## 六、关联

| 方向 | 链接 |
|------|------|
| 细化自 | [§2.5 更新记录](ai-workflow_AI协作开发流程/04-第二步_迭代开发.md) · [07-汇报](ai-workflow_AI协作开发流程/07-汇报.md) |
| 模板权威 | [docs/templates/](../docs/templates/README-模板索引.md) |
| 验收标准 | [docs/specs/06-documentation.md](../docs/specs/06-documentation.md) |
| 代码地图 CODE_MAP / 知识图谱 | [08-代码理解与图谱规范](08-code-understanding_代码理解与图谱规范.md) |
| 文档一致性检查时机 | [第三步·收束节点](ai-workflow_AI协作开发流程/06-第三步_收束节点.md) |

---

## 更新记录

| 日期 | 版本 | 变更说明 |
|------|------|---------|
| 2026-05-27 | v2.0 | 重构为文档产出环节的细化；模板正文改为链接 docs/templates/；红线配验证；§2.9 代码地图移交 08 |
| 2026-05-27 | v1.1 | 新增 §2.8 知识互联 + §2.9 代码地图 |
| 2026-05-26 | v1.0 | 按统一模板改造 |
