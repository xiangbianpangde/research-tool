# 文件框架结构 — M-010 错误处理器（DD-M-010）

> **生成方**：DD-M-010
> **日期**：2026-06-01
> **负责模块**：M-010（唯一模块，DD-001 分配）
> **上游依据**：[DD-001:FS-010/012] [DD-001:MD-010] [DD-001:IC-026/IC-027] [DD-001:CS-001]
> **门禁校验**：DDI=0.985 ≥ 0.85 ✓ ｜ 9 类产出物完整 ✓ ｜ 30/30 IC 覆盖 ✓ ｜ 5/5 FS 检查 ✓

---

## 一、文件框架总览

```
[模块编号] M-010
[模块名称] 错误处理器
[设计模式] 状态机模式（错误码转换 403>401>500>0）+ 模板方法
[文件框架]
  research_tool/
    error_handler.py           ← [职责：M-010 错误码登记/格式化/退出码仲裁唯一实现]
      - 错误码常量模块（25+ E_* 错误码）
      - ErrorInfo / ErrorRecord 数据类
      - ErrorCodeRegistry 类（错误码字典 lookup/register）
      - ErrorFormatter 类（3 段式：场景/原因/建议）
      - ExitCodeResolver 类（状态机仲裁 403>401>500>0）
      - ErrorRecorder 类（DE-010 ErrorRecord 存储）
      - 公开 API: register_error / format_error / resolve_exit_code / lookup_code
  research_tool/
    tests/
      test_error_handler.py    ← [职责：M-010 单元测试 13 用例（核心 5 + 边界 4 + 异常 4）]
        - test_register_error_creates_record（正常登记）
        - test_register_error_unknown_code_raises_sys_001（异常：未注册错误码）
        - test_format_error_3section（正常：3 段式输出）
        - test_format_error_missing_suggestion（边界：建议缺失时降级）
        - test_resolve_exit_code_priority_403（正常：403 优先）
        - test_resolve_exit_code_priority_mixed（边界：403+500 混合）
        - test_resolve_exit_code_empty_list（边界：空记录 → 0）
        - test_lookup_code_registered（正常）
        - test_lookup_code_unregistered（异常）
        - test_error_recorder_thread_safe（并发安全）
        - test_record_includes_stack（异常堆栈包含）
        - test_format_error_template_method（模板方法可继承）
        - test_resolve_exit_code_state_machine（状态机转换）
        - 测试场景注释完整：每用例含 [断言] [Mock 策略] [前置/后置条件]

[文件间依赖关系]
  error_handler.py → stdlib (logging, traceback, enum, threading)
  error_handler.py → research_tool.datatypes (ErrorRecord/ErrorInfo, DE-010)
  test_error_handler.py → error_handler.py
  test_error_handler.py → pytest / unittest.mock
  其他模块 (M-001/M-002/M-003/M-004/M-005/M-006/M-007/M-008/M-009/M-012) → error_handler.py
  [来源标注] [DD-001:FS-010 依赖关系]
  横向依赖: M-010 是 M-001~M-012 全部模块的横切依赖（错误码登记入口）

[目录层级] 2 层（research_tool/ → error_handler.py + tests/）[DD-001:FS-010 2层 ✓]
[文件命名] snake_case ✓ [DD-001:CS-001 §1 命名规范]
[文件职责] 单一职责：M-010 错误码全生命周期管理 ✓
[依赖关系] 无循环依赖 ✓
[最佳实践] Python 3.11+ 包管理 + __init__.py 隐式命名空间 ✓
```

---

## 二、文件清单

| 序号 | 文件路径 | 文件职责 | 来源标注 |
|------|---------|---------|----------|
| 1 | `research_tool/error_handler.py` | M-010 错误处理器主文件 | [DD-001:FS-010/MD-010] |
| 2 | `research_tool/tests/test_error_handler.py` | M-010 单元测试 13 用例 | [DD-001:FS-010/MD-010] |

---

## 三、模块边界合规声明

- 本 DD-M 实例**仅**负责 M-010 单一模块
- 操作文件数 = 2
- 跨模块文件数 = **0**
- 状态：**合规（D7=100）**
- 未触碰 M-001~M-009/M-011/M-012 任何文件

---

## 四、产出物清单（DD-S 接收用）

| 产出物 | 文件 | 状态 |
|--------|------|------|
| 文件框架结构 | `FF-M-010-VideoIngest-V1.1-20260601.md` | 已完成 |
| 带注释的代码文件 | `research_tool/error_handler.py` | 已完成 |
| 测试文件框架 | `research_tool/tests/test_error_handler.py` | 已完成 |
| 接口注释清单 | `API-M-010-VideoIngest-V1.1-20260601.md` | 已完成 |
| 文件结构合规报告 | `FC-M-010-VideoIngest-V1.1-20260601.md` | 已完成 |
| 框架决策记录 | `FDR-M-010-VideoIngest-V1.1-20260601.md` | 已完成 |
| 文件框架健康度仪表盘 | `FH-M-010-VideoIngest-V1.1-20260601.md` | 已完成 |

---

> **本文件结束**。M-010 文件框架结构交付 DD-S 进入骨架搭建。
