# 结构风险清单 — VideoIngest V1.1（TD-001）

> **生成方**：TD-001
> **日期**：2026-06-01
> **风险总数**：8（高 2 / 中 3 / 低 3）

---

## SR-001 跨并发任务共享状态时效性

```
[风险编号] SR-001
[风险描述] BP-002 preflight 报告在 BP-011 3 并发场景下被共享。若首个任务 YouTube 失败后用户中途安装 Deno，后续 2 个任务仍按初始 PreflightReport（deno_pass=false）判定，导致 YouTube 任务持续失败。
[影响模块] M-002, M-001, M-012
[影响范围] BP-002 + BP-011 + 所有 YouTube 任务
[缓解策略]
  - BR-027: PreflightReport.ttl_seconds=60
  - M-001 在每个任务执行 BP-003 前重新拉取 preflight 状态
  - 重新检测逻辑在 IF-006 内部实现（不破坏接口契约）
[监控指标]
  - 指标: preflight_stale_hits (重新检测命中数) / preflight_cache_hits (缓存命中数)
  - 阈值: stale_hits/cache_hits > 10% 触发告警
  - 日志字段: M-011.DE-014.step = "preflight_revalidate"
[风险等级] 高（影响 YouTube 主功能）
[来源标注] [SA洞察#1] [CE-001]
```

## SR-002 LLM 字段名前缀污染

```
[风险编号] SR-002
[风险描述] M-006 LLM 输出 front matter 时若漏写 video_ 前缀（直接写 "title" 而非 "video_title"），污染 raw/ 文件，影响既有管道解析。
[影响模块] M-006, M-007, M-008, B-004
[影响范围] BP-006 之后所有链路 + 既有管道
[缓解策略]
  - BR-025: BP-006 步骤 6 后增加字段名前缀校验
  - M-006.IF-014: 字段名前缀校验（白名单 video_*）
  - 缺失时 LLM 强制重试 1 次
  - 仍失败 → M-010 E_LLM_001 + 保留中间产物
[监控指标]
  - 指标: front_matter_prefix_violations / total_summaries
  - 阈值: > 5% 触发告警（说明 prompt 需优化）
  - 日志字段: DE-006.yaml_parse_ok / DE-006.front_matter_prefix_ok
[风险等级] 高（影响管道自动跑通）
[来源标注] [SA洞察#2] [CE-006] [BR-003/BR-025]
```

## SR-003 M-007 接口数过载

```
[风险编号] SR-003
[风险描述] M-007 notes_schema 当前对外接口 6 个（IF-016~IF-021），超过建议上限 5。其中 IF-018（截图嵌入）和 IF-019（报告引用源）均为字符串片段拼装，可合并到 IF-020（Markdown 总装）。
[影响模块] M-007
[影响范围] 内部接口管理
[缓解策略]
  - V1.2 阶段合并 IF-018 + IF-019 到 IF-020（作为子步骤，不暴露独立接口）
  - 保留 IF-021 作为扩展点（不合并）
  - 当前 5/6 内聚度检查不阻塞交付，下轮优化
[监控指标]
  - 指标: M-007 接口数 / 模块调用频次
  - 阈值: 接口数 > 5 触发重构
[风险等级] 中（内部接口质量，非功能影响）
[来源标注] [TD推断:MP 模块划分内聚度自检]
```

## SR-004 Whisper 内存瓶颈

```
[风险编号] SR-004
[风险描述] M-005 转写模块在 medium 档下需 2-4GB RAM。3 并发场景下需 ≥8GB 可用 RAM，否则触发 BR-007 自动降档（base/small），影响转写质量。
[影响模块] M-005, M-012
[影响范围] BP-005 转写 + BP-011 并发
[缓解策略]
  - BR-007: RAM < 8G 自动降档
  - M-012.IF-030: 资源探测，3 并发 → 2 并发自动降级
  - 文档化 RAM 建议（8GB 最小 / 16GB 推荐）
  - V2.0 候选: M-014 GPU Whisper 服务独立部署
[监控指标]
  - 指标: transcriber_model_size 分布 / OOM 触发次数
  - 阈值: OOM 次数 > 0 持续 1 天 → 触发告警
  - 日志字段: DE-005.model_size, DE-005.cer_estimate
[风险等级] 中（影响质量，非阻塞）
[来源标注] [SA:BP-005/BR-007/EX-018] [调研:S-004/RR-003] [CE-005]
```

## SR-005 YouTube 缓存命中率衰减

```
[风险编号] SR-005
[风险描述] YouTube PO Token 24h 内多次轮换导致 etag 变化，缓存命中率下降。V1.1 引入 etag_revalidate_at 字段强制 24h revalidate，但可能增加 24h 内重复下载次数。
[影响模块] M-004
[影响范围] BP-004 缓存 + BP-003 下载
[缓解策略]
  - BR-024: YouTube 视频 24h 内强制 revalidate
  - 监控命中率衰减趋势
  - 若命中率 < 30% 持续 7 天 → 评估关闭 etag_revalidate_at（接受陈旧）
[监控指标]
  - 指标: cache_hit_rate (按平台分) / revalidate_triggered_count
  - 阈值: YouTube cache_hit_rate < 30% 持续 7 天
  - 日志字段: DE-004.etag_revalidate_at, DE-004.hit_count
[风险等级] 中（影响性能，非阻塞）
[来源标注] [SA洞察#3] [CE-003] [BR-024]
```

## SR-006 缓存 DB 并发写竞争

```
[风险编号] SR-006
[风险描述] 3 并发场景下 M-004 缓存写入可能竞争。V1.1 用 asyncio.Lock 串行化，但 sqlite WAL 模式下仍可能出现 lock 等待。极端情况下 3 个任务同时写缓存，主链延迟 + 200ms。
[影响模块] M-004, M-012
[影响范围] BP-004 + BP-011
[缓解策略]
  - 当前 asyncio.Lock 已足够（V1.1 单机假设）
  - 监控 lock_wait_ms 指标
  - V2.0 候选: 迁移到 Redis 后分布式锁
[监控指标]
  - 指标: cache_lock_wait_ms_p99
  - 阈值: p99 > 100ms 触发告警
  - 日志字段: M-011.DE-014.step="cache_write"
[风险等级] 低（监控已覆盖）
[来源标注] [TD推断:基于 sqlite WAL 性能特性]
```

## SR-007 既有管道集成配置遗漏

```
[风险编号] SR-007
[风险描述] BP-010 落盘后既有管道 Collect 阶段未配置"raw/ 下发现 video_*.md 即纳入"，导致新视频笔记未被自动纳入（CE-009）。
[影响模块] M-008, B-004
[影响范围] BP-010 → 既有管道
[缓解策略]
  - CE-009 修复: 在 BP-010 步骤 4 显式调用 research-tool Collect 配置 API 注入 1 行配置
  - 首次安装时检查配置（若缺失则自动注入）
  - 文档化配置注入步骤
[监控指标]
  - 指标: pipe_collect_config_present / pipe_stages_run
  - 阈值: pipe_success=false 且 reason="collect_miss" → 告警
  - 日志字段: DE-009.pipe_stages_run, DE-009.pipe_success
[风险等级] 中（影响管道自动跑通）
[来源标注] [CE-009]
```

## SR-008 CLI 退出码并发仲裁

```
[风险编号] SR-008
[风险描述] 3 并发任务中不同错误码（E_DL_001, E_DL_BILI_403, success）混合时，CLI 退出码当前实现可能错乱（CE-010）。
[影响模块] M-010, M-001
[影响范围] BP-012 + CI/CD 集成
[缓解策略]
  - CE-010 修复: 退出码取最严重错误码（403 > 401 > 500 > 0）
  - M-010.IF-027: 错误码优先级字典
  - 单元测试覆盖: 3 并发错误码混合场景
[监控指标]
  - 指标: cli_exit_code_consistency_rate
  - 阈值: 一致性 < 100% 触发回归
[风险等级] 低（仅 CI/CD 误判风险）
[来源标注] [CE-010] [EX-039]
```

---

> **本文件结束**。8 风险全部含缓解策略 + 监控指标 + 来源标注。
