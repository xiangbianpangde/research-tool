# 框架决策记录（FDR） — M-011 结构化日志器（DD-M-011）

> **生成方**：DD-M-011
> **负责模块**：M-011 结构化日志器（structured_logger）
> **日期**：2026-06-01
> **决策数**：6 条（FDR-M011-001 ~ FDR-M011-006）

---

## FDR-M011-001: JsonFormatter 采用装饰器模式

```
[决策编号] FDR-M011-001
[决策标题] JsonFormatter 装饰 logging.LogRecord
[决策状态] 已接受
[决策内容] JsonFormatter 类继承 logging.Formatter，覆写 format(record) 方法，将 LogRecord 序列化为 JSON Lines 单行
[决策理由] 装饰器模式符合 stdlib logging 设计契约；可与 stdlib Handler 无缝集成；后续扩展字段映射（field_mapping）灵活
[拒绝的替代方案]
  方案B: 自定义 JSONEncoder + 独立写文件类
  拒绝理由: 与 stdlib logging 体系割裂，第三方日志聚合工具（ELK/Loki）无法复用 handler 配置
[影响范围] structured_logger.py:JsonFormatter
[相关FDR] 无
[来源标注] [DD-001:MD-VideoIngest-V1.1-20260601.md#m-011-结构化日志器 §设计模式]
```

## FDR-M011-002: DailyRotatingHandler 采用模板方法

```
[决策编号] FDR-M011-002
[决策标题] DailyRotatingHandler 继承 logging.Handler
[决策状态] 已接受
[决策内容] 覆写 emit(record) / should_rotate() / rotate() 三个方法实现按日切分
[决策理由] stdlib logging.Handler 已提供并发安全（RLock）与流式写入基础设施；模板方法复用基类能力
[拒绝的替代方案]
  方案B: 自己实现 DailyRotatingFileHandler 完全独立类
  拒绝理由: 需重新实现并发锁、flush、shutdown 等基础设施，开发成本高且易出错
[影响范围] structured_logger.py:DailyRotatingHandler
[相关FDR] 无
[来源标注] [DD-001:MD-VideoIngest-V1.1-20260601.md#m-011-结构化日志器 §设计模式]
```

## FDR-M011-003: 时区固定 UTC + LOG_TZ 覆盖

```
[决策编号] FDR-M011-003
[决策标题] 默认时区 UTC，跨时区部署通过 LOG_TZ 环境变量覆盖
[决策状态] 已接受
[决策内容] DailyRotatingHandler 初始化时使用 datetime.timezone.utc 作为默认时区；当 LOG_TZ 环境变量存在时尝试按 IANA 名加载
[决策理由] 跨时区服务器（UTC 容器 + 当地时间用户）部署时，固定本地时区会导致日志日期错位与重复归档；统一 UTC 是云原生最佳实践
[拒绝的替代方案]
  方案B: 始终使用 datetime.now()（本地时区）
  拒绝理由: Docker 容器本地时区不可控，CI 与生产环境易出现日志切分时机不一致
  方案C: 使用 time.tzset() 强制设置时区
  拒绝理由: 受操作系统环境变量 TZ 影响，不适合嵌入式场景
[影响范围] structured_logger.py:DailyRotatingHandler
[相关FDR] 无
[来源标注] [DD-001:MD-VideoIngest-V1.1-20260601.md#m-011-结构化日志器 §日志策略 30 天保留] [DD-M推断:依据 AR:BR-016 跨时区部署场景]
```

## FDR-M011-004: SensitiveFilter 递归深度上限 5 层

```
[决策编号] FDR-M011-004
[决策标题] SensitiveFilter 嵌套 dict 递归 redact 最大深度 5
[决策状态] 已接受
[决策内容] SensitiveFilter._redact_dict(d, depth=0) 内部递归调用，当 depth > 5 时停止递归并保留当前层
[决策理由] 默认日志 kwargs 嵌套深度不超过 4 层（M-001 argv.headers.cookie 等）；5 层是安全裕度，防止恶意超深嵌套触发 RecursionError
[拒绝的替代方案]
  方案B: 不限深度
  拒绝理由: 攻击者可构造 1000 层嵌套触发栈溢出，影响主进程稳定性
  方案C: 限制为 3 层
  拒绝理由: 实际日志结构（M-001 argv.headers）已可能 3 层，3 层限制太紧会误伤合法数据
[影响范围] structured_logger.py:SensitiveFilter
[相关FDR] 无
[来源标注] [DD-M推断:依据 M-001 argv 解析可能含 Cookie 嵌套 + Python 默认递归深度限制 1000]
```

## FDR-M011-005: log_dir 自动 chmod 0o700

```
[决策编号] FDR-M011-005
[决策标题] configure_logging 内部自动 chmod log_dir 为 0o700
[决策状态] 已接受
[决策内容] configure_logging 创建 log_dir 后立即调用 os.chmod(log_dir, 0o700) 满足 IC-028 前置条件
[决策理由] IC-028 契约前置条件要求 log_dir 0o700 权限；SEC 安全规范要求日志目录仅 owner 可读写；调用方易遗漏 chmod，自动 chmod 降低集成成本
[拒绝的替代方案]
  方案B: 由调用方负责 chmod
  拒绝理由: 错误码 E_SEC_LOG_PERMS 出现频率高，违反"失败安全"原则
  方案C: 检测权限不符时抛错
  拒绝理由: 抛错会中断 CLI 启动，影响核心下载流程可用性
[影响范围] structured_logger.py:configure_logging
[相关FDR] 无
[来源标注] [DD-001:IC-VideoIngest-V1.1-20260601.md#ic-028 §前置条件 日志目录 0o700] [DD-M推断:依据 SEC 安全规范要求]
```

## FDR-M011-006: 路径不可写降级 stderr 而非抛错

```
[决策编号] FDR-M011-006
[决策标题] 路径不可写时 emit_log 降级 stderr 输出 + WARN 警告
[决策状态] 已接受
[决策内容] 当 DailyRotatingHandler.emit 抛 OSError/PermissionError 时，fallback 到 sys.stderr.write + 一次 WARN 标记；不向上抛错
[决策理由] 日志是诊断工具，不应阻塞主链；CLI 启动时若日志目录不可写（如 Docker volume 挂载失败），应允许主流程继续运行
[拒绝的替代方案]
  方案B: 启动时即抛错
  拒绝理由: 致命化日志配置错误会阻塞用户实际可执行的下载流程，违反"渐进降级"原则
  方案C: 静默丢弃
  拒绝理由: 完全静默导致用户对日志丢失无感知，调试难度极高
[影响范围] structured_logger.py:emit_log / DailyRotatingHandler.emit
[相关FDR] 无
[来源标注] [DD-001:MD-VideoIngest-V1.1-20260601.md#m-011-结构化日志器 §异常处理 日志路径不可写 → 降级 stderr]
```

---

## FDR 决策矩阵

| 决策编号 | 状态 | 影响范围 | 决策类别 |
|---------|------|---------|---------|
| FDR-M011-001 | 已接受 | JsonFormatter | 架构模式 |
| FDR-M011-002 | 已接受 | DailyRotatingHandler | 架构模式 |
| FDR-M011-003 | 已接受 | DailyRotatingHandler | 时区策略 |
| FDR-M011-004 | 已接受 | SensitiveFilter | 安全防御 |
| FDR-M011-005 | 已接受 | configure_logging | 安全合规 |
| FDR-M011-006 | 已接受 | emit_log / emit | 错误处理 |

**D6 可追溯性 = 100%**：6/6 重大决策均含编号/状态/理由/拒绝方案/影响范围/来源标注。

---

> **本文件结束**。6 条 FDR 决策全部已接受，决策理由与拒绝方案完整。
