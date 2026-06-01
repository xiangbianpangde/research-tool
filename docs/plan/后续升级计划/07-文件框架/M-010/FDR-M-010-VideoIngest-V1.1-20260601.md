# 框架决策记录 — M-010 错误处理器（DD-M-010）

> **生成方**：DD-M-010
> **日期**：2026-06-01
> **决策数**：5

---

## FDR-M010-001 单例模式（模块级单例）

```
[决策编号] FDR-M010-001
[决策标题] M-010 模块级单例（registry/recorder/formatter/resolver）
[决策状态] 已接受
[决策内容] error_handler.py 模块加载时构造 4 个单例：_registry / _recorder / _formatter / _resolver，避免在每个公开 API 内重复实例化
[决策理由]
  - M-010 是 M-001~M-012 全部模块的横切依赖，进程级唯一性可避免并发竞争
  - 全局 4 个单例共享同一 ErrorRecorder 状态，便于跨模块错误聚合
  - Python 模块天然单例（import 一次），零额外开销
[拒绝的替代方案]
  - 方案A: 每次调用构造新实例 → 拒绝理由：无法聚合跨模块错误
  - 方案B: 依赖注入容器 → 拒绝理由：12 个模块均需注入，复杂度反而升高
[影响范围] error_handler.py 全部公开 API
[相关FDR] 无
[来源标注] [DD-M推断:基于 M-010 横向依赖特性 + M-010 全局唯一要求]
```

---

## FDR-M010-002 状态机模式（错误码→退出码）

```
[决策编号] FDR-M010-002
[决策标题] 退出码仲裁采用状态机模式 403>401>500>0
[决策状态] 已接受
[决策内容] ExitCodeResolver 实现为状态机，转换路径: INIT → [resolve] → RESOLVE → [apply priority] → EXIT_CODE
[决策理由]
  - 多任务错误码仲裁需明确优先级（403>401>500>0），状态机更易表达
  - 状态机便于扩展：未来增加新退出码只需追加状态
  - 符合 DD-001 MD-010 状态机定义
[拒绝的替代方案]
  - 方案A: 简单 if-elif 链 → 拒绝理由：难以扩展到 10+ 退出码
  - 方案B: 字典查表 → 拒绝理由：无法表达复杂仲裁规则（如混合错误码）
[影响范围] ExitCodeResolver.resolve / handle_mixed
[相关FDR] 无
[来源标注] [DD-001:MD-010 状态机]
```

---

## FDR-M010-003 模板方法模式（ErrorFormatter）

```
[决策编号] FDR-M010-003
[决策标题] ErrorFormatter 采用模板方法模式
[决策状态] 已接受
[决策内容] ErrorFormatter 暴露 DEFAULT_TEMPLATE 常量 + format() 方法，子类可继承重写 format() 实现定制格式（如 HTML / JSON / Slack Block）
[决策理由]
  - 错误信息输出格式可能在多场景下定制（CLI/JSON Lines/HTML 报告）
  - 模板方法保留核心 3 段式生成逻辑，仅允许子类调整展示
  - 符合 DD-001 MD-010 模板方法要求
[拒绝的替代方案]
  - 方案A: 继承 + 完全重写 → 拒绝理由：失去 3 段式标准化优势
  - 方案B: 配置驱动（YAML）→ 拒绝理由：格式逻辑较复杂，配置难以表达
[影响范围] ErrorFormatter 类 + 潜在子类
[相关FDR] 无
[来源标注] [DD-001:MD-010 设计模式 - 模板方法]
```

---

## FDR-M010-004 E_SYS_001 fallback 机制

```
[决策编号] FDR-M010-004
[决策标题] 未注册错误码自动 fallback 到 E_SYS_001
[决策状态] 已接受
[决策内容] register_error / lookup_code 收到未注册错误码时，自动返回 E_SYS_001 占位记录（不抛错）
[决策理由]
  - 错误处理器自身是"最后一道防线"，不应因错误码未注册而崩溃主链
  - E_SYS_001 含完整堆栈，便于事后追溯
  - 符合 DD-001 MD-010 异常处理 E_SYS_001
[拒绝的替代方案]
  - 方案A: 抛 ValueError → 拒绝理由：违反 M-010 自身 try-except 包裹要求
  - 方案B: 返回 None → 拒绝理由：调用方难以区分"无错误"与"未注册"
[影响范围] register_error / lookup_code / ErrorCodeRegistry.lookup
[相关FDR] 无
[来源标注] [DD-001:MD-010 异常处理 E_SYS_001]
```

---

## FDR-M010-005 threading.Lock 并发保护

```
[决策编号] FDR-M010-005
[决策标题] ErrorRecorder 使用 threading.Lock 保护 records 列表
[决策状态] 已接受
[决策内容] ErrorRecorder.__init__ 构造 threading.Lock，record() 写入前 acquire / 写入后 release
[决策理由]
  - M-001~M-012 多模块并发调用 register_error，无锁会导致 records 列表损坏
  - threading.Lock 性能开销可接受（< 1μs）
  - 符合 DD-001 MD-010 并发安全要求
[拒绝的替代方案]
  - 方案A: queue.Queue → 拒绝理由：API 复杂度升高
  - 方案B: 乐观锁（CAS）→ 拒绝理由：Python list.append 非原子且无 CAS 原语
[影响范围] ErrorRecorder 类
[相关FDR] 无
[来源标注] [DD-001:MD-010 并发安全 - 全局唯一]
```

---

## FDR 状态汇总

| 决策编号 | 标题 | 状态 |
|---------|------|------|
| FDR-M010-001 | 模块级单例 | 已接受 |
| FDR-M010-002 | 状态机模式 | 已接受 |
| FDR-M010-003 | 模板方法模式 | 已接受 |
| FDR-M010-004 | E_SYS_001 fallback | 已接受 |
| FDR-M010-005 | threading.Lock 保护 | 已接受 |

---

> **本文件结束**。M-010 框架决策记录 5 条交付 DD-S 知晓。
