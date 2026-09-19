# 接口注释清单 — M-004 缓存管理器（DD-M-004）

> **生成方**：DD-M-004
> **日期**：2026-06-01
> **接口契约数**：3（IC-009 / IC-010 / IC-011）
> **覆盖率**：100%（3/3 已在 cache_manager.py 中体现函数签名注释）

---

## API-009 缓存查询（IC-009）

```
[接口编号] API-009
[关联契约] IC-009（DD-001）
[实现文件] research_tool/cache_manager.py
[函数签名注释]
  def query_cache(url: VideoURL, etag: str = "") -> Optional[CacheEntry]:
      """
      通过 sha256(url) + etag 双键查询缓存（IC-009）。
      
      Args:
          url: VideoURL 对象（DE-002）
          etag: ETag/Last-Modified 字符串（默认 ""）
      
      Returns:
          CacheEntry 命中；None 未命中或 DB 不可用（降级）
      
      Raises:
          (无 — E_CK_001 触发降级返回 None，不抛错)
      
      Example:
          >>> entry = query_cache(video_url, etag="W/\"abc123\"")
      """
[参数说明] url 必填；etag 可选默认 ""
[返回值说明] Optional[CacheEntry]，特殊值 None 表示 miss 或 E_CK_001 降级
[错误码说明] E_CK_001: DB 不可用 → register_error → 返回 None
[并发安全] 是（asyncio.Lock 保护写；WAL 模式读并发）
[幂等性] 是（幂等键 url+etag；DB 存储期间永久有效）
[性能约束] < 10ms
[来源标注] [DD-001:IC-009] [AR:API-009] [调研:S-102]
```

## API-010 缓存写入（IC-010）

```
[接口编号] API-010
[关联契约] IC-010（DD-001）
[实现文件] research_tool/cache_manager.py
[函数签名注释]
  def write_cache(entry: CacheEntry) -> bool:
      """
      异步写锁串行化写入缓存条目（IC-010）。
      
      Args:
          entry: CacheEntry 对象（含 url_sha256/etag/payload/created_at）
      
      Returns:
          True 写入成功；False 写入失败（重试 1 次后仍失败）
      
      Raises:
          (无 — 写入失败重试后降级返回 False)
      
      Example:
          >>> ok = write_cache(cache_entry)
      """
[参数说明] entry 必填
[返回值说明] bool，wait_ms 记录到 M-011 日志
[错误码说明] E_CK_001: 写入失败 → 重试 1 次 → 降级返回 False
[并发安全] 是（asyncio.Lock 串行化 + UNIQUE 约束）
[幂等性] 是（幂等键 url+etag；ON CONFLICT REPLACE 覆盖；永久）
[性能约束] < 50ms
[来源标注] [DD-001:IC-010] [AR:API-010]
```

## API-011 缓存失效清理（IC-011）

```
[接口编号] API-011
[关联契约] IC-011（DD-001）
[实现文件] research_tool/cache_manager.py
[函数签名注释]
  def cleanup_expired(ttl_days: int = 30) -> int:
      """
      后台任务清理超过 TTL 的过期条目（IC-011）。
      
      Args:
          ttl_days: 通用 TTL（天），YouTube 平台固定 24h
      
      Returns:
          清理条目数（int，0 表示无过期）
      
      Raises:
          (无 — 清理失败不阻塞主链)
      
      Example:
          >>> count = cleanup_expired(ttl_days=30)
      """
[参数说明] ttl_days 可选默认 30
[返回值说明] int
[错误码说明] -（清理失败不抛错）
[并发安全] 是（独立后台任务）
[幂等性] 是（基于 created_at 过滤，重复执行安全）
[性能约束] 无 SLA（后台任务）
[来源标注] [DD-001:IC-011] [AR:API-011] [AR:BR-024/BR-026]
```
