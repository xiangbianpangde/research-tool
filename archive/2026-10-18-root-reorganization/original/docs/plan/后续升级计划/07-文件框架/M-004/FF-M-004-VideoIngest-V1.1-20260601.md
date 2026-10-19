# 文件框架结构 — M-004 缓存管理器（DD-M-004）

> **生成方**：DD-M-004
> **负责模块**：M-004 缓存管理器（cache_manager）
> **日期**：2026-06-01
> **上游模块细化方案**：[MD-VideoIngest-V1.1-20260601.md#m-004-缓存管理器]
> **设计模式**：Repository 模式 + 装饰器模式（asyncio.Lock）
> **关联接口契约**：IC-009 / IC-010 / IC-011

---

## [模块编号] M-004

## [模块名称] 缓存管理器（cache_manager）

## [文件框架]

```
research_tool/
├── cache_manager.py               ← [职责：M-004 主体，含 5 类 + 5 函数签名 + 状态机 + 异常降级]
│   ├── 文件头注释                  ← [soul 3.2 模板：职责/功能/输入输出/依赖/注意事项/来源]
│   ├── 模块 docstring               ← [AR:API-009/010/011]
│   ├── 类 DBConnection              ← [soul 3.3 类注释：属性/方法/状态机/异常]
│   ├── 类 CacheRepository           ← [Repository 模式：CacheEntry 增删改查]
│   ├── 类 HashCalculator            ← [双键计算：sha256(url) + etag/last_modified]
│   ├── 类 TTLCleaner                ← [后台清理：默认 30 天 / YouTube 24h]
│   ├── 类 LockManager               ← [asyncio.Lock 装饰器包装]
│   ├── 函数 query_cache             ← [IC-009 入口]
│   ├── 函数 write_cache             ← [IC-010 入口]
│   ├── 函数 cleanup_expired         ← [IC-011 入口]
│   ├── 函数 compute_url_sha256      ← [DD-M推断:哈希工具函数]
│   └── 函数 acquire_write_lock      ← [DD-M推断:锁上下文管理器入口]
└── tests/
    └── test_cache_manager.py        ← [职责：M-004 单元测试，12 用例（核心 5 + 边界 4 + 异常 3）]
        ├── 文件头注释                ← [soul 3.2 模板：测试场景/断言/Mock 策略]
        ├── test_hash_calculator_*    ← [5 用例：双键计算 / 边界 / 异常]
        ├── test_repository_crud_*    ← [4 用例：get/put/delete/cleanup]
        └── test_lock_manager_*       ← [3 用例：并发写串行化 / wait 测量 / 异常]
```

## [文件间依赖关系]

```
research_tool/cache_manager.py
  ├─→ research_tool/datatypes.py       [DE-009 CacheEntry / DE-003 VideoURL]
  ├─→ research_tool/error_handler.py   [register_error]
  └─→ research_tool/structured_logger.py [emit_log 缓存命中/锁等待]

# 横切依赖（所有模块）
cache_manager.py → error_handler.py (M-010)  [错误码登记]
cache_manager.py → structured_logger.py (M-011)  [JSON Lines 日志]

# 被依赖
cache_manager.py ← downloader.py (M-003)  [IC-007 下载先 query_cache]
cache_manager.py ← llm_client.py (M-006)  [IC-013 总结前可能先查缓存]
cache_manager.py ← cli.py (M-001)         [IC-001 启动时 cache 初始化]
```

**[来源标注]** [DD-001:FS-VideoIngest-V1.1-20260601.md#模块文件结构规范] [DD-001:MD-VideoIngest-V1.1-20260601.md#m-004-缓存管理器] [DD-M推断:依据 M-004 单一职责 + Repository 模式一致性]

---

## [单文件职责说明]

| 文件 | 职责 | 内部组成 | 行数预估 |
|------|------|---------|---------|
| cache_manager.py | 5 子模块整合，5 类 + 5 函数签名 + 状态机实现 | 5 类 + 5 函数 | ~280 行 |
| test_cache_manager.py | 12 用例覆盖（核心 5 + 边界 4 + 异常 3） | 12 测试函数 | ~220 行 |

**[合规检查]** 单文件函数数 ≤ 20 ✓；单模块文件数 = 2（在 5-10 范围）✓

---

## [模块边界守护 — 策略16]

- 本 DD-M-004 仅创建/修改 M-004 内的文件（cache_manager.py + test_cache_manager.py）
- 跨模块文件操作数 = 0（D7=100）
- 跨模块依赖通过文件头注释标注，但不直接操作其他模块文件

---

> **本文件结束**。M-004 文件框架结构已定义，交付 DD-S 进入骨架搭建。
