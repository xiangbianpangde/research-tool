# 架构设计规范

> **本规范是 [ai-workflow 第一步·编写计划](ai-workflow_AI协作开发流程/03-第一步_编写计划.md) 的细化**，把「技术选型 / 架构设计」环节展开成可执行标准，并作为 [第二步 §2.3 DeepSeek 审查](ai-workflow_AI协作开发流程/04-第二步_迭代开发.md)「架构层面」的判据。
>
> **档位裁剪**：轻量项目（< 5 功能点）只守 §一「红线」，不强制 ADR、调用图；标准 / 团队项目守全部。

---

## 一、红线（必守 · 审查命中任一条即打回）

| # | 红线 | 怎么自动抓 |
|---|------|-----------|
| 1 | 禁止循环依赖（模块间 A → B → A） | `import-linter` / `dependency-cruiser` 的 forbidden 契约 |
| 2 | 领域层不依赖框架 / ORM（只准标准库 + 领域接口） | `import-linter` layers 契约 |
| 3 | 禁止跨层调用（表现层直接调基础设施层） | `import-linter` layers 契约 + 审查 |
| 4 | 敏感配置禁止硬编码 | 见 [02-代码 §一 红线3](02-coding_代码编写规范.md)，本规范不重复，仅约束「配置如何分层管理」（§四） |

---

## 二、落地：依赖约束写成可校验的契约（复制即用）

架构规则若不可校验就会腐烂。在 CI 中跑依赖契约，违反即失败。

**Python — `importlinter.ini`：**

```ini
[importlinter]
root_package = myapp

[importlinter:contract:layers]
name = 分层单向依赖
type = layers
layers =
    presentation
    application
    domain
    infrastructure

[importlinter:contract:domain-pure]
name = 领域层不依赖框架
type = forbidden
source_modules = myapp.domain
forbidden_modules = fastapi, sqlalchemy, django
```

**TypeScript** 对应 `dependency-cruiser`（`.dependency-cruiser.js` 中配 `no-circular`、层间 `forbidden` 规则），同样挂进 CI。

> 跑 `lint-imports`（或 `depcruise`），红线 1/2/3 全部由工具拦截。

---

## 三、分层与「代码放哪层」决策表

```
表现层 (UI/API) → 应用层 (用例编排) → 领域层 (业务规则) → 基础设施层 (DB/缓存/外部服务)
依赖方向单向向下，下层不反向引用上层。
```

| 这段代码是…… | 放哪层 | 不应包含 |
|--------------|--------|----------|
| 接收请求、参数校验、返回响应 | 表现层 | 业务逻辑 |
| 用例编排、事务、权限校验 | 应用层 | 领域规则 |
| 实体、业务规则、领域服务 | 领域层 | 数据库 / 框架细节 |
| DB 访问、缓存、外部 API | 基础设施层 | 业务判断 |

---

## 四、模块划分、配置、技术选型（决策表）

### 模块划分（按规模选）

| 项目特征 | 划分方式 | 结构 |
|----------|----------|------|
| 业务复杂 / 多人 | 按业务域 | `src/{user,order,product}/{api,service,model}/` |
| 简单 / 小项目 | 按技术层 | `src/{controllers,services,models,utils}/` |
| 任何规模 | 公共代码归 `common/` | 避免跨业务模块直接引用 |

### 配置管理（红线级）

| 规则 | 要点 |
|------|------|
| 密钥经环境变量注入 | 不入代码 / 配置文件（详见 [02 红线3](02-coding_代码编写规范.md)） |
| 提供 `.example` 模板 | 真实配置不入库，`.example` 告诉新人要配什么 |
| 分环境 | dev / test / staging / prod 各自独立配置 |

### 技术选型（结论写入 ADR）

成熟稳定 > 新奇；社区活跃 > 功能强大；团队熟悉 > 理论最优；够用 > 过度准备。
选型理由写入 ADR（`worklogs/decisions/`，仅[收束节点](ai-workflow_AI协作开发流程/06-第三步_收束节点.md)产出）。

---

## 五、反模式（仅保留架构独有的两类）

### ❌ 循环依赖

```python
# user/service.py     from order.service import OrderService
# order/service.py    from user.service import UserService   # A→B→A
```
✅ 提取公共接口到 `common/interfaces.py`，两侧都依赖抽象，解除直接互引。

### ❌ 跨层调用

```python
# controllers/user_controller.py
def create_user(request):
    db.execute("INSERT INTO users ...")   # 表现层直接操作 DB
```
✅ 表现层 → 应用层 `UserService.register()` → 领域层 `User.create()` → 基础设施层 `repo.save()`。换 DB 只改基础设施层。

> 密钥硬编码反模式见 [02](02-coding_代码编写规范.md)；「依赖黑洞 / 孤儿模块」属调用图分析，见 [08](08-code-understanding_代码理解与图谱规范.md)。

---

## 六、检查清单（= 审查「架构层面」展开）

- [ ] 无循环依赖（层间、模块间）—— `lint-imports` 通过
- [ ] 领域层未依赖框架 / ORM
- [ ] 无跨层调用（表现层不直接碰基础设施层）
- [ ] 敏感信息经环境变量注入（见 02）
- [ ] 模块划分方式匹配项目规模
- [ ] 配置提供了 `.example` 模板，分环境
- [ ] 技术选型理由已记录（标准/团队档位：ADR）

---

## 七、关联

| 方向 | 链接 |
|------|------|
| 细化自 | [ai-workflow 第一步·编写计划](ai-workflow_AI协作开发流程/03-第一步_编写计划.md) |
| 验收标准 | [docs/specs/01-architecture.md](../docs/specs/01-architecture.md) |
| 密钥 / 代码级安全 | [02-代码编写规范](02-coding_代码编写规范.md) |
| 调用图 / 依赖黑洞 / 代码地图 | [08-代码理解与图谱规范](08-code-understanding_代码理解与图谱规范.md) |
| ADR 产出时机 | [ai-workflow 第三步·收束节点](ai-workflow_AI协作开发流程/06-第三步_收束节点.md) |

---

## 更新记录

| 日期 | 版本 | 变更说明 |
|------|------|---------|
| 2026-05-27 | v2.0 | 重构为第一步的细化；红线配 import-linter 契约；新增可校验配置；密钥反模式去重（→02）；依赖黑洞/孤儿模块/调用图移交 08 |
| 2026-05-27 | v1.1 | 新增 §2.7 依赖关系可视化 + 反模式4/5 |
| 2026-05-26 | v1.0 | 按统一模板改造 |
