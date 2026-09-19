# 文件框架结构 — M-011 结构化日志器（DD-M-011）

> **生成方**：DD-M-011
> **负责模块**：M-011 结构化日志器（structured_logger）
> **日期**：2026-06-01
> **上游模块细化方案**：[MD-VideoIngest-V1.1-20260601.md#m-011-结构化日志器]
> **设计模式**：装饰器模式（JsonFormatter）+ 模板方法（按日切分）
> **关联接口契约**：IC-028（API-028 日志写入）

---

## [模块编号] M-011

## [模块名称] 结构化日志器（structured_logger）

## [文件框架]

```
research_tool/
├── structured_logger.py                ← [职责：M-011 主体，含 4 类 + 5 函数签名 + 敏感过滤 + 切分降级]
│   ├── 文件头注释                      ← [soul 3.2 模板：职责/功能/输入输出/依赖/注意事项/来源]
│   ├── 模块 docstring                  ← [AR:API-028 来源]
│   ├── 常量定义段                       ← [DEFAULT_LOG_DIR / DEFAULT_RETENTION_DAYS / SENSITIVE_KEYS / LOG_LEVEL_MAP]
│   ├── 类 JsonFormatter                ← [soul 3.3 类注释：装饰器模式 - LogRecord → JSON Lines]
│   ├── 类 DailyRotatingHandler         ← [soul 3.3 类注释：模板方法 - 按日切分 + 30 天保留]
│   ├── 类 SensitiveFilter              ← [soul 3.3 类注释：黑名单过滤 - API_KEY/COOKIE/PROMPT 字段 redact]
│   ├── 类 UrlHasher                    ← [soul 3.3 类注释：URL → sha256(url) 替换]
│   ├── 函数 configure_logging          ← [IC-028 入口 - 注册 Formatter/Handler/Filter]
│   ├── 函数 emit_log                   ← [IC-028 写入入口 - JSON Lines append]
│   ├── 函数 filter_sensitive           ← [敏感字段 redact 入口]
│   ├── 函数 hash_url                   ← [URL 哈希入口]
│   └── 函数 rotate_daily               ← [按日切分入口 - 关闭旧文件/打开新文件]
└── tests/
    └── test_structured_logger.py        ← [职责：M-011 单元测试，12 用例（核心 5 + 边界 4 + 异常 3）]
        ├── 文件头注释                  ← [soul 3.2 模板：测试场景/断言/Mock 策略]
        ├── test_json_formatter_*       ← [3 用例：基础字段 / 异常字段 / ensure_ascii]
        ├── test_daily_rotating_*       ← [3 用例：同日不切分 / 跨日切分 / 30 天清理]
        ├── test_sensitive_filter_*     ← [3 用例：API_KEY 屏蔽 / COOKIE 屏蔽 / 嵌套 dict]
        ├── test_url_hasher_*           ← [2 用例：URL 哈希 / 嵌套 dict 替换]
        └── test_configure_emit_*       ← [1 用例：端到端 emit_log + 路径不可写降级 stderr]
```

## [文件间依赖关系]

```
research_tool/structured_logger.py
  ├─→ Python stdlib (logging, json, hashlib, datetime, pathlib, sys)
  ├─→ Python stdlib (concurrent.futures, threading)  [DD-M推断:依据 logging.Handler 内置锁保证并发安全]
  └─→ 无（基础设施模块 - 横切依赖的反向锚点）

# 横切依赖（所有模块 → M-011）
structured_logger.py ← cli.py (M-001)                [IC-001 启动时 configure_logging]
structured_logger.py ← preflight.py (M-002)          [IC-006 检测结果日志]
structured_logger.py ← downloader.py (M-003)         [IC-007 下载 start/end 日志]
structured_logger.py ← cache_manager.py (M-004)      [IC-009/010 hit/miss + wait_ms 日志]
structured_logger.py ← transcriber.py (M-005)        [IC-012 引擎选择/降级日志]
structured_logger.py ← llm_client.py (M-006)         [IC-013/014 模型/重试日志]
structured_logger.py ← notes_schema.py (M-007)       [IC-016~020 各步骤日志]
structured_logger.py ← pipeline_adapter.py (M-008)   [IC-022/023 落盘/管道日志]
structured_logger.py ← ffmpeg_wrapper.py (M-009)     [IC-025 截图日志]
structured_logger.py ← error_handler.py (M-010)      [IC-026/027 错误码日志]
structured_logger.py ← concurrent_orchestrator.py (M-012) [IC-029/030 并发/资源日志]
```

**[来源标注]** [DD-001:FS-VideoIngest-V1.1-20260601.md#模块文件结构规范] [DD-001:MD-VideoIngest-V1.1-20260601.md#m-011-结构化日志器] [DD-M推断:依据 M-011 基础设施无依赖 + 装饰器/模板方法模式一致性]

---

## [单文件职责说明]

| 文件 | 职责 | 内部组成 | 行数预估 |
|------|------|---------|---------|
| structured_logger.py | 4 子模块整合，4 类 + 5 函数签名 + 装饰器/模板方法实现 | 4 类 + 5 函数 + 4 常量 | ~260 行 |
| test_structured_logger.py | 12 用例覆盖（核心 5 + 边界 4 + 异常 3） | 12 测试函数 | ~230 行 |

**[合规检查]** 单文件函数数 ≤ 20 ✓；单模块文件数 = 2（在 5-10 范围）✓；单文件职责单一 ✓

---

## [DD-M 洞察注入 — 框架隐患与实现风险]

1. **【跨模块依赖反向】M-011 是唯一的"被依赖方"，不依赖任何业务模块** — 这与 M-001~M-010 全部依赖 M-011 的横切关系形成对照。结构设计师 DD-S 需特别注意：M-011 的 import 块只能引用 stdlib，禁止 import 任何 `research_tool.*` 子模块，否则会形成循环导入（C1~C-1↔C1）。[DD-M推断:依据 FS 中"依赖关系无环检测通过" + M-011 无业务依赖]

2. **【JsonFormatter 字段顺序】** JSON Lines 解析对字段顺序不敏感，但下游日志聚合（ELK/Loki）通常按字段建立索引。建议固定字段顺序为 `ts → level → module → task_id → url_sha256 → step → duration_ms → code → msg → *kwargs`，便于 grep 与 jq 链式查询。[DD-M推断:依据 IC-028 入参顺序 + 日志聚合工具普遍按字段顺序建索引]

3. **【按日切分时钟源】** DailyRotatingHandler 切分判断基于 `datetime.now().date()`，但跨时区运行（如 UTC 服务器 + 本地时区用户）时可能产生重复/丢失。建议在 handler 初始化时显式固定时区 `timezone.utc`（V1.1 默认），并通过环境变量 `LOG_TZ` 覆盖。[DD-M推断:依据 AR:BR-016 30 天保留 + 跨时区部署场景]

4. **【敏感字段嵌套深度】** SensitiveFilter 默认 redact 一级 dict 字段（API_KEY/COOKIE/PROMPT），但 dict 可能嵌套（如 `kwargs={"headers": {"Cookie": "..."}}`）。建议在 filter 内递归 redact 嵌套结构，限制最大深度 5 防止栈溢出。[DD-M推断:依据 M-001 argv 解析可能含 Cookie 嵌套 + Python 递归深度限制 1000]

5. **【log_dir 权限】** IC-028 前置条件要求"日志目录 0o700"，但 M-011 当前未在 `configure_logging` 内自动 chmod。需在第一次写入前 `os.chmod(log_dir, 0o700)`，否则违反契约前置条件。[DD-M推断:依据 IC-028 前置条件 + SEC 安全规范要求]

---

## [模块边界守护 — 策略16]

- 本 DD-M-011 仅创建/修改 M-011 内的文件（structured_logger.py + test_structured_logger.py）
- 跨模块文件操作数 = 0（D7=100）
- 跨模块依赖通过文件头注释标注，但不直接操作其他模块文件
- 所有 import 限定为 stdlib，禁止 `from research_tool.xxx import ...` 以避免循环导入

---

> **本文件结束**。M-011 文件框架结构已定义，交付 DD-S 进入骨架搭建。
