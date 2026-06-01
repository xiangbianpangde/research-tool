# 接口注释清单 — M-010 错误处理器（DD-M-010）

> **生成方**：DD-M-010
> **日期**：2026-06-01
> **接口契约数**：2（IC-026、IC-027，对应 API-026、API-027）
> **覆盖率**：100%（2/2 IC 在文件注释中体现）

---

## API-026 错误登记（IC-026）

```
[接口编号] API-026
[关联契约] IC-026（来自 DD-001）
[实现文件] research_tool/error_handler.py
[函数签名注释]
  ```python
  def register_error(
      code: str,         # [参数说明] 错误码（E_DL_001 / E_LLM_001 等 25+ 种）
      exc: Exception,    # [参数说明] 异常对象（含原始堆栈信息）
      task_id: str,      # [参数说明] 任务 ID（来自 M-001 派发或 M-012 并发）
  ) -> ErrorRecord:      # [返回值说明] 已登记的 ErrorRecord（含 timestamp + stack）
      """
      错误码登记入口（IC-026）。

      Args:
          code: 错误码（必填，遵循 E_<CATEGORY>_<NUMBER>_<DETAIL> 命名）
          exc:  异常对象（必填，用于提取 traceback）
          task_id: 任务 ID（必填，非空校验）

      Returns:
          ErrorRecord: 已登记的错误记录

      Raises:
          无（错误码未注册时自动 fallback 到 E_SYS_001 登记）

      Example:
          >>> record = register_error(
          ...     "E_DL_001",
          ...     ValueError("invalid url"),
          ...     task_id="t-001",
          ... )
      """
  ```
[参数说明]
  - code: 25+ 种错误码之一，命名遵循 E_<CATEGORY>_<NUMBER>_<DETAIL>
  - exc:  任意 Exception 子类，用于 traceback.format_exc() 提取堆栈
  - task_id: 非空字符串，来自调用方（M-001/M-002/M-003/.../M-012）
[返回值说明]
  - 类型: ErrorRecord（DE-010）
  - 描述: 已登记的错误记录，含 code/msg/task_id/stack/timestamp
[错误码说明]
  - E_SYS_001: 未注册错误码 → 完整堆栈上报
[来源标注] [DD-001:IC-026] [DD-001:MD-010 函数签名 1]
```

---

## API-027 退出码仲裁（IC-027）

```
[接口编号] API-027
[关联契约] IC-027（来自 DD-001）
[实现文件] research_tool/error_handler.py
[函数签名注释]
  ```python
  def resolve_exit_code(
      records: List[ErrorRecord],  # [参数说明] 错误记录列表（可为空）
  ) -> int:                        # [返回值说明] CLI 退出码 ∈ {0, 1, 2, 3, 4}
      """
      退出码仲裁（IC-027）。

      优先级状态机: 403 > 401 > 500 > 0

      Args:
          records: 错误记录列表（可为空 → 返回 0）

      Returns:
          int: CLI 退出码
              0  = 全部成功
              1  = 500 类（系统/网络/未知）
              2  = 401 类（鉴权缺失）
              3  = 403 类（鉴权失败 / 权限拒绝）
              4  = 其它（保留）

      Raises:
          无（内部消化，仲裁失败时返回 1）

      Example:
          >>> records = [ErrorRecord(code="E_DL_BILI_403", ...)]
          >>> resolve_exit_code(records)
          3
      """
  ```
[参数说明]
  - records: 任意长度 List[ErrorRecord]（空列表时返回 0）
[返回值说明]
  - 类型: int
  - 描述: CLI 退出码
  - 特殊值: 0=成功 / 1=500类 / 2=401类 / 3=403类 / 4=其它
[错误码说明]
  - 无（仲裁内部不抛错）
[来源标注] [DD-001:IC-027] [DD-001:MD-010 函数签名 3]
```

---

## 内部 API（DD-M-010 推断登记）

```
[内部 API-1] format_error
[关联契约] N/A（内部 API）
[实现文件] research_tool/error_handler.py
[函数签名注释]
  ```python
  def format_error(
      record: ErrorRecord,  # [参数说明] 错误记录
  ) -> str:                 # [返回值说明] 3 段式错误信息字符串
      """3 段式错误信息格式化（场景/原因/建议）。"""
  ```
[来源标注] [DD-001:MD-010 函数签名 2] [DD-M推断:内部 API 推断登记]

[内部 API-2] lookup_code
[关联契约] N/A（内部 API，供其他模块反查）
[实现文件] research_tool/error_handler.py
[函数签名注释]
  ```python
  def lookup_code(
      code: str,  # [参数说明] 错误码
  ) -> ErrorInfo: # [返回值说明] 错误码静态信息（未注册时返回 E_SYS_001 占位）
      """查询错误码静态信息。"""
  ```
[来源标注] [DD-001:MD-010 函数签名 4] [DD-M推断:内部 API 推断登记]
```

---

## 接口契约验收汇总

| 契约 | 关联文件 | 函数签名注释 | 参数说明 | 返回值说明 | 错误码说明 | 通过 |
|------|---------|------------|---------|-----------|-----------|------|
| IC-026 | error_handler.py | 有 | 有 | 有 | 有 | 5/5 |
| IC-027 | error_handler.py | 有 | 有 | 有 | 有 | 5/5 |

**2/2 全部通过 5 项验收标准。**

---

> **本文件结束**。M-010 接口注释清单交付 DD-S。
