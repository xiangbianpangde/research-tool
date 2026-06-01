# 框架决策记录（FDR）— M-001（DD-M-001）

> **生成方**：DD-M-001
> **日期**：2026-06-01
> **负责模块**：M-001 cli_bindings
> **决策数**：3

---

## FDR-M001-001 单一文件 vs 子目录拆分

```
[决策编号] FDR-M001-001
[决策标题] cli.py 采用单一文件而非子目录拆分
[决策状态] 已接受
[决策内容] M-001 全部 5 个类 + 5 个函数 + 1 个 main 入口聚合在 research_tool/cli.py 单文件中
[决策理由]
  - 与 DD-001 FS-001 文件结构规范一致（FS 明确 research_tool/cli.py）
  - 5 类 + 5 函数方法总数 = 19（< soul 4.2 单文件函数上限 20）
  - 减少模块间导入复杂度
  - 保持与其他模块（M-002~M-012）的命名一致性
[拒绝的替代方案]
  方案 B: cli/__init__.py + arg_parser.py + platform_resolver.py + config_loader.py + dispatcher.py + rag_entry.py
  拒绝理由: 与 FS-001 命名规范冲突（FS 明确 research_tool/cli.py 而非 cli/ 包）；跨实例隔离需重新协调；过度拆分
[影响范围]
  - research_tool/cli.py（直接）
  - 测试文件 test_cli.py（直接）
  - 不影响其他模块
[相关FDR] 无
[来源标注] [DD-001:FS-001] [DD-M推断:4.11 多方案对比]
```

---

## FDR-M001-002 API Key 强制环境变量加载

```
[决策编号] FDR-M001-002
[决策标题] LLMConfigLoader 强制 API Key 从环境变量加载
[决策状态] 已接受
[决策内容] LLMConfigLoader.load_from_env() 强制从 os.getenv() 读取 API Key，不接受文件配置（仅允许环境变量覆盖）
[决策理由]
  - 防止 API Key 落盘泄露（PII 保护）
  - 与 M-011 SensitiveFilter 协同（api_key / cookie / prompt 字段强制过滤）
  - 符合 12-Factor App 配置管理原则
  - 简化部署（K8s Secret / .env 文件均可注入）
[拒绝的替代方案]
  方案 B: 接受 --api-key CLI 参数
  拒绝理由: CLI 参数会被 `ps`/`history` 泄露，违反"永不落盘"原则
  方案 C: 接受配置文件 config.yaml
  拒绝理由: 配置文件会被 git 误提交；环境变量更安全
[影响范围]
  - cli.py: LLMConfigLoader.load_from_env() / override()
  - 不影响 M-006 llm_client.py（M-006 仅读取已加载的配置）
[相关FDR] 无
[来源标注] [DD-M推断:IC-028 敏感字段过滤] [DD-M推断:12-Factor App]
```

---

## FDR-M001-003 argv 日志仅存 sha256

```
[决策编号] FDR-M001-003
[决策标题] CLI argv 日志仅存 sha256 哈希（前 16 字符），不存明文
[决策状态] 已接受
[决策内容] main() 入口调用 M-011 emit_log 时，argv 字段使用 sha256(argv_string)[:16] 而非明文
[决策理由]
  - 避免 URL（可能含 PII）在日志中泄露
  - 满足 IC-001 日志策略（仅 argv_sha256）
  - 保留审计能力（重复请求可通过 sha256 检测）
  - 与 M-011 UrlHasher 协同
[拒绝的替代方案]
  方案 B: 不记录 argv
  拒绝理由: 失去审计能力，无法追踪重复请求
  方案 C: 记录明文但 redact 敏感字段
  拒绝理由: redact 规则复杂，容易遗漏；sha256 简单可靠
[影响范围]
  - cli.py: main() 入口日志调用
  - 不影响 M-011 structured_logger.py（M-011 接收的是已 sha256 化的字段）
[相关FDR] 无
[来源标注] [DD-001:MD-001 日志策略] [DD-001:IC-028]
```

---

## 决策汇总

| 决策编号 | 标题 | 状态 |
|---------|------|------|
| FDR-M001-001 | 单一文件 vs 子目录拆分 | 已接受 |
| FDR-M001-002 | API Key 强制环境变量加载 | 已接受 |
| FDR-M001-003 | argv 日志仅存 sha256 | 已接受 |

**3/3 重大框架决策已记录。**

---

> **本文件结束**。M-001 框架决策记录完整，覆盖文件组织、配置安全、日志隐私三个维度。
