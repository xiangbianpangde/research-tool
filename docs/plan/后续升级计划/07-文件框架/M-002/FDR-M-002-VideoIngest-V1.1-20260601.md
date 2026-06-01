# 框架决策记录 — M-002 预检模块（DD-M-002）

> **生成方**：DD-M-002
> **日期**：2026-06-01
> **模块**：M-002（preflight）

---

## FDR-M002-001 装饰器模式实现 lru_cache TTL 60s

```
[决策编号] FDR-M002-001
[决策标题] 自定义 ttl_lru_cache 装饰器实现 60s TTL
[决策状态] 已接受
[决策内容] 由于 Python stdlib functools.lru_cache 不支持 TTL 过期，DD-M-002 自定义 ttl_lru_cache(ttl_seconds=60) 装饰器，包装 _check_all_cached() 实现 60s 缓存
[决策理由]
  - 满足 [DD-001:IC-006 幂等性 lru_cache TTL 60s] 要求
  - stdlib functools.lru_cache(maxsize=1) 只能控制缓存容量，无法控制时间
  - 自定义实现可避免引入 cachetools 等外部依赖（保持 [DD-001:TS-001 Python stdlib] 原则）
  - 使用 threading.Lock 保护并发安全
[拒绝的替代方案]
  方案A: 引入 cachetools.TTLCache
    拒绝理由: 违反 [DD-001:TS-001 Python stdlib] 原则，增加 pyproject.toml 依赖
  方案B: 用 functools.lru_cache + 手动时间戳检查
    拒绝理由: 调用方需在外部维护时间戳，无法实现装饰器透明性
[影响范围] preflight.py _check_all_cached 函数、check_all 公开入口
[相关FDR] FDR-M002-003
[来源标注] [DD-001:MD-VideoIngest-V1.1-20260601#m-002-洞察#1-lru_cache-ttl-60s] [DD-M推断:基于 functools.lru_cache 局限性]
```

---

## FDR-M002-002 门面模式 + 4 个 Checker 类分离

```
[决策编号] FDR-M002-002
[决策标题] 采用 Facade Pattern 聚合 4 个独立 Checker 类
[决策状态] 已接受
[决策内容] 创建 4 个独立 Checker 类（DenoChecker / NodeChecker / FFmpegChecker / WhisperModelChecker），由 PreflightFacade 聚合对外暴露统一入口
[决策理由]
  - [DD-001:MD-VideoIngest-V1.1-20260601#m-002-类设计] 明确要求 5 个类（4 checker + 1 report）
  - 单个 Checker 类职责单一（SRP），便于单元测试和扩展
  - 失败/降级策略不同（FAIL_BLOCKING vs FAIL_SOFT）需要独立类处理
  - 便于未来 V2.0 增加第 5 项检查（如 yt-dlp 版本校验）
[拒绝的替代方案]
  方案A: 单个 PreflightChecker 类内含 4 个方法
    拒绝理由: 违反 [DD-001:MD] 中 5 类设计；混合 4 种不同降级策略使类过大（违反 R24 文件职责模糊）
  方案B: 函数式编程（4 个独立函数 + check_all 聚合）
    拒绝理由: 与 [DD-001:MD-VideoIngest-V1.1-20260601#m-002-类设计] 5 个类不符
[影响范围] preflight.py 5 个类的组织结构
[相关FDR] FDR-M002-001
[来源标注] [DD-001:MD-VideoIngest-V1.1-20260601#m-002-类设计] [DD-M推断:基于单一职责原则]
```

---

## FDR-M002-003 check_all 公开入口的缓存代理策略

```
[决策编号] FDR-M002-003
[决策标题] check_all() 内部直接调用 _check_all_cached() 享受 60s 缓存
[决策状态] 已接受
[决策内容] 公开 API check_all() 是薄包装，内部调用 @ttl_lru_cache 装饰的 _check_all_cached()
[决策理由]
  - 满足 [DD-001:IC-006] 中 "重复调用 < 60s 直接返回缓存" 要求
  - 公开 API 与装饰器分离，便于：
    1. 单元测试时禁用缓存（直接测试 _check_all_cached）
    2. 未来增加缓存失效的强制刷新路径（如 cache_invalidate()）
  - 保持 [DD-001:CS-001 命名规范]，私有函数用 _ 前缀
[拒绝的替代方案]
  方案A: 直接在 check_all 上加 @ttl_lru_cache
    拒绝理由: 失去测试灵活性；测试时无法绕过缓存验证 4 项检查逻辑
  方案B: 用 @lru_cache(maxsize=1) + 模块级 _cache_timestamp
    拒绝理由: 实现复杂，不如自定义装饰器清晰
[影响范围] check_all / _check_all_cached 两个函数
[相关FDR] FDR-M002-001
[来源标注] [DD-001:IC-006 幂等性] [DD-M推断:基于可测试性原则]
```

---

## FDR-M002-004 4 项检查并行执行策略（asyncio.gather）

```
[决策编号] FDR-M002-004
[决策标题] PreflightFacade.check_all 使用 asyncio.gather 并行 4 项检查
[决策状态] 已接受
[决策内容] PreflightFacade.check_all 是 async def，4 个 checker.check() 内部封装为 asyncio.to_thread 调用，再用 asyncio.gather 并行
[决策理由]
  - 满足 [DD-001:IC-006 性能约束] "首次 < 2s" 要求（4 个 subprocess 串行约 4s，并行约 1s）
  - 满足 [DD-001:CS-001 异步规范] "CPU 密集用 asyncio.to_thread"
  - gather(return_exceptions=True) 隔离各子任务异常，避免单点失败阻塞
[拒绝的替代方案]
  方案A: 串行执行 4 个 checker
    拒绝理由: 性能不达标（4s 超出 IC-006 性能约束）
  方案B: 进程池 multiprocessing.Pool
    拒绝理由: 4 项检查 1s 级别，并行开销（进程创建/序列化）反而更慢
[影响范围] PreflightFacade.check_all 实现
[相关FDR] -
[来源标注] [DD-001:IC-006 性能约束-首次<2s] [DD-001:CS-VideoIngest-V1.1-20260601#cs-001-异步规范]
```

---

## FDR-M002-005 错误码常量化（CS-001 命名规范）

```
[决策编号] FDR-M002-005
[决策标题] 4 个错误码（E_DL_001_DENO_MISSING + 3 个 SIM-STUB）定义为模块级常量
[决策状态] 已接受
[决策内容] 在 preflight.py 顶部定义 4 个 UPPER_SNAKE_CASE 常量，避免字符串硬编码
[决策理由]
  - 满足 [DD-001:CS-001 命名规范] "错误码常量 E_<CATEGORY>_<NUMBER>_<DETAIL>"
  - 便于 M-010 错误码字典统一管理 + M-011 日志统一引用
  - 避免拼写错误导致错误码与 M-010 registry 不匹配
[拒绝的替代方案]
  方案A: 错误码硬编码在函数内
    拒绝理由: 易出错，难统一管理，违反 [DD-001:CS-001 命名规范]
  方案B: 错误码集中到独立文件 error_codes.py
    拒绝理由: V1.1 阶段过度设计（YAGNI），后续如需统一可在 M-010 registry 中集中
[影响范围] preflight.py 全部 4 个错误码引用点
[相关FDR] -
[来源标注] [DD-001:CS-VideoIngest-V1.1-20260601#cs-001-命名规范] [DD-001:EX-VideoIngest-V1.1-20260601#ex-001-错误码]
```

---

## [来源标注]

- 全部 5 个 FDR 均基于 [DD-001] 上游规范 + [DD-M推断] 推断决策
- 与 M-001/M-003 协调：[DD-001:MD-VideoIngest-V1.1-20260601#m-002-异常处理] 提及 deno 缺失阻塞 YouTube 的具体执行点在 M-001/M-003

---

> **本文件结束**。5 个 FDR 全部已接受，覆盖装饰器/门面/缓存代理/并行执行/错误码常量化 5 个关键设计决策。
