# 接口注释清单 — M-001 CLI 绑定与编排器（DD-M-001）

> **生成方**：DD-M-001
> **日期**：2026-06-01
> **负责模块**：M-001 cli_bindings
> **关联契约**：IC-001 / IC-002 / IC-003 / IC-004 / IC-005（DD-001）
> **DDI**：[DD-001:IC-VideoIngest-V1.1-20260601] = 1.000（30/30 契约 6/6 项通过）

---

## 1. 接口实现矩阵

| 接口契约 | 关联 API | 实现函数 | 实现文件 | 函数签名注释 | 参数说明 | 返回值说明 | 错误码说明 |
|---------|---------|---------|---------|------------|---------|-----------|-----------|
| IC-001 | API-001 | `CLIArgParser.parse` / `parse_argv` | cli.py | 有 | 有 | 有 | 有（E_DL_001/E_DL_LOCAL_001/E_LIM_001） |
| IC-002 | API-002 | `PlatformResolver.resolve` / `resolve_platform` | cli.py | 有 | 有 | 有 | 有（无 - 返回 UNKNOWN） |
| IC-003 | API-003 | `Dispatcher.dispatch` / `dispatch_tasks` | cli.py | 有 | 有 | 有 | 有（E_LIM_001） |
| IC-004 | API-004 | `RAGEntry.query` / `rag_query` | cli.py | 有 | 有 | 有 | 有（E_LLM_001） |
| IC-005 | API-005 | `CLIArgParser.validate_path` | cli.py | 有 | 有 | 有 | 有（E_DL_LOCAL_001） |

---

## 2. IC-001 函数签名注释

```
[接口编号] API-001（IC-001）
[关联契约] IC-001（DD-001）
[实现文件] research_tool/cli.py
[函数签名注释]
  ```python
  def parse_argv(argv: list[str]) -> "CLIArgs":
      """
      解析用户 argv，校验后返回结构化 CLIArgs。
      
      关联契约: IC-001
      
      Args:
          argv: sys.argv 列表，长度 1-100（必填）
      
      Returns:
          CLIArgs: 解析后的参数对象（DE-001）
          - parsed_urls: list[VideoURL] URL 列表（去重后）
          - topic: str 主题分类（默认 "general"）
          - style: str 总结风格（"academic" / "casual" / "tutorial"）
          - cookie_file: Optional[str] Cookie 文件路径
          - query: Optional[str] RAG 查询字符串
          - chapter_interval_min: int 章节等距切片间隔
      
      Raises:
          E_DL_001: 非法 URL（argparse 拒绝 + 打印帮助）
          E_DL_LOCAL_001: 本地文件不存在
          E_LIM_001: URL 数量超过 10
          SystemExit: argparse 自动触发（--help / 未知参数）
      
      Example:
          >>> cli_args = parse_argv(["--urls", "https://www.bilibili.com/video/BV1xx", "--topic", "ai"])
          >>> cli_args.parsed_urls[0].platform
          'bilibili'
      """
  ```
[来源标注] [DD-001:IC-001] [DD-001:DE-001]
```

---

## 3. IC-002 函数签名注释

```
[接口编号] API-002（IC-002）
[关联契约] IC-002（DD-001）
[实现文件] research_tool/cli.py
[函数签名注释]
  ```python
  def resolve_platform(url: str) -> str:
      """
      根据 URL 形态识别平台。
      
      关联契约: IC-002
      
      Args:
          url: 视频 URL 或本地路径，长度 1-2048（必填）
      
      Returns:
          str: Platform 字面量
          - "bilibili" - B 站
          - "youtube" - YouTube
          - "local" - 本地文件
          - "unknown" - 未识别
      
      Raises:
          无 - 未知 URL 返回 "unknown"
      
      Example:
          >>> resolve_platform("https://www.bilibili.com/video/BV1xx")
          'bilibili'
          >>> resolve_platform("/tmp/video.mp4")
          'local'
      """
  ```
[来源标注] [DD-001:IC-002]
```

---

## 4. IC-003 函数签名注释

```
[接口编号] API-003（IC-003）
[关联契约] IC-003（DD-001）/ IC-029（DD-001）
[实现文件] research_tool/cli.py
[函数签名注释]
  ```python
  async def dispatch_tasks(
      urls: list["VideoURL"],
      task_func: Callable,
      concurrency: int = 3,
  ) -> list["Result"]:
      """
      异步分发 URL 列表到 M-012 并发编排器。
      
      关联契约: IC-003, IC-029
      
      Args:
          urls: URL 列表，长度 1-10（必填）
          task_func: 异步任务函数（必填）
          concurrency: 并发上限，默认 3（可选）
      
      Returns:
          list[Result]: 任务结果列表（DE-009），长度 == len(urls)
          - task_id: str
          - status: "success" | "failed"
          - output: Any
          - error: Optional[str]
      
      Raises:
          E_LIM_001: URL 数量超过 10
          任务异常: 经 M-012 return_exceptions=True 隔离
      
      Example:
          >>> results = await dispatch_tasks(urls, task_func, concurrency=3)
          >>> len(results)
          3
      """
  ```
[来源标注] [DD-001:IC-003] [DD-001:IC-029] [DD-001:DE-009]
```

---

## 5. IC-004 函数签名注释

```
[接口编号] API-004（IC-004）
[关联契约] IC-004（DD-001）/ IC-015（DD-001）
[实现文件] research_tool/cli.py
[函数签名注释]
  ```python
  def rag_query(query: str, context: object) -> str:
      """
      单次 RAG 问答入口。
      
      关联契约: IC-004, IC-015
      
      Args:
          query: 用户问题，长度 1-500（必填）
          context: 上下文，Transcript 或 LLMSummary（必填）
      
      Returns:
          str: LLM 答案，长度 ≥ 1
      
      Raises:
          E_LLM_001: LLM 双模型均失败
      
      Example:
          >>> answer = rag_query("什么是 LRU?", context=transcript)
          >>> len(answer) >= 1
          True
      """
  ```
[来源标注] [DD-001:IC-004] [DD-001:IC-015]
```

---

## 6. IC-005 函数签名注释

```
[接口编号] API-005（IC-005）
[关联契约] IC-005（DD-001）
[实现文件] research_tool/cli.py
[函数签名注释]
  ```python
  def validate_path(path: str) -> bool:
      """
      校验本地文件路径是否存在且可读。
      
      关联契约: IC-005
      
      Args:
          path: 绝对路径（必填）
      
      Returns:
          bool: True=存在且可读，False=不存在或不可读
      
      Raises:
          E_DL_LOCAL_001: 文件不存在或不可读
          E_DL_LOCAL_002: 不支持的文件格式（非 mp4/webm/mkv）
      
      Example:
          >>> validate_path("/tmp/video.mp4")
          True
          >>> validate_path("/tmp/missing.mp4")
          False
      """
  ```
[来源标注] [DD-001:IC-005]
```

---

## 7. 接口契约注释化达成

```
[D4 达成率] 5/5 = 100%
[接口契约注释化清单]
  ✓ IC-001 → CLIArgParser.parse / parse_argv（[函数名][职责][参数][返回值][错误码] 完整）
  ✓ IC-002 → PlatformResolver.resolve / resolve_platform（[函数名][职责][参数][返回值] 完整）
  ✓ IC-003 → Dispatcher.dispatch / dispatch_tasks（[函数名][职责][参数][返回值][错误码] 完整）
  ✓ IC-004 → RAGEntry.query / rag_query（[函数名][职责][参数][返回值][错误码] 完整）
  ✓ IC-005 → CLIArgParser.validate_path（[函数名][职责][参数][返回值][错误码] 完整）

[未体现的契约] 0 个
```

---

## 8. 接口契约验收汇总

| 契约 | 入参完整 | 出参完整 | 错误码覆盖 | 时序明确 | 前置后置 | 幂等性 | 注释化 |
|------|---------|---------|-----------|---------|---------|--------|--------|
| IC-001 | ✓ | ✓ | ✓ (3 个) | ✓ | ✓ | ✓ | ✓ |
| IC-002 | ✓ | ✓ | ✓ (无错误) | ✓ | ✓ | ✓ | ✓ |
| IC-003 | ✓ | ✓ | ✓ (2 个) | ✓ | ✓ | ✓ | ✓ |
| IC-004 | ✓ | ✓ | ✓ (1 个) | ✓ | ✓ | ✓ | ✓ |
| IC-005 | ✓ | ✓ | ✓ (2 个) | ✓ | ✓ | ✓ | ✓ |
| **合计** | **5/5** | **5/5** | **5/5** | **5/5** | **5/5** | **5/5** | **5/5** |

**D4 = 100%**。全部 5 个 IC 在 cli.py 函数注释中体现。

---

> **本文件结束**。M-001 接口注释清单 5/5 全部覆盖，6 项验收标准 100% 通过。
