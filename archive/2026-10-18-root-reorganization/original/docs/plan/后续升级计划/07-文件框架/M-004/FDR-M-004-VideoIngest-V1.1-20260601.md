# 框架决策记录 — M-004 缓存管理器（DD-M-004）

> **生成方**：DD-M-004
> **日期**：2026-06-01
> **决策数**：3（FDR-M-004-001 / 002 / 003）

---

## FDR-M-004-001 Repository 模式应用于 CacheRepository

```
[决策编号] FDR-M-004-001
[决策标题] CacheRepository 采用 Repository 模式封装 CRUD
[决策状态] 已接受
[决策内容] 将 sqlite3 CRUD 操作封装在 CacheRepository 类，外部通过 repository 实例访问
[决策理由] 1) Repository 模式隔离数据访问层与业务逻辑层
          2) 便于单元测试 mock（test_cache_manager.py 中 fixture: repository）
          3) DD-001 MD-004 已明确指定 Repository 模式 [AR:DP-004]
[拒绝的替代方案] 方案A: 在 cache_manager.py 中直接暴露 sqlite3.Connection
                拒绝理由：违反 CS-001 单一职责原则，难以 mock 测试
[影响范围] research_tool/cache_manager.py（CacheRepository 类）
          research_tool/tests/test_cache_manager.py（fixture: repository）
[相关FDR] 无
[来源标注] [DD-001:MD-004] [AR:DP-004]
```

## FDR-M-004-002 装饰器模式应用于 LockManager.with_lock

```
[决策编号] FDR-M-004-002
[决策标题] LockManager.with_lock 采用装饰器模式包装写操作
[决策状态] 已接受
[决策内容] 提供 with_lock 装饰器工厂，自动获取 asyncio.Lock + 测量 wait_ms + 记录日志
[决策理由] 1) 装饰器模式让写操作函数保持"纯"业务语义
          2) wait_ms 测量与日志记录对调用方透明
          3) DD-001 MD-004 明确指定 asyncio.Lock 装饰器模式
[拒绝的替代方案] 方案A: 在每个 write_cache 调用处手动 async with lock
                拒绝理由：代码重复、wait_ms 测量易遗漏
              方案B: 用 threading.Lock 替代 asyncio.Lock
                拒绝理由：asyncio 场景下阻塞事件循环
[影响范围] research_tool/cache_manager.py（LockManager 类 + 装饰器）
[相关FDR] 无
[来源标注] [DD-001:MD-004 设计模式:装饰器模式 asyncio.Lock] [AR:DP-004]
```

## FDR-M-004-003 测试 DB 用 :memory: 临时 sqlite

```
[决策编号] FDR-M-004-003
[决策标题] 单元测试 DB 连接采用 :memory: 内存 sqlite
[决策状态] 已接受
[决策内容] test_cache_manager.py 中所有单测使用 sqlite3.connect(":memory:") 而非文件 DB
[决策理由] 1) :memory: 无需清理临时文件
          2) 每个测试独立 DB，无污染
          3) DD-001 MD-004 测试策略明确指定 [DD-001:MD-004 Mock 策略]
[拒绝的替代方案] 方案A: 使用 tmp_path + 真实文件
                拒绝理由：速度慢、跨平台权限 0o600 测试复杂
[影响范围] research_tool/tests/test_cache_manager.py（fixture: in_memory_db）
[相关FDR] 无
[来源标注] [DD-001:MD-004]
```
