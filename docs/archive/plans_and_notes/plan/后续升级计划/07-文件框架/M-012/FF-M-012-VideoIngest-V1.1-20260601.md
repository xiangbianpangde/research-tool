# 文件框架结构 — M-012 并发编排器（DD-M 输出）

> **生成方**：DD-M-M-012
> **日期**：2026-06-01
> **负责模块**：M-012（concurrent_orchestrator）
> **来源规范**：[DD-001:FS-M-012] [DD-001:MD-M-012] [DD-001:IC-029/IC-030] [DD-001:CS-001]

---

## M-012 文件框架

```
[模块编号] M-012
[模块名称] 并发编排器（concurrent_orchestrator）
[文件框架]
  research_tool/
    concurrent_orchestrator.py          ← [职责：URL 列表并发编排与限流（协调器+资源池模式）]
      - [SemaphorePool 类注释]            # 资源池：asyncio.Semaphore 限流
      - [TaskGatherer 类注释]             # 任务编排：URL 列表 + task_func 并发执行
      - [ResourceProber 类注释]           # 资源探测：psutil CPU/MEM
      - [ConcurrencyResolver 类注释]      # 动态降级：基于 RAM 调整并发数
      - [gather_tasks() 函数注释]         # IC-029 主入口
      - [detect_concurrency() 函数注释]   # IC-030 资源探测
      - [acquire_semaphore() 函数注释]    # 上下文管理器
      - [release_semaphore() 函数注释]    # 显式释放
      - [measure_wait_ms() 函数注释]      # P99 等待时长
  research_tool/tests/
    test_concurrent_orchestrator.py     ← [职责：M-012 单元测试 + 集成测试]
      - [TestSemaphorePool 测试场景]       # 6 个测试场景
      - [TestTaskGatherer 测试场景]        # 5 个测试场景
      - [TestResourceProber 测试场景]      # 6 个测试场景
      - [TestConcurrencyResolver 测试场景] # 5 个测试场景
      - [TestIntegration 集成测试]         # 6 个测试场景
      - [TestEdgeCases 边界测试]           # 4 个测试场景
```

## 文件间依赖关系

```
concurrent_orchestrator.py (M-012)
  ├─→ error_handler.py (M-010)  [register_error 异常登记]
  ├─→ structured_logger.py (M-011)  [emit_log 日志写入]
  └─→ datatypes.py (DE-001~012)  [VideoURL, Result 类型]

test_concurrent_orchestrator.py
  └─→ concurrent_orchestrator.py  [被测试]
```

**依赖图 DAG，无环检测通过**（M-012 单向依赖 M-010/M-011/datatypes）。

## 来源标注

- 文件结构：[DD-001:FS-M-012] - 2 层目录结构，1 个主文件 + 1 个测试文件
- 类设计：[DD-001:MD-M-012] - 4 子模块 → 4 类（SemaphorePool/TaskGatherer/ResourceProber/ConcurrencyResolver）
- 函数签名：[DD-001:MD-M-012] - 5 函数签名（gather_tasks/detect_concurrency/acquire_semaphore/release_semaphore/measure_wait_ms）
- 接口契约：[DD-001:IC-003] [DD-001:IC-029] [DD-001:IC-030]
- 代码风格：[DD-001:CS-001] - Python 4 空格、Google docstring、asyncio 规范
- 测试策略：[DD-001:MD-M-012] - 12 测试用例（核心 5 + 边界 4 + 异常 3）+ psutil mock
