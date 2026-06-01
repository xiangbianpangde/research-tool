# 代码编写规范

> **本规范是 [ai-workflow 第二步·迭代开发](ai-workflow_AI协作开发流程/04-第二步_迭代开发.md) 的细化**，把工作流里两个一句话带过的环节展开成可执行标准：
> - 细化 **§2.1 Opus 实现** —— 写代码时必须满足的硬约束（命名 / 结构 / 错误处理 / 安全 / 日志）。
> - 细化 **§2.3 DeepSeek 审查** —— 审 `git diff` 时逐条核对的清单。本规范 §六「检查清单」就是该环节审查清单的展开版。
>
> **档位裁剪**（档位定义见 [第一步 §1.0](ai-workflow_AI协作开发流程/03-第一步_编写计划.md)）：轻量项目只守 §一「红线」；标准 / 团队项目守全部。

---

## 一、红线（必守 · DeepSeek 审查命中任一条即打回）

每条红线都配「怎么自动抓」——规则要能被工具拦下，而不是靠人记。

| # | 红线 | 怎么自动抓 |
|---|------|-----------|
| 1 | 禁止 `print()` / `console.log` 入库，统一用 logging | ruff `T20` / eslint `no-console` |
| 2 | SQL 必须参数化，禁止字符串拼接 | ruff `S608` (bandit) + 审查 |
| 3 | 密钥 / Token / 密码禁止硬编码 | ruff `S105-S107` + `gitleaks` 扫描；`.env` 必须在 `.gitignore` |
| 4 | 禁止静默吞异常（`except: pass` / `catch {}`） | ruff `E722` `S110` |
| 5 | 禁止提交注释掉的代码块、调试断点（`pdb` / `debugger`） | ruff `T100` + 审查 |
| 6 | 日志禁止输出敏感信息，须脱敏（`phone=138****1234`） | 审查（无可靠自动规则，列为 §六 必查项） |
| 7 | 所有外部输入必须校验后再使用（白名单优先） | 审查 + 类型校验库（pydantic / zod） |

> 红线 2/3 已是红线，下文 §五「反模式」不再重复；架构层的「跨层调用」「循环依赖」见 [01-架构](01-architecture_架构设计规范.md)，本规范不重复。

---

## 二、落地：把规则装进工具（复制即用）

可操作的关键在于——规则不靠自觉，靠 pre-commit 和 CI 拦截。新项目在 [第一步 §1.9 创建基础设施](ai-workflow_AI协作开发流程/03-第一步_编写计划.md) 时一并落地。

**Python — `pyproject.toml`：**

```toml
[tool.ruff]
line-length = 100
[tool.ruff.lint]
# T20=print  S=安全(bandit)  E722=裸except  T100=调试断点  N=命名  PLR0915=函数过长
select = ["E", "F", "N", "T20", "T100", "S", "PLR0915"]
[tool.ruff.lint.pylint]
max-statements = 50   # 函数语句数警告阈值，可按项目调
```

**`.pre-commit-config.yaml`（提交前自动拦截）：**

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.0
    hooks:
      - id: ruff           # lint
      - id: ruff-format    # 格式化
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.18.0
    hooks:
      - id: gitleaks       # 阻止密钥入库
```

**TypeScript** 对应：eslint（`no-console`、`@typescript-eslint/no-explicit-any`）+ prettier，同样挂进 pre-commit。

> 装好后，红线 1/3/4/5 在 `git commit` 时被自动挡下，DeepSeek 审查只需聚焦工具抓不到的红线 6/7 和业务逻辑。

---

## 三、写代码时的决策表

数值阈值一律是 **linter 警告级、可按项目调**，不是铁律——触发时要么拆分，要么写一行注释说明为何例外。

### 命名

| 元素 | 风格 | 示例 |
|------|------|------|
| 变量 / 函数 | camelCase / snake_case（项目内统一） | `userName` / `user_name` |
| 类 / 接口 / 组件 | PascalCase | `UserService` |
| 常量 | UPPER_SNAKE | `MAX_RETRY_COUNT` |
| 布尔 | is/has/can 前缀 | `isActive` `hasPermission` |
| 文件名 | kebab-case / snake_case，描述性全名（禁缩写） | `user-service.ts` |

> 见名知意是红线级要求：禁止 `data` `info` `temp` `process` 等模糊名——审查时若读名字猜不出用途即打回。

### 结构（超阈值 → 拆分或注释例外）

| 单元 | 警告阈值 | 超了怎么办 |
|------|---------|-----------|
| 函数行数 | ~50 | 抽子函数；算法密集型加注释说明 |
| 函数参数 | 4 | 用对象 / dataclass 传参 |
| 类成员变量 | 7 | 按职责拆成多个类 |
| 单文件行数 | 500 | 拆模块 |

### 注释

| 何时必须写 | 何时不要写 |
|-----------|-----------|
| 复杂算法（解释思路与选型理由） | 复述代码的废话（`i++ // 自增`） |
| 业务规则（解释「为什么这样处理」） | 与代码已不符的过时注释（直接删） |
| 临时方案标记 | |

```python
# TODO(张三): 2026-06-01 前替换为异步实现
# FIXME: 并发下有 data race，需加锁
# HACK: 绕过第三方库 bug，2.0 修复后移除
```

---

## 四、错误处理与日志（实现阶段必做）

**原则：尽早失败、明确传播、留足上下文。**

```python
import logging
logger = logging.getLogger(__name__)

def fetch_user(user_id: str) -> User:
    try:
        return db.query("SELECT * FROM users WHERE id = ?", [user_id])  # 参数化
    except UserNotFoundError:
        raise                                    # 明确传播，让上层决定
    except DatabaseError as e:
        logger.error("查询用户失败 | user_id=%s | error=%s", user_id, e)  # 含上下文
        raise ServiceUnavailableError("数据库服务不可用") from e
```

| 规则 | 要点 |
|------|------|
| 用具体异常类型 | 捕 `UserNotFoundError`，不捕裸 `Exception` |
| 最外层统一兜底 | 全局处理器捕未处理异常 → 标准化错误响应（响应格式见 [04-API](04-api_API设计规范.md)） |
| 资源用上下文管理器 | 文件 / 连接 / 锁用 `with` 自动释放 |
| 日志级别 | DEBUG（生产关）/ INFO（关键节点）/ WARN（可恢复）/ ERROR（需关注） |
| 日志带业务上下文 | `user_id` `order_id` 等；禁高频循环刷屏 |

---

## 五、反模式（仅保留本规范独有、高频的两类）

### ❌ 模糊命名

```python
def process(d):           # 看完函数体才知道干嘛
    return d[0] + d[1]
```
✅ `def calculate_total_price(item_prices: list[float]) -> float:` —— 名字即文档，调用方不读实现就懂。

### ❌ 静默吞异常

```python
def fetch_user(user_id):
    try:
        return db.query(...)
    except Exception:
        pass              # 返回 None，调用方不知出错，排查时日志空白
```
✅ 见 §四：分类型捕获，明确传播或记日志后转换。静默失败让 bug 在远处才暴露，上下文已丢。

---

## 六、检查清单（= DeepSeek 审查 §2.3 的展开版）

审 `git diff` 时逐条核对；前 7 项对应红线，工具能抓的已在 pre-commit 拦下，这里复核工具盲区与业务逻辑。

- [ ] 无 `print()` / `console.log`（用 logging）
- [ ] SQL 全部参数化，无字符串拼接
- [ ] 无硬编码密钥 / Token / 密码（环境变量注入）
- [ ] 无 `except: pass` 静默吞异常；用具体异常类型
- [ ] 无注释掉的代码块、调试断点
- [ ] 日志不含密码 / Token / 身份证号等敏感信息
- [ ] 所有外部输入已校验
- [ ] 命名见名知意，风格与项目一致（无 `data`/`temp`/`process`）
- [ ] 函数单一职责，超阈值处已拆分或注释例外
- [ ] 无越界变更（只改本功能点该改的）
- [ ] 未与已有模块重复造轮子（对照 `CLAUDE.md` 代码库图谱）

---

## 七、关联

| 方向 | 链接 |
|------|------|
| 细化自 | [ai-workflow 第二步 §2.1 实现 / §2.3 审查](ai-workflow_AI协作开发流程/04-第二步_迭代开发.md) |
| 验收标准 | [docs/specs/02-coding.md](../docs/specs/02-coding.md) |
| 分层 / 跨层 / 循环依赖 | [01-架构设计规范](01-architecture_架构设计规范.md) |
| 错误响应格式 | [04-API 设计规范](04-api_API设计规范.md) |
| 中大型项目的调用图 / 可理解性 | [08-代码理解与图谱规范](08-code-understanding_代码理解与图谱规范.md) |

---

## 更新记录

| 日期 | 版本 | 变更说明 |
|------|------|---------|
| 2026-05-27 | v2.0 | 重构为 ai-workflow 第二步的细化：挂接 §2.1/§2.3；红线配自动检测；新增 lint/pre-commit 落地配置；阈值降为可调警告；调用图内容移交 08；去重密钥/跨层反模式 |
| 2026-05-27 | v1.1 | 新增 §2.8 代码可理解性 |
| 2026-05-26 | v1.0 | 按统一模板改造 |
