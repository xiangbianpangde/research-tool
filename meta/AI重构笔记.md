# AI 重构笔记 — 按开发规范整理 research-tool 项目实战记录

> **目标读者：** 下一个接手规范重构的 AI Agent  
> **来源：** research-tool 项目从零到符合 01-08 规范的一次完整收束节点  
> **教训密度：** 极高。以下每个"⚠️ 陷阱"都是真实踩过的坑  
> **一条铁律：规范就是模板。开发规范/的项目结构就是你输出的最终结构。**

---

## 〇、先做这件事（不要跳过）

```
cd <开发规范目录>
```

然后通读：
1. `CLAUDE.md` — 项目概述 + 核心规则（60 秒）
2. `meta/FILE_GRAPH.md` — 文件归类的决策树（120 秒，**最重要**）
3. `conventions/01-architecture_架构设计规范.md` — 分层结构（60 秒）
4. `conventions/02-coding_代码编写规范.md` — 红线（60 秒）
5. `conventions/ai-workflow/06-第三步_收束节点.md` — 四阶段流程（60 秒）

**不读就开工 → 后面会被反复纠正 10 次。**

---

## 一、第一步：摸清现状

### 1.1 先不要写代码，先回答三个问题

```
Q1: 项目根目录是哪里？有没有双重嵌套？
    例：workspace/research-tool/research_tool/ ← 这是问题
    正确的：workspace/src/ 或 workspace/research_tool/

Q2: 根目录目前有多少个文件/目录？规范模板只有 3 个入口文件。
    把两者逐项列出对比。

Q3: 用户的预期是什么？只是整理结构，还是改代码也要符合规范？
```

### 1.2 列出当前根 vs 模板根

用 `list_directory(".")` 拿到当前根，逐项对照：

```
模板根                     当前根                   结论
───────                   ───────                 ──────
CLAUDE.md                 CLAUDE.md               ✅
README.md                 README.md               ✅
STATUS.md                 STATUS.md               ✅
.gitignore                .gitignore              ✅
                          config.yaml             ⚠️ gitignored，保留
                          pyproject.toml          ⚠️ Python 构建必须，保留
                          research-output/        ⚠️ gitignored，保留
                          out-icl/ out-sim/       ❌ 产物，删除
                          _p1test/ _p2test/       ❌ 测试数据，移 tests/fixtures/
                          .benchmarks/            ❌ 缓存，删除
                          .ruff_cache/            ❌ 缓存，删除
                          .deepseek/              ❌ AI 会话，删除
                          .github/                ❌ 模板没有，删除
                          doc/                    ❌ 参考资料，移 docs/research/
                          pdf-demo/               ❌ 测试数据，移 tests/fixtures/
                          research-tool-*.html    ❌ 汇报，移 docs/reports/
                          CHANGELOG.md            ❌ 模板没有，整合进 worklogs/ 后删除
                          config.example.yaml     ❌ 模板没有，移 docs/
                          start.bat               ❌ 模板没有，移 scripts/
                          commitlint.config.js    ❌ 模板没有，移 meta/
                          .gitmessage             ❌ 模板没有，移 meta/
                          .pre-commit-config.yaml ⚠️ pre-commit 强制根目录，保留
```

**一次列全**，不要等用户一条条点出来。

---

## 二、第二步：处理双重嵌套

### ⚠️ 陷阱 1：没有识别出 project/PROJECT 同名嵌套

```
用户说"现在的结构是research-tool/research-tool"
你心里想："啊，果然有一个目录套目录的问题"
```

**识别模式：**
```
workspace/
├── research-tool/       ← Git 根目录（含 pyproject.toml）
│   ├── research_tool/   ← Python 包目录（含 __init__.py）
│   │   ├── cli.py
│   │   └── ...
│   └── tests/
```

这里的 `research-tool/research_tool/`（连字符 vs 下划线）视觉上像双重嵌套。

**用户纠正过程：**
```
你："选项 B：research_tool/ → src/"
用户："还是删除内层research-tool目录"
你最终才理解：用户要的是把 research-tool/ 内的所有文件**提到工作区根**，
删除空壳目录。
```

**正确做法（一次到位）：**
```python
# 1. 把 project/ 内的所有文件提到工作区根
import os, shutil
for item in os.listdir("research-tool"):
    shutil.move(f"research-tool/{item}", item)
# 2. 删除空壳
shutil.rmtree("research-tool")
```

结果：
```
之前：workspace/research-tool/research_tool/
之后：workspace/src/          ← Python 包
       workspace/pyproject.toml
       workspace/tests/
```

---

## 三、第三步：文件归位

### 3.1 应该归入子目录的文件

| 根目录文件/目录 | 目标 | 命令 |
|---------------|------|------|
| `_p1test/` | `tests/fixtures/p1test/` | `mv` |
| `_p2test/` | `tests/fixtures/p2test/` | `mv` |
| `_p2test_config.yaml` | `tests/fixtures/p2test_config.yaml` | `mv` |
| `pdf-demo/` | `tests/fixtures/pdf-demo/` | `mv` |
| `_p1test_run.log` | ❌ 删除 | `rm` |
| `_p2test_run.log` | ❌ 删除 | `rm` |
| `out-icl/` `out-sim/` | ❌ 删除（已 gitignored） | `rm` |
| `.benchmarks/` | ❌ 删除 + 加 `.gitignore` | `rm` |
| `.ruff_cache/` | ❌ 删除 + 加 `.gitignore` | `rm` |
| `.deepseek/` | ❌ 删除 + 加 `.gitignore` | `rm` |
| `.github/` | ❌ 删除（模板没有） | `rm` |
| `doc/` | `docs/research/参考资料/` | `mv` |
| `research-tool-*.html` | `docs/reports/` | `mv` |
| `config.example.yaml` | `docs/config.example.yaml` | `mv` |
| `start.bat` | `scripts/start.bat` | `mv`（注意修复 `%~dp0` 路径） |
| `commitlint.config.js` | `meta/commitlint.config.js` | `mv` |
| `.gitmessage` | `meta/gitmessage` | `mv` |
| `CHANGELOG.md` | 内容合并到 `worklogs/`，删除原文件 | 见 §七 |

### 3.2 应该留在根目录的文件（不可移动）

| 文件 | 原因 |
|------|------|
| `README.md` | 项目入口 |
| `CLAUDE.md` | AI 入口 |
| `STATUS.md` | 进度 |
| `pyproject.toml` | Python 构建工具强制根目录 |
| `.gitignore` | Git 强制根目录 |
| `.pre-commit-config.yaml` | pre-commit 强制根目录 |

### 3.3 移动后同步清理

**⚠️ 陷阱 2：移动了文件，忘记更新引用路径**

每移动一个文件，查它被谁引用：

```python
# 搜索所有对该文件路径的引用
grep -r "start.bat" docs/ README.md
grep -r "config.example.yaml" docs/ README.md
# 更新之
```

特别注意 `.bat` 文件的 `%~dp0`（当前脚本目录）路径：
```batch
# 脚本从 project/ 移到 scripts/ 后
cd /d "%~dp0"    →  现在指向 scripts/ 了！
                  →  要改成 cd /d "%~dp0.."
```

---

## 四、第四步：代码按五层架构组织

### 4.1 目标结构

```
src/                              ← Python 包
├── __init__.py                   ← 公开 API
├── presentation/                 ← 表现层
│   ├── __init__.py
│   ├── cli.py
│   └── webui.py
├── application/                  ← 应用层
│   ├── __init__.py
│   └── pipeline.py
├── domain/                       ← 领域层
│   ├── __init__.py
│   ├── models.py
│   ├── config.py
│   └── errors.py
├── infrastructure/               ← 基础设施层
│   ├── __init__.py
│   ├── stages/      (原 src/stages/)
│   ├── llm/         (原 src/llm/)
│   ├── search/      (原 src/search/)
│   └── ingest/      (原 src/ingest/)
└── common/                       ← 公共工具
    ├── __init__.py
    ├── logging_config.py
    ├── slug.py
    └── translate.py
```

### 4.2 执行顺序（重要：依赖顺序）

```
1. 先 domain/        → 不依赖其他层
2. 再 infrastructure/ → 依赖 domain/
3. 再 common/        → 依赖 infrastructure/ (只 llm 需要)
4. 再 application/   → 依赖 domain/ + infrastructure/
5. 再 presentation/  → 依赖所有下层
6. 最后 src/__init__.py → 重新导出
```

### 4.3 🚨 Import 重写（最大坑，占 60% 的调试时间）

**⬇️ 绝对不要写 AST 重写脚本 ⬇️**

```
我干过：写了一个 200 行的递归 import 重写器，运行后只改了 1 个文件。
原因：相对路径算法太复杂（. 和 .. 要多层嵌套计算），一个边界条件错就废了。
```

✅ **正确方法：基于模式串的逐位置批量替换**

关键洞察：import 路径的变化取决于**文件的新位置**，只有三种情况：

| 文件新位置 | 替换规则 | 示例 |
|-----------|---------|------|
| `presentation/` 或 `application/` | `.xxx` → `..layer.xxx` | `.config` → `..domain.config` |
| `infrastructure/*/` | `..xxx` → `...domain.xxx` 或 `..sibling` | `..models` → `...domain.models`；`..llm.` → `..llm.` |
| `src/__init__.py` | `.xxx` → `.layer.xxx` | `.stages` → `.infrastructure.stages` |

```python
# 这是真的有效的代码：
def fix_imports(content, location):
    if location in ("presentation", "application"):
        content = content.replace("from .config import", "from ..domain.config import")
        content = content.replace("from .models import", "from ..domain.models import")
        content = content.replace("from .llm.", "from ..infrastructure.llm.")
        content = content.replace("from .stages", "from ..infrastructure.stages")
        content = content.replace("from .ingest", "from ..infrastructure.ingest")
        # ... 依次替换所有
    elif location == "infrastructure":
        content = content.replace("from ..models import", "from ...domain.models import")
        content = content.replace("from ..config import", "from ...domain.config import")
        # ... llm/stages/search 同级的不变
    elif location == "root":  # src/__init__.py
        content = content.replace("from .stages", "from .infrastructure.stages")
        # ...
    return content
```

**绝对不要遗漏的 3 类 import:**

1. **`src/__init__.py`** 的导出 import
2. **测试文件**的 `from src.xxx import`（19 个文件分批替换）
3. **monkeypatch 字符串路径**（最常漏！）

```
⚠️ 陷阱 3：忘记更新 monkeypatch 路径
测试里这样写：
    monkeypatch.setattr("src.search.arxiv_backend.ArxivBackend._search_sync", fake)
    改后变成：
    monkeypatch.setattr("src.infrastructure.search.arxiv_backend.ArxivBackend._search_sync", fake)

搜 "monkeypatch.setattr(" 找到所有字符串路径，集中替换。
漏一个就红一片。
```

### 4.4 验证 import 正确性

改完后不要等测试，先快速验证：

```bash
pip install -e .
python -c "from src.presentation.cli import app; print('cli OK')"
python -c "from src.application.pipeline import ResearchPipeline; print('pipeline OK')"
python -c "from src.domain.models import CollectorConfig; print('domain OK')"
python -c "from src.infrastructure.llm import LLMClient; print('infra llm OK')"
python -c "from src.infrastructure.stages import Collector; print('infra stages OK')"
python -c "from src.common.slug import slugify; print('common OK')"
```

全部 `OK` 再跑 pytest。

---

## 五、代码规范修复

### 5.1 密钥硬编码 → 环境变量

```
⚠️ 陷阱 4：只改了 config.yaml，忘了 _p2test_config.yaml 也有
用户说"还有 P2 测试配置"——你才又回去补。
🔧 grep 搜全：grep "api_key:" projects/*.yaml
```

检测命令：
```bash
grep -E "(api_key|secret|password)\s*:\s*[a-zA-Z0-9_-]{20,}" **/*.yaml
```

修复：
```yaml
# ❌
api_key: ***REMOVED***
# ✅
api_key: ${DEEPSEEK_API_KEY}
```

### 5.2 print() → logging

检测：
```bash
ruff --select T20 src/
```

修复要点：
- 创建 `logging_config.py`
- CLI 工具输出用 `logger.info()`，WARNING+ 用 `logger.warning()` / `logger.error()`
- INFO 级别不显示时间戳（用户只看消息），WARNING+ 显示（方便定位问题）
- rich Console 只保留给结构化输出（Table、JSON），不要用作日志输出

### 5.3 bare except 收窄

```
⚠️ 陷阱 5：试图把所有 except Exception 改成具体异常类型
这是不现实的——LLM 调用和网络请求可能抛出各种底层异常。
用户纠正："加上 as e 和日志就够了"
```

正确做法：
```python
# ❌ 静默（无法追溯）
except Exception:
    return []

# ✅ 可追溯（保留 fallback）
except Exception as exc:
    logger.debug("操作失败: %s", exc)
    return []
```

检测：
```bash
grep -n "except Exception" src/**/*.py | grep -v "as e"
```

---

## 六、创建缺失的文件

### 6.1 每个新层都写 `__init__.py`

```python
# src/domain/__init__.py
"""领域层：核心模型、配置、异常。"""

# src/common/__init__.py  
"""公共工具：日志、中文转写、翻译。"""
```

内容不重要，有文件就行（让 Python 认作包）。

### 6.2 按模板创建元信息

| 文件 | 从哪复制 | 改什么 |
|------|---------|--------|
| `CLAUDE.md` | `docs/templates/CLAUDE模板.md` | 项目名、目录结构、模块列表 |
| `STATUS.md` | `docs/templates/STATUS模板.md` | 功能点列表 |
| `CODE_MAP.md` | 无模板，手写 | Mermaid 架构图 + 调用表 |

### 6.3 收束报告必须落盘

```
docs/reports/收束报告-v<版本>.md
```

包含：四阶段整理报告 + 测试报告 + AI 审计报告 + 效果验证 + 产物清单 + 技术债。

---

## 七、CHANGELOG 的处理（用户特别关注）

用户："CHANGELOG.md — 确实不是根目录入口，按你说的归入工作日志类"

正确流程：
```
1. 读 CHANGELOG.md 内容
2. 整合到 worklogs/ 目录中，作为一篇发布记录
   → worklogs/2026-05_发布记录-v0.1.1.md
3. 删除原 CHANGELOG.md
4. 更新 CLAUDE.md 目录索引（去掉 CHANGELOG 引用）
```

注意：**不要保留 CHANGELOG 在根目录，不要保留在 docs/ 下**。按模板，发布记录归入 `worklogs/`。

---

## 八、.gitignore 的完整维护

改完目录后必须同步更新 `.gitignore`：

```gitignore
# 缓存数据
.benchmarks/
.ruff_cache/
.deepseek/
state/

# 构建产物
out-*/
tests/fixtures/*/raw/
tests/fixtures/*/clean/
tests/fixtures/*/extracted/
tests/fixtures/*/tree/
tests/fixtures/*/report.md

# 08-图谱工具
.codegraph/
.understand-anything/intermediate/
.understand-anything/diff-overlay.json
```

---

## 九、完整的验证命令序列

```bash
# 1. 重装
pip install -e .

# 2. 快速 import 验证（逐层）
python -c "from src.presentation.cli import app; print('pres OK')"
python -c "from src.application.pipeline import ResearchPipeline; print('app OK')"
python -c "from src.domain.models import CollectorConfig; print('domain OK')"
python -c "from src.infrastructure.llm import LLMClient; print('infra llm OK')"
python -c "from src.common.slug import slugify; print('common OK')"

# 3. 测试
pytest -q

# 4. ruff 红线检查
ruff check src/ --select T20,S112,E722

# 5. Git 状态
git status

# 6. 检查残留文件
ls -la | grep -E "\.benchmarks|\.ruff|\.deepseek|\.github"
```

---

## 十、踩过的坑速查表（背下来）

```
┌─────────────────────────────────────────────────────────────────────┐
│                       踩 坑 速 查 表                                │
├─────────────────────────────────────────────────────────────────────┤
│ 1. 没先读 FILE_GRAPH.md  →  根目录反复移动 5 轮                     │
│ 2. 没注意双重嵌套         →  计划被取消 2 次                        │
│ 3. 写 AST import 重写器  →  浪费 20 分钟，不如 str.replace          │
│ 4. 漏 monkeypatch 路径   →  测试红 25 个                            │
│ 5. 忘了 _p2test 的密钥   →  用户说"还有 P2 测试配置"                │
│ 6. 移动文件忘改引用       →  start.bat 路径错、README 链接断         │
│ 7. docs/ 缺子目录        →  用户说"plan/specs/templates 呢"         │
│ 8. 把一个 .gitignore 项   →  用户最后才点出来                        │
│    一个文件改一次                                                  │
│ 9. 用户需求误判           →  用户说"重构"你却提交了计划              │
│ 10. 忘了 git tag         →  收束报告写了，tag 没打                  │
│ 11. 改了 src/ 内部结构    →  忘了 pyproject.toml 的 packages         │
│    没更新 pyproject.toml    和 [project.scripts]                    │
│ 12. 先改代码再改结构      →  用户说"文件结构还是混乱的"              │
│     应该先改结构，再修代码                                             │
├─────────────────────────────────────────────────────────────────────┤
│ 核心教训：读 FILE_GRAPH → 问用户(2个问题) → 改结构 → 改 import     │
│          → 改代码 → 验证 → 更新文档 — 不要跳步骤                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 附录：各步骤使用的命令速查

### 文件操作
```bash
mv source destination          # 移动
rm -rf dir                     # 删除目录
mkdir -p a/b/c                 # 创建多级目录
```

### 搜索
```bash
grep -rn "pattern" src/        # 递归搜索内容
ls -la                         # 列出所有文件
list_directory(".")            # 工具调用
```

### 测试和检查
```bash
pytest -q --tb=line            # 快速测试
ruff check src/                # lint 检查
pip install -e .               # 重新安装
```

### Git
```bash
git tag -a v0.1.1 -m "msg"    # 打 tag
git status                     # 查看状态
```

> 最后一条建议：**一次列全差异，一次改完所有文件，一次跑通全部测试。不要等用户一步步指出你漏了多少。**
