# 代码风格指南 — VideoIngest V1.1（DD-001）

> **生成方**：DD-001
> **日期**：2026-06-01
> **技术栈**：Python 3.11+ (主) + Shell (辅助脚本) + YAML/TOML (配置)
> **覆盖率**：100%（3/3 技术栈 + 3 个自动化工具配置）

---

## CS-001 Python 代码风格

### 命名规范

| 元素 | 规则 | 示例 |
|------|------|------|
| 类名 | PascalCase | `CacheManager`, `LLMSummary` |
| 函数名 | snake_case | `query_cache`, `transcribe` |
| 方法名 | snake_case | `acquire_semaphore` |
| 变量名 | snake_case | `url_sha256`, `input_tokens` |
| 常量名 | UPPER_SNAKE_CASE | `DEFAULT_CONCURRENCY = 3`, `MAX_URL_COUNT = 10` |
| 私有成员 | _leading_underscore | `_check_deno`, `_internal_state` |
| 错误码常量 | E_<CATEGORY>_<NUMBER>_<DETAIL> | `E_DL_001_DENO_MISSING` |
| dataclass 字段 | snake_case | `url_sha256`, `video_id` |
| 模块名 | snake_case | `cache_manager`, `notes_schema` |
| 包名 | snake_case（小写） | `research_tool`, `bilinode_partial` |

### 格式规范

| 项 | 规则 |
|---|------|
| 缩进 | 4 空格（不用 Tab） |
| 行宽 | 120 字符（软上限 100） |
| 换行符 | LF（Unix 风格） |
| 文件编码 | UTF-8（无 BOM） |
| 引号 | 双引号 `"` 优先（docstring 用 `"""`） |
| import 顺序 | 标准库 → 第三方 → 本地（空行分隔） |
| 空行 | 顶层 2 空行 / 方法间 1 空行 |
| 行尾 | 1 个换行符 |

### 注释规范

| 类型 | 规则 |
|------|------|
| 文件头注释 | 简短描述（1-2 行） |
| 模块 docstring | `"""模块职责。\n\n[AR:API-NNN 来源]\n"""` |
| 类 docstring | `"""类职责。\n\n属性:\n    attr1: 描述\n方法:\n    method1: 描述\n"""` |
| 函数 docstring | `"""函数功能。\n\n参数:\n    arg1: 描述\n返回:\n    描述\n异常:\n    ErrorCode: 触发条件\n"""` |
| 行内注释 | 解释"为什么"而非"做什么" |
| TODO 注释 | `# TODO(author): 描述` |

### 导入规范

```python
# 1. 标准库
import asyncio
import sqlite3
from pathlib import Path

# 2. 第三方
import httpx
import yaml

# 3. 本地
from research_tool.datatypes import VideoURL, LLMSummary
from research_tool.error_handler import register_error
```

**禁止**：
- 循环导入（cycle import）
- 通配符导入（`from xxx import *`）
- 隐式重新导入（同名覆盖）

### 类型注解规范

```python
# 必须：所有函数签名
def query_cache(url: VideoURL, etag: str = "") -> Optional[CacheEntry]:
    ...

# 必须：所有类属性
class CacheManager:
    db_path: Path
    lock: asyncio.Lock
    ttl_days: int = 30

# 推荐：使用 typing 模块
from typing import Optional, List, Dict
from collections.abc import Callable, Awaitable
```

**禁止**：
- `Any`（除特殊场景）
- `dict` / `list` 作返回值（用 `Dict` / `List`）

### 异常处理规范

```python
# 1. 捕获粒度：尽量精确
try:
    result = sqlite3.connect(db_path)
except sqlite3.OperationalError as e:
    register_error("E_CK_001", e, task_id=task_id)
    return None  # 降级

# 2. 日志记录：必须记录
except Exception as e:
    logger.error(f"Unexpected: {e}", exc_info=True)
    raise

# 3. 异常转换：领域异常 → 错误码
except yt_dlp.utils.DownloadError as e:
    raise DownloadError(code="E_DL_001", message=str(e)) from e
```

**规则**：
- 禁止裸 `except:`（必须指定异常类型）
- 禁止 `except Exception: pass`（必须至少 log）
- 异常 → 错误码登记 → 降级或上抛

### 异步规范

```python
# 1. async def 仅用于 IO 密集
async def download_video(url: VideoURL) -> DownloadTask:
    ...

# 2. CPU 密集用 asyncio.to_thread
async def transcribe(audio_path: str) -> Transcript:
    return await asyncio.to_thread(_sync_transcribe, audio_path)

# 3. 资源用 async with
async with semaphore:
    result = await task_func(url)

# 4. 异常隔离用 gather(..., return_exceptions=True)
results = await asyncio.gather(*tasks, return_exceptions=True)
```

### 测试规范

| 项 | 规则 |
|---|------|
| 测试文件命名 | `test_<module>.py` |
| 测试函数命名 | `test_<func>_<scenario>` |
| Fixture 命名 | snake_case（如 `sample_audio`） |
| 覆盖率要求 | 行 ≥ 80%，分支 ≥ 70%（核心模块 ≥ 90%） |
| Mock 策略 | `unittest.mock.patch` / `pytest-mock` |
| 异步测试 | `pytest-asyncio` + `@pytest.mark.asyncio` |
| 集成测试 | `tests/integration/` 子目录 |
| 测试数据 | `tests/fixtures/` 目录 |

---

## CS-002 Shell 脚本风格

```bash
#!/usr/bin/env bash
set -euo pipefail

# 变量：UPPER_SNAKE_CASE
INSTALL_DIR="/usr/local/bin"

# 函数：snake_case
check_dependency() {
  local cmd=$1
  if ! command -v "$cmd" &> /dev/null; then
    echo "Error: $cmd not found"
    return 1
  fi
}

# 错误处理
trap 'echo "Failed at line $LINENO"; exit 1' ERR

# 主流程
main() {
  check_dependency "deno"
  check_dependency "ffmpeg"
  echo "All dependencies OK"
}

main "$@"
```

**规则**：
- 顶部 `set -euo pipefail`
- 函数小写 + 局部变量 `local`
- 错误时 `trap ... ERR`
- 缩进 2 空格

---

## CS-003 YAML/TOML 配置风格

```yaml
# YAML: 2 空格缩进，UTF-8
project:
  name: "VideoIngest"
  version: "1.1.0"

dependencies:
  - name: "yt-dlp"
    version: ">=2023.7.6"
```

```toml
# TOML（pyproject.toml）
[project]
name = "research-tool"
version = "1.1.0"
requires-python = ">=3.11"

dependencies = [
  "yt-dlp>=2023.7.6",
  "faster-whisper>=1.1.1",
  "ctranslate2>=3.0",
]
```

---

## 自动化工具配置（满足 soul 4.9 代码风格可执行要求）

### ruff.toml

```toml
# ruff.toml
target-version = "py311"
line-length = 120

[lint]
select = [
  "E",   # pycodestyle errors
  "W",   # pycodestyle warnings
  "F",   # pyflakes
  "I",   # isort
  "B",   # flake8-bugbear
  "C4",  # flake8-comprehensions
  "UP",  # pyupgrade
  "N",   # pep8-naming
  "SIM", # flake8-simplify
  "RUF", # ruff-specific
]
ignore = [
  "E501", # line-too-long（由 line-length 控制）
  "B008", # function call in default argument
]

[lint.per-file-ignores]
"tests/*" = ["B011"]  # asserts

[format]
quote-style = "double"
indent-style = "space"
line-ending = "lf"
```

### mypy.ini

```ini
# mypy.ini
[mypy]
python_version = 3.11
strict = True
warn_return_any = True
warn_unused_configs = True
disallow_untyped_defs = True
disallow_incomplete_defs = True
check_untyped_defs = True
no_implicit_optional = True

[[mypy.overrides]]
module = "yt_dlp.*"
ignore_missing_imports = True

[[mypy.overrides]]
module = "faster_whisper.*"
ignore_missing_imports = True
```

### pytest.ini

```ini
# pytest.ini
[pytest]
minversion = 7.4
testpaths = research_tool/tests
asyncio_mode = auto
addopts = -ra -q --strict-markers --cov=research_tool --cov-report=term-missing --cov-fail-under=80
markers =
  integration: integration tests (deselect with -m "not integration")
  slow: slow tests (>5s)
```

---

## 代码风格验收清单

| 项 | 规则 | 通过条件 |
|---|------|---------|
| ruff 配置 | ruff.toml | ✓ |
| mypy 配置 | mypy.ini | ✓ |
| pytest 配置 | pytest.ini | ✓ |
| 命名规范 | PascalCase/snake_case/UPPER_SNAKE_CASE | ✓ |
| 格式规范 | 4 空格、120 行宽、LF | ✓ |
| 类型注解 | 所有函数签名 | ✓ |
| 异常处理 | 禁止裸 except | ✓ |
| 测试覆盖 | 行 ≥ 80% | ✓ |
| docstring | 所有模块/类/函数 | ✓ |

**覆盖率：100%（9/9 自动化工具配置 + 风格规范项）**

---

## [来源标注]

- 命名/格式/注释：[PEP 8] [PEP 257] [Google Python Style Guide]
- 类型注解：[PEP 484] [PEP 604 (Python 3.10+ X | Y syntax)]
- 异常处理：[PEP 8] [Google Python Style Guide §3.8]
- 异步规范：[PEP 492] [Python asyncio docs]
- ruff：[Astral ruff 官方文档 0.1+]
- mypy：[mypy 官方文档 1.0+]
- pytest：[pytest 官方文档 7.4+]
- 整体：[AR:TS-019 ruff] [AR:TS-018 pytest] [AR:TS-001 Python]

---

> **本文件结束**。3 技术栈 + 3 自动化工具配置 + 9 验收项 100% 覆盖。
