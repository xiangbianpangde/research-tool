# 框架决策记录 — M-012 并发编排器（DD-M 输出）

> **生成方**：DD-M-M-012
> **日期**：2026-06-01
> **负责模块**：M-012（concurrent_orchestrator）
> **决策数**：5（FDR-M-012-001 ~ FDR-M-012-005）

---

## FDR-M-012-001 单文件组织（不拆分模块）

```
[决策编号] FDR-M-012-001
[决策标题] M-012 保持单文件 concurrent_orchestrator.py
[决策状态] 已接受
[决策内容] 4 类 + 5 函数全部组织在单文件 concurrent_orchestrator.py，不拆分为子模块
[决策理由]
  1. DD-001 FS-M-012 明确指定单文件结构（research_tool/concurrent_orchestrator.py）
  2. 4 类之间高度内聚（SemaphorePool → TaskGatherer → ConcurrencyResolver → ResourceProber）
  3. 单文件函数总数 9（含 __init__）< 20 上限（soul 4.2）
  4. 拆分反而引入跨文件导入开销和循环依赖风险
[拒绝的替代方案]
  方案B: 拆分为 semaphore_pool.py / task_gatherer.py / resource_prober.py / concurrency_resolver.py
  拒绝理由: 增加 4 个文件，跨文件依赖 4 条，违反 soul R14（禁止过度拆分），且与 DD-001 FS 冲突
[影响范围] M-012 全部产出物
[相关FDR] FDR-M-012-002（类内组织）
[来源标注] [DD-001:FS-M-012] [soul §4.2 单文件函数数上限]
```

## FDR-M-012-002 类内组织顺序

```
[决策编号] FDR-M-012-002
[决策标题] 类按"基础设施→核心→探测→决策"顺序排列
[决策状态] 已接受
[决策内容] SemaphorePool → TaskGatherer → ResourceProber → ConcurrencyResolver
[决策理由]
  1. SemaphorePool 是基础设施（被 TaskGatherer 引用）
  2. TaskGatherer 是核心编排（引用 SemaphorePool + ConcurrencyResolver）
  3. ResourceProber 是探测工具（被 ConcurrencyResolver 引用）
  4. ConcurrencyResolver 是决策器（引用 ResourceProber）
  5. 按依赖顺序便于阅读和 IDE 跳转
[拒绝的替代方案]
  方案B: 按字母顺序 ConcurrencyResolver → ResourceProber → SemaphorePool → TaskGatherer
  拒绝理由: 字母顺序破坏依赖关系阅读体验，增加理解成本
[影响范围] concurrent_orchestrator.py 类定义顺序
[相关FDR] FDR-M-012-001
[来源标注] [DD-M推断:基于依赖顺序的可读性考量]
```

## FDR-M-012-003 全局资源池懒加载单例

```
[决策编号] FDR-M-012-003
[决策标题] 全局 SemaphorePool/ConcurrencyResolver 使用懒加载单例
[决策状态] 已接受
[决策内容] 模块级 _global_semaphore_pool / _global_concurrency_resolver 在首次 gather_tasks 调用时初始化
[决策理由]
  1. 避免 import 时副作用（psutil 调用仅在需要时发生）
  2. 单例模式避免重复创建资源池（保证 Semaphore 计数连续性）
  3. 懒加载符合 asyncio 异步上下文（gather 是异步入口）
  4. 测试时可显式 _initialize_orchestrator() 注入 mock
[拒绝的替代方案]
  方案B: 模块加载时立即初始化（eager init）
  拒绝理由: 模块导入时 psutil 调用可能失败，影响整个模块可导入性
  方案C: 每次调用新建实例
  拒绝理由: 破坏 Semaphore 计数连续性，无法实现跨调用限流
[影响范围] _global_semaphore_pool / _global_concurrency_resolver 全局变量
[相关FDR] FDR-M-012-004
[来源标注] [DD-M推断:基于 psutil 资源探测的副作用隔离需求]
```

## FDR-M-012-004 psutil 不可用时优雅降级

```
[决策编号] FDR-M-012-004
[决策标题] psutil 缺失/异常时返回保守默认值（不抛错）
[决策状态] 已接受
[决策内容] ResourceProber.__init__ 检测 psutil 可用性；detect_cpu/detect_ram 异常时返回 1 / 8.0
[决策理由]
  1. DD-001 MD-M-012 明确：资源探测失败 → 保持默认 3 并发（不抛错）
  2. psutil 是第三方依赖，可能在某些环境（如最小化 Docker）缺失
  3. 保守默认值（8GB）保证不触发降级，维持主流程功能
  4. 异常路径不会影响 CI 测试（test_psutil_missing_returns_defaults）
[拒绝的替代方案]
  方案B: psutil 缺失时直接抛 ImportError
  拒绝理由: 违反 DD-001 MD-M-012 异常处理策略（"保持默认 3 并发"）
  方案C: 强制依赖 psutil（pyproject.toml 硬约束）
  拒绝理由: 减少环境兼容性，与 DD-001 IC-030 "psutil >= 5.9"软约束冲突
[影响范围] ResourceProber 全部方法
[相关FDR] FDR-M-012-003
[来源标注] [DD-001:MD-M-012 异常处理] [DD-001:IC-030 前置条件 psutil >= 5.9]
```

## FDR-M-012-005 测试场景注释采用"3 字段"标准格式

```
[决策编号] FDR-M-012-005
[决策标题] 测试场景注释采用"测试场景+断言+Mock"三字段格式
[决策状态] 已接受
[决策内容] 每个测试方法注释包含 3 个字段：测试场景（做什么）、断言（验证什么）、Mock（依赖什么）
[决策理由]
  1. DD-001 MD-M-012 测试策略要求 Mock 策略明确
  2. 3 字段格式便于 DD-S（结构设计师）实现测试代码时直接照搬
  3. 统一格式便于自评审清单 4.9 测试文件注释完整项检查
  4. 覆盖率统计可按测试场景归类（核心/边界/异常）
[拒绝的替代方案]
  方案B: 仅写"测试场景"单字段
  拒绝理由: 缺少 Mock 信息，DD-S 实现时需反复查阅 MD
  方案C: 5+ 字段（含性能/前置/后置）
  拒绝理由: 过度细化，违反 soul R14（禁止过度拆分）
[影响范围] test_concurrent_orchestrator.py 全部 32 个测试方法注释
[相关FDR] 无
[来源标注] [DD-M推断:基于 soul §3.6 测试场景注释模板 + DD-001:MD-M-012 测试策略]
```
