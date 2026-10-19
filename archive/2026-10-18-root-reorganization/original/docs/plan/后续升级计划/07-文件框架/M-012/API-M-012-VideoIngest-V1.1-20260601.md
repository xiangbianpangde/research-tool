# 接口注释清单 — M-012 并发编排器（DD-M 输出）

> **生成方**：DD-M-M-012
> **日期**：2026-06-01
> **负责模块**：M-012（concurrent_orchestrator）
> **接口契约数**：3（IC-003 / IC-029 / IC-030）
> **覆盖率**：100%（3/3 全部含函数签名注释 + 参数说明 + 返回值说明 + 错误码说明）

---

## API-001 IC-003 并发调度

```
[接口编号] API-001（IC-003）
[关联契约] IC-003（来自 DD-001）
[接口名称] 并发调度
[实现文件] research_tool/concurrent_orchestrator.py
[函数签名注释]
  ```python
  async def gather_tasks(
      urls: list[VideoURL],                                    # 必填 URL 列表（长度 1-10）
      task_func: Callable[[VideoURL], Awaitable[Result]],       # 必填 任务函数
      concurrency: int = 3,                                     # 可选 并发上限（默认 3）
  ) -> list[Result]:                                            # 返回结果列表（与 urls 等长）
      """
      M-012 主入口：URL 列表并发执行（IC-003/IC-029）。

      Args:
          urls: URL 列表，长度必须 1-10（超过触发 E_LIM_001）
          task_func: 异步任务函数，接受 VideoURL 返回 Result
          concurrency: 并发上限，默认 3（资源池大小）

      Returns:
          结果列表，长度与 urls 相同；异常位置为 Exception 实例（return_exceptions=True 隔离）

      Raises:
          ValueError: URL 数量 > 10 触发 E_LIM_001

      Example:
          >>> results = await gather_tasks(
          ...     urls=[url1, url2, url3],
          ...     task_func=process_one_url,
          ...     concurrency=3
          ... )
      """
  ```
[参数说明]
  参数1: urls - 视频 URL 列表，必填，校验规则：长度 1-10，每项为 VideoURL 实例
  参数2: task_func - 任务函数，必填，签名约束：async def(VideoURL) -> Result
  参数3: concurrency - 并发上限，可选，默认 3，校验规则：1 <= concurrency <= 10
[返回值说明]
  类型: list[Result]
  描述: 与 urls 等长的结果列表，异常隔离后失败位置为 Exception 实例
  特殊值: 空列表（urls 为空时）；含 Exception 实例（部分失败时）
[错误码说明]
  E_LIM_001: URL 数量 > 10，拒绝执行
  任务异常: 隔离到 results 列表，不中断整体
[并发安全] 是（Semaphore 保护）
[幂等性] 否（每次调用重新执行 task_func）
[性能约束] 启动 < 50ms / 总耗时 = 最慢任务 + 排队时间
[来源标注] [DD-001:IC-003] [DD-001:IC-029] [DD-001:MD-M-012]
```

## API-002 IC-029 asyncio.gather 并发执行

```
[接口编号] API-002（IC-029）
[关联契约] IC-029（来自 DD-001）
[接口名称] asyncio.gather 并发执行
[实现文件] research_tool/concurrent_orchestrator.py
[函数签名注释]
  ```python
  async def gather_tasks(
      urls: list[VideoURL],                                    # 必填 URL 列表
      task_func: Callable[[VideoURL], Awaitable[Result]],       # 必填 任务函数
  ) -> list[Result]:                                            # 返回结果列表
      """
      URL[] + task_func 经 Semaphore(3) 并发执行（IC-029）。

      Args:
          urls: URL 列表（与 IC-003 一致）
          task_func: 任务函数（与 IC-003 一致）

      Returns:
          结果列表（与 IC-003 一致）

      Raises:
          同 IC-003

      Example:
          同 IC-003
      """
  ```
[参数说明] 与 IC-003 一致（gather_tasks 是 IC-003 + IC-029 的统一入口）
[返回值说明] 与 IC-003 一致
[错误码说明] 与 IC-003 一致
[并发安全] 是（Semaphore(3) 保护，3 并发上限）
[幂等性] 否
[性能约束] 启动 < 50ms
[来源标注] [DD-001:IC-029] [DD-001:MD-M-012 子模块2 task_gatherer]
```

## API-003 IC-030 资源探测降级

```
[接口编号] API-003（IC-030）
[关联契约] IC-030（来自 DD-001）
[接口名称] 资源探测降级
[实现文件] research_tool/concurrent_orchestrator.py
[函数签名注释]
  ```python
  def detect_concurrency() -> int:  # 返回推荐并发数（2 或 3）
      """
      psutil 探测 CPU/MEM，< 8GB 降级到 2 并发（IC-030）。

      Args:
          无

      Returns:
          推荐并发数（int ∈ {2, 3}）
          - 2: RAM < 8GB 降级
          - 3: 默认（高内存或探测失败）

      Raises:
          无（探测失败返回默认 3）

      Example:
          >>> concurrency = detect_concurrency()
          >>> semaphore = asyncio.Semaphore(concurrency)
      """
  ```
[参数说明] 无参数
[返回值说明]
  类型: int
  描述: 推荐并发数（2 或 3）
  特殊值: 3（探测失败或 psutil 不可用时）
[错误码说明] -（无错误码，探测失败优雅降级）
[并发安全] 是
[幂等性] 是
[性能约束] < 10ms
[来源标注] [DD-001:IC-030] [DD-001:MD-M-012 子模块3+4 resource_prober/concurrency_resolver]
```

---

## 接口契约验收汇总

| 契约 | 函数签名注释 | 参数说明 | 返回值说明 | 错误码说明 | 性能约束 | 幂等性 | 并发安全 | 通过 |
|------|------------|---------|-----------|-----------|---------|--------|---------|------|
| IC-003 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 7/7 |
| IC-029 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 7/7 |
| IC-030 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 7/7 |

**3/3 全部通过接口契约验收**。
