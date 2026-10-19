# [文件路径] research_tool/structured_logger.py
# [文件职责]  M-011 结构化日志器主体，含 JsonFormatter/DailyRotatingHandler/SensitiveFilter/UrlHasher 4 类 + 5 函数签名
# [所属模块] M-011（来自DD-001）
# [关联设计规范] MD-VideoIngest-V1.1-20260601.md#m-011-结构化日志器 / FS-VideoIngest-V1.1-20260601.md / IC-VideoIngest-V1.1-20260601.md#ic-028
# [关联接口契约] IC-028（API-028 日志写入）
# [功能描述]
#   功能1: JSON Lines 格式化输出（装饰器模式 - LogRecord → JSON 字符串）
#   功能2: 按日切分 Handler（模板方法 - 跨日自动 rotate + 30 天保留）
#   功能3: 敏感字段过滤（API_KEY/COOKIE/PROMPT 黑名单 redact，含嵌套 dict 递归）
#   功能4: URL 哈希替换（明文 URL → sha256，避免日志泄露视频地址）
# [输入输出]
#   输入: 调用方传入日志字段 kwargs（level/module/task_id/url_sha256/step/duration_ms/code/msg）
#   输出: JSON Lines 单行写入按日切分的日志文件，路径不可写时降级 stderr
# [依赖关系]
#   依赖文件: 无（基础设施模块 - 仅依赖 Python stdlib）
#   被依赖文件: cli.py (M-001) / preflight.py (M-002) / downloader.py (M-003) / cache_manager.py (M-004) / transcriber.py (M-005) / llm_client.py (M-006) / notes_schema.py (M-007) / pipeline_adapter.py (M-008) / ffmpeg_wrapper.py (M-009) / error_handler.py (M-010) / concurrent_orchestrator.py (M-012)
# [注意事项]
#   注意1: 严禁 import 任何 research_tool.* 子模块以避免循环导入（M-011 是横切基础设施锚点）
#   注意2: log_dir 必须 0o700 权限（IC-028 前置条件 + SEC 安全规范），configure_logging 内部自动 chmod
#   注意3: DailyRotatingHandler 切分基于 timezone.utc（V1.1 默认），跨时区部署需通过 LOG_TZ 环境变量覆盖
#   注意4: SensitiveFilter 递归深度上限 5 层，防止恶意超深嵌套触发栈溢出
#   注意5: emit_log 不返回任何值，调用方不应依赖返回值做控制流
# [代码风格] 遵循 CS-001（Python 4 空格 / 120 行宽 / Google Docstring / 全类型注解 / 双引号 / LF）
# [创建日期] 2026-06-01
# [修改历史]
#   2026-06-01: DD-M-011 - 初始创建（仅含注释与签名骨架，无业务代码）
# [作者] DD-M-011-20260601
# [来源标注] [DD-001:MD-VideoIngest-V1.1-20260601.md#m-011-结构化日志器] [DD-001:IC-VideoIngest-V1.1-20260601.md#ic-028] [DD-001:FS-VideoIngest-V1.1-20260601.md#模块文件结构规范] [DD-001:CS-VideoIngest-V1.1-20260601.md#cs-001]
"""M-011 结构化日志器模块。

[职责] 提供 JSON Lines 格式化 + 按日切分 + 敏感字段过滤 + URL 哈希的基础设施日志能力。

[设计模式]
- 装饰器模式: JsonFormatter 装饰 logging.LogRecord，转 dict → JSON 字符串
- 模板方法模式: DailyRotatingHandler 继承 logging.Handler，覆写 emit/should_rotate 实现按日切分

[来源标注] [AR:API-028] [AR:DP-011] [AR:BR-016 30 天保留] [AR:DE-014 日志格式契约]
"""

# [标准库导入 - soul 3.4 装饰器/模板方法所需]
# [来源标注] [DD-001:CS-001 §导入规范 - 标准库优先]


# === 常量定义段 ===
# [来源标注] [DD-001:MD-VideoIngest-V1.1-20260601.md#m-011-结构化日志器 日志策略 30 天保留] [DD-M推断:依据 IC-028 入参约定]


# === 类 JsonFormatter ===
# [类名] JsonFormatter
# [职责] 将 LogRecord 序列化为 JSON Lines 单行
# [关联设计规范] MD-VideoIngest-V1.1-20260601.md#m-011-结构化日志器
# [设计模式] 装饰器模式（输入 LogRecord → 输出 JSON 字符串）
# [属性]
#   属性1: field_mapping dict[str, str] 字段重映射表（log record attr → JSON key）
#   属性2: ensure_ascii bool JSON dump 时是否强制 ASCII（默认 True 避免编码问题）
# [方法列表]
#   方法1: format(record: LogRecord) -> str - 序列化为单行 JSON
#   方法2: _format_field(value: Any) -> Any - 字段值归一化（含 datetime → ISO8601）
# [状态机] N/A（无状态转换）
# [异常处理]
#   异常1: TypeError - 字段值不可序列化时降级为 str(value)
#   异常2: ValueError - JSON 编码失败时降级为最小字段集 {ts, level, module, msg}
# [来源标注] [DD-001:MD-VideoIngest-V1.1-20260601.md#m-011-结构化日志器] [DD-M推断:依据 AR:DE-014 日志格式契约]


# === 类 DailyRotatingHandler ===
# [类名] DailyRotatingHandler
# [职责] 按日切分 JSON Lines 日志文件，保留 30 天
# [关联设计规范] MD-VideoIngest-V1.1-20260601.md#m-011-结构化日志器
# [设计模式] 模板方法模式（继承 logging.Handler，覆写 emit/should_rotate）
# [属性]
#   属性1: log_dir Path 日志目录路径
#   属性2: prefix str 文件名前缀（默认 "research-tool"）
#   属性3: retention_days int 保留天数（默认 30，V1.1 默认值）
#   属性4: current_date date 当前打开文件对应的日期（用于切分判断）
#   属性5: current_file TextIO 当前打开的文件句柄
#   属性6: tz timezone 时区对象（默认 timezone.utc，LOG_TZ 可覆盖）
# [方法列表]
#   方法1: emit(record: LogRecord) -> None - 写入单行 JSON Lines
#   方法2: should_rotate() -> bool - 判断是否需要切分
#   方法3: rotate() -> None - 关闭旧文件 + 打开新文件
#   方法4: cleanup_expired() -> int - 清理超过 retention_days 的旧文件
#   方法5: _filename_for(d: date) -> str - 生成日期文件名
# [状态机]
#   OPEN(day1) → [跨日] → ROTATE → OPEN(day2)
#   任意状态 → [路径不可写] → STDERR_FALLBACK
# [异常处理]
#   异常1: PermissionError - 日志目录不可写 → 降级 stderr + 警告日志
#   异常2: OSError - 切分文件失败 → 保留当前文件继续写
#   异常3: 内部线程安全由 logging.Handler 基类 RLock 保障
# [来源标注] [DD-001:MD-VideoIngest-V1.1-20260601.md#m-011-结构化日志器] [DD-001:CS-001 §异常处理规范]


# === 类 SensitiveFilter ===
# [类名] SensitiveFilter
# [职责] 黑名单字段 redact，过滤 API_KEY/COOKIE/PROMPT 等敏感数据
# [关联设计规范] MD-VideoIngest-V1.1-20260601.md#m-011-结构化日志器
# [属性]
#   属性1: blacklist set[str] 敏感字段名集合（API_KEY/COOKIE/PROMPT 等）
#   属性2: redaction str 替换占位符（默认 "***REDACTED***"）
#   属性3: max_depth int 递归最大深度（默认 5）
# [方法列表]
#   方法1: filter(record: LogRecord) -> bool - 过滤 LogRecord，命中黑名单返回 True 阻断
#   方法2: redact(record: LogRecord) -> None - 原地 redact record 中敏感字段
#   方法3: _redact_dict(d: dict, depth: int) -> dict - 递归 redact 嵌套 dict
#   方法4: _is_sensitive(key: str) -> bool - 大小写不敏感匹配
# [状态机] N/A
# [异常处理]
#   异常1: RecursionError - 嵌套超过 max_depth 时停止递归并保留当前层
#   异常2: KeyError - 字段不存在时静默跳过
# [来源标注] [DD-001:MD-VideoIngest-V1.1-20260601.md#m-011-结构化日志器] [DD-M推断:依据 M-001 argv 解析可能含 Cookie 嵌套 + Python 递归深度限制 1000]


# === 类 UrlHasher ===
# [类名] UrlHasher
# [职责] URL 字段哈希替换，明文 URL → sha256
# [关联设计规范] MD-VideoIngest-V1.1-20260601.md#m-011-结构化日志器
# [属性]
#   属性1: url_fields tuple[str, ...] URL 字段名集合（默认 ("url", "video_url", "source_url")）
#   属性2: hash_algorithm str 算法名（默认 "sha256"）
# [方法列表]
#   方法1: hash_url(url: str) -> str - 计算 sha256 摘要
#   方法2: replace_in_dict(d: dict) -> dict - 原地替换 dict 中 URL 字段
#   方法3: _normalize_url(url: str) -> str - 标准化（去尾斜杠/去 query string 中 token）
# [状态机] N/A
# [异常处理]
#   异常1: TypeError - url 非 str 类型时降级为 repr(url) 后哈希
# [来源标注] [DD-001:MD-VideoIngest-V1.1-20260601.md#m-011-结构化日志器] [DD-M推断:依据 IC-028 url_sha256 字段约定]


# === 函数 configure_logging ===
# [函数名] configure_logging
# [职责] 注册 Formatter/Handler/Filter，构建结构化日志管线
# [关联接口契约] IC-028（API-028 日志写入）
# [参数说明]
#   参数1: log_dir str 必填 无默认 日志目录路径
#   参数2: level str 可选 "INFO" 日志级别（DEBUG/INFO/WARN/ERROR）
#   参数3: retention_days int 可选 30 保留天数（V1.1 默认）
#   参数4: tz_offset_hours int 可选 0 时区偏移（0=UTC，+8=Asia/Shanghai）
# [返回值]
#   类型: logging.Logger
#   描述: 已配置的根 logger
#   特殊值: None - 当 log_dir 创建失败时返回降级到 stderr 的 logger
# [错误码] -（路径不可写 → stderr 降级，不抛错）
# [前置条件] 调用方需确保工作目录可写
# [后置条件] 根 logger 已绑定 JsonFormatter + DailyRotatingHandler + SensitiveFilter
# [并发安全] 是（logging 内部 RLock）
# [幂等性] 是（多次调用以最后一次为准）
# [性能约束] 首次调用 < 100ms / 后续 < 5ms
# [示例]
#   logger = configure_logging("/var/log/research-tool", level="DEBUG")
#   emit_log("INFO", "M-003", task_id="t1", url_sha256="abc", step="download", duration_ms=12000)
# [来源标注] [DD-001:IC-VideoIngest-V1.1-20260601.md#ic-028] [DD-001:MD-VideoIngest-V1.1-20260601.md#m-011-结构化日志器]


# === 函数 emit_log ===
# [函数名] emit_log
# [职责] 写入单条结构化日志记录
# [关联接口契约] IC-028（API-028 日志写入）
# [参数说明]
#   参数1: level str 必填 无默认 日志级别（DEBUG/INFO/WARN/ERROR）
#   参数2: module str 必填 无默认 模块名（M-001~M-012）
#   参数3: task_id str 可选 "" 任务 ID
#   参数4: url_sha256 str 可选 "" URL 哈希（已计算）
#   参数5: step str 可选 "" 当前步骤
#   参数6: duration_ms int 可选 0 耗时（毫秒）
#   参数7: code str 可选 "" 错误码（与 M-010 错误码对齐）
#   参数8: msg str 可选 "" 文本消息
#   参数9: **kwargs Any 可选 {} 扩展字段（自动 redact + 哈希）
# [返回值]
#   类型: None
#   描述: 无返回值
# [错误码] -（路径不可写 → stderr 降级）
# [前置条件] configure_logging 已调用
# [后置条件] 单行 JSON Lines 已追加到当日日志文件
# [并发安全] 是（logging 内置 RLock + Handler 内部锁）
# [幂等性] 否（每次都追加新行）
# [性能约束] < 5ms / 单条
# [示例]
#   emit_log("INFO", "M-011", task_id="t1", step="init", msg="logger started")
# [来源标注] [DD-001:IC-VideoIngest-V1.1-20260601.md#ic-028]


# === 函数 filter_sensitive ===
# [函数名] filter_sensitive
# [职责] 入口级敏感字段过滤封装
# [关联接口契约] IC-028（API-028 日志写入 - 隐式通过 SensitiveFilter 生效）
# [参数说明]
#   参数1: record logging.LogRecord 必填 无默认 日志记录
# [返回值]
#   类型: bool
#   描述: True 允许通过 / False 阻断（命中黑名单字段时阻断）
#   特殊值: True - 未命中敏感字段
# [错误码] -（不抛错）
# [前置条件] SensitiveFilter 已绑定到 logger handler
# [后置条件] record 中敏感字段已 redact（若未阻断）
# [并发安全] 是
# [幂等性] 是
# [性能约束] < 1ms / 单条
# [来源标注] [DD-001:MD-VideoIngest-V1.1-20260601.md#m-011-结构化日志器]


# === 函数 hash_url ===
# [函数名] hash_url
# [职责] 计算 URL 的 sha256 摘要
# [关联接口契约] IC-028（API-028 日志写入 - 隐式为 url_sha256 字段计算）
# [参数说明]
#   参数1: url str 必填 无默认 待哈希 URL
# [返回值]
#   类型: str
#   描述: 64 字符 hex 摘要
#   特殊值: 空字符串 - url 为空时
# [错误码] -（不抛错）
# [前置条件] url 长度 1-2048
# [后置条件] 返回值长度 = 64
# [并发安全] 是
# [幂等性] 是（相同 URL 始终返回相同摘要）
# [性能约束] < 1ms / 单条
# [示例]
#   hash_url("https://www.bilibili.com/video/BV1xx") -> "abc123..."
# [来源标注] [DD-001:MD-VideoIngest-V1.1-20260601.md#m-011-结构化日志器]


# === 函数 rotate_daily ===
# [函数名] rotate_daily
# [职责] 强制触发按日切分（用于测试 + 手动归档）
# [关联接口契约] IC-028（API-028 日志写入 - 切分执行）
# [参数说明]
#   参数1: 无
# [返回值]
#   类型: str
#   描述: 新日志文件路径
#   特殊值: "" - 切分失败时
# [错误码] -（不抛错，stderr 警告）
# [前置条件] DailyRotatingHandler 已绑定
# [后置条件] 旧文件已关闭 + 新文件已打开
# [并发安全] 是（logging 内置锁）
# [幂等性] 否（每次调用都会创建新文件）
# [性能约束] < 50ms
# [来源标注] [DD-001:MD-VideoIngest-V1.1-20260601.md#m-011-结构化日志器]


# === 公共 API 导出声明 ===
# [来源标注] [DD-M推断:依据 soul 3.5 文件结构规范 - 公共接口导出]
__all__ = [
    # 类
    "JsonFormatter",
    "DailyRotatingHandler",
    "SensitiveFilter",
    "UrlHasher",
    # 函数
    "configure_logging",
    "emit_log",
    "filter_sensitive",
    "hash_url",
    "rotate_daily",
]
