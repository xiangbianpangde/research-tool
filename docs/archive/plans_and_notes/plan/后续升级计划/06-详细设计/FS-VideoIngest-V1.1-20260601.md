# 文件结构规范 — VideoIngest V1.1（DD-001）

> **生成方**：DD-001
> **日期**：2026-06-01
> **模块数**：12（M-001~M-012）
> **覆盖率**：100%（12/12 通过 5/5 文件结构检查）

---

## 项目根目录

```
research-tool/                              # 项目根
├── pyproject.toml                          # PEP 621 依赖 + 锁版本（TS-001~022）
├── requirements.txt                        # 兼容 pip 直装
├── README.md                               # 用户文档
├── ruff.toml                               # Lint + 格式化配置
├── mypy.ini                                # 类型检查配置
├── pytest.ini                              # 测试配置
├── .gitignore
├── raw/                                    # 输出笔记（DS-002）
│   └── <topic>/
│       └── <video-id>.md
├── research                                # 入口脚本（DP-001）
│   #!/usr/bin/env python3
│   # from research_tool.cli import main; main()
├── research_tool/                          # 核心包
│   ├── __init__.py
│   ├── cli.py                              # M-001 CLI 入口
│   ├── preflight.py                        # M-002 预检
│   ├── downloader.py                       # M-003 下载器
│   ├── cache_manager.py                    # M-004 缓存
│   ├── transcriber.py                      # M-005 转写
│   ├── llm_client.py                       # M-006 LLM
│   ├── notes_schema.py                     # M-007 笔记组装
│   ├── pipeline_adapter.py                 # M-008 管道
│   ├── ffmpeg_wrapper.py                   # M-009 截图
│   ├── error_handler.py                    # M-010 错误
│   ├── structured_logger.py                # M-011 日志
│   ├── concurrent_orchestrator.py          # M-012 并发
│   ├── datatypes.py                        # DE-001~012 dataclass
│   ├── bilinode_partial/                   # TS-005 BiliNote 移植
│   │   └── transcriber/bcut/
│   │       ├── __init__.py
│   │       └── engine.py
│   └── tests/                              # 单元测试
│       ├── __init__.py
│       ├── conftest.py
│       ├── fixtures/
│       │   ├── argv_*.json
│       │   ├── deno_version.txt
│       │   ├── ffmpeg_version.txt
│       │   ├── short_audio_10s.wav
│       │   ├── short_video_30s.mp4
│       │   ├── front_matter.yaml
│       │   ├── sample_markdown.md
│       │   ├── transcript_sample.json
│       │   └── deepseek_response.json
│       ├── test_cli.py                     # M-001
│       ├── test_preflight.py               # M-002
│       ├── test_downloader.py              # M-003
│       ├── test_cache_manager.py           # M-004
│       ├── test_transcriber.py             # M-005
│       ├── test_llm_client.py              # M-006
│       ├── test_notes_schema.py            # M-007
│       ├── test_pipeline_adapter.py        # M-008
│       ├── test_ffmpeg_wrapper.py          # M-009
│       ├── test_error_handler.py           # M-010
│       ├── test_structured_logger.py       # M-011
│       └── test_concurrent_orchestrator.py # M-012
├── docs/                                   # 文档
│   ├── architecture.md
│   ├── deployment.md
│   ├── api.md
│   └── preflight.md                        # 4 项环境依赖说明
└── scripts/                                # 辅助脚本
    ├── check_preflight.sh                  # 预检脚本
    └── install_deps.sh                     # 依赖安装指引
```

[来源：[AR:DP §3] [AR:DP §6 部署清单] [Python 包管理最佳实践]]

---

## 模块文件结构规范（5 项检查全通过）

| 模块 | 目录层级 | 文件命名 | 文件职责 | 文件依赖 | 最佳实践 |
|------|---------|---------|---------|---------|---------|
| M-001 | 2 层 | research_tool/cli.py | CLI 入口、参数解析、调度 | 依赖 M-002/012/010/011 | ✓ |
| M-002 | 2 层 | research_tool/preflight.py | 4 项环境检查 + 60s TTL | 依赖 M-011 | ✓ |
| M-003 | 2 层 | research_tool/downloader.py | yt-dlp 包装 + 5 类 | 依赖 M-004/010/011 | ✓ |
| M-004 | 2 层 | research_tool/cache_manager.py | sqlite 双键缓存 | 依赖 M-010/011 | ✓ |
| M-005 | 2 层 | research_tool/transcriber.py | 三引擎调度 | 依赖 M-010/011 + bilinode_partial/ | ✓ |
| M-006 | 2 层 | research_tool/llm_client.py | 双模型 + 校验 | 依赖 M-010/011 | ✓ |
| M-007 | 2 层 | research_tool/notes_schema.py | 6 步 Markdown 拼装 | 依赖 M-005/006/009/010/011 | ✓ |
| M-008 | 2 层 | research_tool/pipeline_adapter.py | 落盘 + 管道触发 | 依赖 M-007/010/011 | ✓ |
| M-009 | 2 层 | research_tool/ffmpeg_wrapper.py | ffmpeg I 帧 + 压缩 | 依赖 M-010/011 | ✓ |
| M-010 | 2 层 | research_tool/error_handler.py | 错误码 + 退出码 | 依赖 M-011 | ✓ |
| M-011 | 2 层 | research_tool/structured_logger.py | JSON Lines + 切分 | 无（基础设施） | ✓ |
| M-012 | 2 层 | research_tool/concurrent_orchestrator.py | Semaphore + 资源探测 | 依赖 M-010/011 | ✓ |

---

## 文件命名规则

| 文件类型 | 命名规则 | 示例 |
|---------|---------|------|
| 模块文件 | snake_case | cache_manager.py |
| 测试文件 | test_<module>.py | test_cache_manager.py |
| Fixture | snake_case | short_audio_10s.wav |
| dataclass | PascalCase | VideoURL, LLMSummary |
| 类 | PascalCase | CacheManager |
| 函数 | snake_case | query_cache |
| 常量 | UPPER_SNAKE_CASE | DEFAULT_CONCURRENCY = 3 |
| 私有成员 | _leading_underscore | _check_deno |
| 错误码常量 | E_<CATEGORY>_<NUMBER>_<DETAIL> | E_DL_001_DENO_MISSING |

---

## 文件依赖关系图

```
research_tool/cli.py (M-001)
  ├─→ preflight.py (M-002)
  ├─→ concurrent_orchestrator.py (M-012)
  ├─→ error_handler.py (M-010)
  ├─→ structured_logger.py (M-011)
  ├─→ cache_manager.py (M-004)
  └─→ datatypes.py (DE-001~012)

concurrent_orchestrator.py (M-012)
  └─→ (动态调用 task_func)
      ├─→ downloader.py (M-003)
      │   └─→ cache_manager.py (M-004)
      ├─→ transcriber.py (M-005)
      │   └─→ bilinode_partial/ (TS-005)
      ├─→ llm_client.py (M-006)
      └─→ ffmpeg_wrapper.py (M-009)

notes_schema.py (M-007)
  ├─→ llm_client.py (M-006)
  ├─→ ffmpeg_wrapper.py (M-009)
  └─→ datatypes.py (DE-002/006/008)

pipeline_adapter.py (M-008)
  └─→ notes_schema.py (M-007)

# 横切依赖（所有模块）
M-001~M-012 → error_handler.py (M-010)  [错误码登记]
M-001~M-012 → structured_logger.py (M-011)  [日志]
```

**依赖图 DAG，无环检测通过**。

---

## [来源标注] 整体标注

- 项目根结构：[AR:DP §1 部署拓扑] [AR:DP §6 部署清单]
- 模块文件结构：[AR:DP §3 12 部署组件] [Python 3.11+ 包管理最佳实践]
- 命名规则：[AR:TS-001 Python 风格] + [soul §3.6 代码风格指南]
- 依赖关系：[AR:AC §1 协作流程图] [soul §4.15 无环检测]

---

> **本文件结束**。12 模块文件结构规范 5/5 检查全通过，符合 Python 3.11+ 最佳实践。
