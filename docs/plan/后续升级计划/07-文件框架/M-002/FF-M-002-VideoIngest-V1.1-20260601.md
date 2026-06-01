# 文件框架结构 — M-002 预检模块（DD-M-002）

> **生成方**：DD-M-002
> **日期**：2026-06-01
> **模块**：M-002（preflight）
> **来源**：[DD-001:FS-VideoIngest-V1.1-20260601] [DD-001:MD-VideoIngest-V1.1-20260601#m-002]

---

## 文件框架

```
research_tool/                                  # 核心包根目录（[DD-001:FS]）
└── preflight.py                                # M-002 预检模块（DD-M-002 产出）
    └── [DD-M推断:路径] 产出物/07-文件框架/M-002/research_tool/preflight.py

research_tool/tests/                            # 单元测试目录（[DD-001:CS-001]）
└── test_preflight.py                           # M-002 测试文件
    └── [DD-M推断:路径] 产出物/07-文件框架/M-002/research_tool/tests/test_preflight.py
```

---

## 文件列表

| 文件路径（实际位置） | 职责 | 类数 | 函数数 | 状态 |
|------------------|------|------|--------|------|
| `产出物/07-文件框架/M-002/research_tool/preflight.py` | M-002 预检模块：4 项环境检查 + 60s TTL 缓存 | 5 | 8 | 注释完整 |
| `产出物/07-文件框架/M-002/research_tool/tests/test_preflight.py` | M-002 单元测试套件 | 7（测试类） | 30+（测试用例） | 注释完整 |

---

## 类清单

| 序号 | 类名 | 文件 | 职责 |
|------|------|------|------|
| 1 | DenoChecker | preflight.py | Deno 二进制版本检测（阻塞 YouTube） |
| 2 | NodeChecker | preflight.py | Node 二进制版本检测（FAIL_SOFT） |
| 3 | FFmpegChecker | preflight.py | FFmpeg 二进制版本检测（FAIL_SOFT） |
| 4 | WhisperModelChecker | preflight.py | Whisper medium 模型文件检测（FAIL_SOFT，降档） |
| 5 | PreflightFacade | preflight.py | 4 项 checker 聚合门面（Facade Pattern） |

---

## 函数清单

| 序号 | 函数名 | 文件 | 关联 IC | 职责 |
|------|--------|------|---------|------|
| 1 | ttl_lru_cache | preflight.py | IC-006 | TTL LRU 缓存装饰器（Decorator Pattern） |
| 2 | check_all | preflight.py | IC-006 | 模块主入口：4 项检查 + 60s 缓存 |
| 3 | _check_all_cached | preflight.py | IC-006 | 内部 @ttl_lru_cache 装饰的聚合函数 |
| 4 | _check_deno | preflight.py | IC-006 | 单项 Deno 检查（私有） |
| 5 | _check_node | preflight.py | IC-006 | 单项 Node 检查（私有） |
| 6 | _check_ffmpeg | preflight.py | IC-006 | 单项 FFmpeg 检查（私有） |
| 7 | _check_whisper | preflight.py | IC-006 | 单项 Whisper 模型检查（私有） |
| 8 | DenoChecker.check | preflight.py | IC-006 | 执行 deno 检测 |
| 9 | DenoChecker.parse_version | preflight.py | IC-006 | 解析 deno 版本号 |
| 10 | NodeChecker.check | preflight.py | IC-006 | 执行 node 检测 |
| 11 | NodeChecker.parse_version | preflight.py | IC-006 | 解析 node 版本号 |
| 12 | FFmpegChecker.check | preflight.py | IC-006 | 执行 ffmpeg 检测 |
| 13 | FFmpegChecker.parse_version | preflight.py | IC-006 | 解析 ffmpeg 版本号 |
| 14 | WhisperModelChecker.check | preflight.py | IC-006 | 执行 whisper 模型检测 |
| 15 | WhisperModelChecker.suggest_download | preflight.py | IC-006 | 生成下载建议命令 |
| 16 | PreflightFacade.check_all | preflight.py | IC-006 | 门面入口：并行 4 项检查 |
| 17 | PreflightFacade.is_blocking | preflight.py | IC-006 | 判定报告是否阻塞主任务 |
| 18 | PreflightFacade.to_dataclass | preflight.py | IC-006 | dict → PreflightReport 转换 |

---

## 文件间依赖关系

```
preflight.py
  ├─→ research_tool/datatypes.py        [DE-012 PreflightReport]
  ├─→ research_tool/structured_logger.py [M-011 get_logger, INFO/WARN/ERROR 日志]
  └─→ research_tool/error_handler.py    [M-010 register_error, 错误码登记]

test_preflight.py
  └─→ research_tool/preflight.py       [M-002 全部公开 API + 内部函数]
  └─→ research_tool/datatypes.py        [PreflightReport dataclass]

# 反向依赖（preflight 被谁调用）
research_tool/cli.py (M-001) → preflight.py     [M-001 在 CLI 入口调用 check_all]
research_tool/downloader.py (M-003) → preflight.py  [M-003 调用 _check_deno 决策 YouTube]
```

**依赖图 DAG，无环检测通过**。所有跨模块依赖均为单向调用，无循环导入风险。

---

## [DD-M 洞察]

1. **M-002 装饰器模式细节**：[DD-M推断:基于 Python functools.lru_cache 扩展 TTL 能力] — stdlib `functools.lru_cache` 不支持 TTL，需自定义 `ttl_lru_cache` 装饰器包装 `_check_all_cached`。建议实现时使用 `threading.Lock` 保护 + 模块级单例 `_cache = {"value": Any, "expires_at": float}`。
2. **M-002 与 M-003 跨模块协作**：[DD-001:MD-VideoIngest-V1.1-20260601#m-002-异常处理] 中提到 "deno 缺失会阻塞 YouTube 任务"，但实际阻塞判定逻辑在 M-001/M-003 中调用 M-002 提供的 `is_blocking()` 或直接检查 `report.deno_ok`，避免 M-002 反向依赖 M-003。
3. **M-002 测试覆盖率要求**：[DD-001:MD-VideoIngest-V1.1-20260601#m-002-测试策略] 明确 "行 ≥ 90% / 分支 ≥ 80%"，是 12 模块中第二高要求（仅低于 M-010 的 95%/90%）。建议测试用 `unittest.mock.patch` 替换 `subprocess.run` + `shutil.which` 完整覆盖 4 项 checker 的成功/边界/失败路径。

---

## [来源标注]

- 文件结构：[DD-001:FS-VideoIngest-V1.1-20260601#模块文件结构规范]
- 类设计：[DD-001:MD-VideoIngest-V1.1-20260601#m-002-类设计]
- 函数签名：[DD-001:MD-VideoIngest-V1.1-20260601#m-002-函数签名]
- 依赖关系：[DD-001:FS-VideoIngest-V1.1-20260601#文件依赖关系图]
- 装饰器/门面模式：[DD-001:MD-VideoIngest-V1.1-20260601#m-002-洞察#1-lru_cache-ttl-60s]
- 测试策略：[DD-001:MD-VideoIngest-V1.1-20260601#m-002-测试策略]
- 数据类：[DD-001:DS-VideoIngest-V1.1-20260601#de-012-preflightreport-dataclass]

---

> **本文件结束**。M-002 文件框架结构 + 类清单 + 函数清单 + 依赖关系 + 3 条 DD-M 洞察，100% 来自 DD-001 上游规范。
