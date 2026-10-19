# 接口注释清单 — M-011 结构化日志器（DD-M-011）

> **生成方**：DD-M-011
> **负责模块**：M-011 结构化日志器（structured_logger）
> **日期**：2026-06-01
> **关联上游契约**：IC-028（API-028 日志写入）
> **覆盖率**：100%（1/1 IC 已映射为 5 个 API 函数签名注释）

---

## 接口契约映射表

| 接口契约编号 | 关联 API | 实现文件 | 函数签名注释 | 参数说明 | 返回值说明 | 错误码说明 |
|------------|---------|---------|------------|---------|-----------|-----------|
| IC-028 | API-028 | research_tool/structured_logger.py | 有 | 有 | 有 | 有 |

---

## IC-028 → 函数签名注释映射

### API-028.1: configure_logging

```
[接口编号] API-028.1
[关联契约] IC-028
[实现文件] research_tool/structured_logger.py
[函数签名注释]
  def configure_logging(
      log_dir: str,                    # [日志目录绝对路径]
      level: str = "INFO",             # [日志级别: DEBUG/INFO/WARN/ERROR]
      retention_days: int = 30,        # [保留天数: V1.1 默认 30 天]
      tz_offset_hours: int = 0         # [时区偏移: 0=UTC, +8=Asia/Shanghai]
  ) -> logging.Logger:                  # [已配置的根 logger，None 表示降级]
      """
      配置结构化日志管线：注册 JsonFormatter + DailyRotatingHandler + SensitiveFilter。

      Args:
          log_dir: 日志目录绝对路径（首次创建时自动 chmod 0o700）
          level: 日志级别阈值
          retention_days: 日志保留天数，超过的文件将被清理
          tz_offset_hours: 时区偏移小时数

      Returns:
          已配置的根 logger 实例

      Raises:
          无（路径不可写时降级 stderr，不抛错）

      Example:
          >>> logger = configure_logging("/var/log/research-tool")
          >>> emit_log("INFO", "M-011", msg="logger started")
      """
[来源标注] [DD-001:IC-VideoIngest-V1.1-20260601.md#ic-028]
```

### API-028.2: emit_log

```
[接口编号] API-028.2
[关联契约] IC-028
[实现文件] research_tool/structured_logger.py
[函数签名注释]
  def emit_log(
      level: str,                      # [日志级别: DEBUG/INFO/WARN/ERROR]
      module: str,                     # [模块名: M-001~M-012]
      task_id: str = "",               # [任务 ID]
      url_sha256: str = "",            # [URL sha256 摘要]
      step: str = "",                  # [当前步骤标识]
      duration_ms: int = 0,            # [耗时毫秒]
      code: str = "",                  # [错误码: 与 M-010 对齐]
      msg: str = "",                   # [文本消息]
      **kwargs: Any                    # [扩展字段，自动 redact + URL 哈希]
  ) -> None:                            # [无返回值]
      """
      写入单条结构化日志记录。

      Args:
          level: 日志级别
          module: 模块标识（M-001 ~ M-012）
          task_id: 任务 ID
          url_sha256: URL sha256 摘要（已计算）
          step: 当前步骤标识
          duration_ms: 耗时毫秒
          code: 错误码
          msg: 文本消息
          **kwargs: 扩展字段（敏感字段自动 redact）

      Returns:
          None

      Raises:
          无（路径不可写时降级 stderr）

      Example:
          >>> emit_log("INFO", "M-003", task_id="t1", step="download", duration_ms=12000)
      """
[来源标注] [DD-001:IC-VideoIngest-V1.1-20260601.md#ic-028]
```

### API-028.3: filter_sensitive

```
[接口编号] API-028.3
[关联契约] IC-028
[实现文件] research_tool/structured_logger.py
[函数签名注释]
  def filter_sensitive(
      record: logging.LogRecord         # [日志记录]
  ) -> bool:                             # [True 允许通过 / False 阻断]
      """
      入口级敏感字段过滤（隐式通过 logging Filter 机制调用）。

      Args:
          record: logging.LogRecord 实例

      Returns:
          True - 未命中黑名单允许通过
          False - 命中黑名单阻断

      Raises:
          无

      Example:
          >>> filter_sensitive(record_with_api_key)  # False
      """
[来源标注] [DD-001:IC-VideoIngest-V1.1-20260601.md#ic-028]
```

### API-028.4: hash_url

```
[接口编号] API-028.4
[关联契约] IC-028
[实现文件] research_tool/structured_logger.py
[函数签名注释]
  def hash_url(
      url: str                          # [待哈希 URL，长度 1-2048]
  ) -> str:                              # [64 字符 hex 摘要]
      """
      计算 URL 的 sha256 摘要。

      Args:
          url: 视频 URL 字符串

      Returns:
          64 字符 hex 摘要（小写）

      Raises:
          无（url 为空时返回空字符串）

      Example:
          >>> hash_url("https://www.bilibili.com/video/BV1xx")
          'abc123...'
      """
[来源标注] [DD-001:IC-VideoIngest-V1.1-20260601.md#ic-028]
```

### API-028.5: rotate_daily

```
[接口编号] API-028.5
[关联契约] IC-028
[实现文件] research_tool/structured_logger.py
[函数签名注释]
  def rotate_daily() -> str:            # [新日志文件路径；切分失败返回 ""]
      """
      强制触发按日切分（用于测试或手动归档）。

      Args:
          无

      Returns:
          新日志文件绝对路径；切分失败返回空字符串

      Raises:
          无（异常时 stderr 警告）

      Example:
          >>> rotate_daily()
          '/var/log/research-tool/research-tool-2026-06-02.log'
      """
[来源标注] [DD-001:IC-VideoIngest-V1.1-20260601.md#ic-028]
```

---

## 验收汇总

| 契约 | 函数 | 签名注释 | 参数说明 | 返回值 | 错误码 | 通过 |
|------|------|---------|---------|--------|--------|------|
| IC-028 | configure_logging | 有 | 有 | 有 | 有（降级说明）| 5/5 |
| IC-028 | emit_log | 有 | 有 | 有 | 有（降级说明）| 5/5 |
| IC-028 | filter_sensitive | 有 | 有 | 有 | 有 | 5/5 |
| IC-028 | hash_url | 有 | 有 | 有 | 有 | 5/5 |
| IC-028 | rotate_daily | 有 | 有 | 有 | 有 | 5/5 |

**D4 接口契约注释化完整度 = 100%**（1/1 IC × 5 函数全覆盖）。

---

> **本文件结束**。M-011 接口注释清单 1/1 IC × 5 函数全部覆盖，交付 DD-S 搭建骨架时参考。
