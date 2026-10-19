# 性能优化方案 — VideoIngest V1.1（AR-001）

> **生成方**：AR-001
> **日期**：2026-06-01
> **覆盖范围**：5 项 TD 性能容量预判（PC-001~PC-005）→ 5 套优化策略
> **覆盖率**：100%（5/5 含瓶颈分析/优化策略/实施步骤/验证方法/回滚策略）

---

## PO-001 30 分钟视频端到端延迟

```
[性能指标] 30 分钟视频端到端处理时间（PC-001）
[目标] ≤ 8 分钟 [TD:PC-001] [PRD:NF-001]
[瓶颈分析]
  模块: M-005 转写
  原因: faster-whisper medium 档 CPU 推理 ~5min (单视频)
  资源: 2-4GB RAM (medium 档)
  占比: 总时延 60-70%
[优化策略]
  1. 缓存命中: M-004 命中时跳过转写/LLM，直接返回笔记
  2. 引擎降级: RAM < 8GB 自动 base/small 档（牺牲质量换速度）[BR-007]
  3. 资源探测: M-012 探测 CPU 核数，< 4 核降并发 1
  4. 引擎路由: M-005 优先 bcut（B 站原生 ASR，速度快）
  5. V2.0 候选: M-014 GPU Whisper 服务（独立部署 REST 9001）[TD:ER V-003]
[实施步骤]
  步骤1: M-005 启动时探测 RAM（psutil.virtual_memory().available < 8GB → 降档）
  步骤2: M-005 探测模型文件存在，不存在自动下载 base → small → medium 降级序列
  步骤3: M-004 命中时 IF-009 直接返回 DE-004（< 10ms）
  步骤4: M-012 并发限流到 3 避免 OOM
[验证方法]
  基准测试: 3 个 30 分钟 1080p 视频（标准普话 + 英文 + 粤语音频）
  测量: 端到端 P95 ≤ 8 分钟（PC-001 通过）
  工具: pytest-benchmark + 自定义 time.perf_counter
[回滚策略]
  触发: P95 > 10 分钟持续 3 天
  步骤: 关闭引擎降级 → 仅 medium → 接受 > 8 分钟
  数据一致性: 降级产出的笔记 CER 较低但字段完整
  预计回滚时间: < 5 分钟（配置开关）
[监控指标]
  - 端到端时长 P50/P95/P99
  - 转写时长 P95
  - 缓存命中率
  - transcriber_model_size 分布
  - OOM 触发次数
[来源标注] [TD:PC-001] [TD:SR-004] [调研:S-004/RR-003]
```

---

## PO-002 缓存命中率

```
[性能指标] 缓存命中率（PC-002）
[目标] 同 URL 重复入参 ≤ 3s 返回 [TD:PC-002] [PRD:F-009.AC-1]
[瓶颈分析]
  模块: M-004 缓存
  原因: 命中率 30-50%（首跑 + 重复入参混合）
  影响: 命中时端到端从 8min 降至 < 3s
[优化策略]
  1. 双键缓存: sha256(url) + etag/last_modified（YouTube 反爬兼容）[TD:DE-004] [BR-004]
  2. YouTube 24h revalidate: 强制刷新避免陈旧 [BR-024]
  3. WAL 模式: 1 写 N 读，支持 3 并发查询
  4. 索引优化: IDX_url_sha256 + IDX_created_at
  5. 失效清理: ttl=30d 后台任务（IF-011）[BR-026]
  6. V2.0 候选: 暴露缓存统计 CLI（命中率 + 命中 Top10）
  7. V2.0 候选: 迁移 Redis（多设备共享 + 分布式锁）[TD:ADR-007]
[实施步骤]
  步骤1: M-004 启动时创建 sqlite 表 + 索引（WAL checkpoint）
  步骤2: M-004 IF-009 查双键，命中返回 DE-004
  步骤3: M-004 IF-010 写入 DE-004（asyncio.Lock 串行化）
  步骤4: M-004 IF-011 后台任务清理 ttl=30d 条目
  步骤5: 命中 < 3s 验证 → 持续监控
[验证方法]
  基准测试: 100 个视频（B 站 / YouTube 各 50）
  测量:
    - 立即重复入参 100% 命中 + ≤ 3s
    - 24h 后重复 100% 命中（etag revalidate）
    - 30 天后重复 0% 命中（ttl 过期）
  工具: 自定义 stats.py（命中率 + Top10）
[回滚策略]
  触发: 命中率 < 30% 持续 7 天
  步骤: 关闭 etag revalidate（接受陈旧）→ 或强制 ttl=7d（更激进失效）
  数据一致性: 关闭 revalidate 后命中陈旧可能，但接口契约不变
  预计回滚时间: < 5 分钟（配置开关）
[监控指标]
  - cache_hit_rate (按平台分)
  - cache_lock_wait_ms_p99
  - 失效清理条数 / 总条数
  - revalidate_triggered_count
[来源标注] [TD:PC-002] [TD:SR-005/SR-006] [调研:S-102] [TD:ADR-004]
```

---

## PO-003 3 并发视频处理

```
[性能指标] 3 个 30 分钟视频并发处理（PC-003）
[目标] ≤ 12 分钟（端到端）[TD:PC-003] [PRD:F-008.AC-1]
[瓶颈分析]
  模块: M-005 转写（3 × medium = 6-12GB RAM）/ M-001 CLI 调度
  原因: 转写并行吃 RAM
  临界: 8GB RAM → 触发降档
[优化策略]
  1. Semaphore(3) 限流: M-012 严格控制 3 并发 [TD:ADR-006]
  2. 资源探测降级: psutil.virtual_memory().available < 8GB → 降级 2 并发
  3. 缓存串行化写: M-004 asyncio.Lock 避免 3 任务同时写 cache.db
  4. 引擎优先级: whisper medium → bcut → groq 顺序
  5. URL 上限: 单次入参 > 10 → 仅取前 10 [TD:ADR-006]
  6. V2.0 候选: V2.0 M-014 GPU Whisper 服务独立部署（V2.0 5 并发）
[实施步骤]
  步骤1: M-012 Semaphore(3) + gather
  步骤2: M-012.IF-030 资源探测 → 降级 2 并发
  步骤3: M-004.asyncio.Lock 串行化写
  步骤4: CLI 拒绝 URL > 10（提示用户分批）
[验证方法]
  基准测试: 3 个 30 分钟视频（混合 B 站 / YouTube）
  测量:
    - 8GB RAM: 端到端 ≤ 12 分钟 + 自动降级
    - 16GB RAM: 端到端 ≤ 12 分钟 + 3 并发稳定
    - 32GB RAM: 端到端 ≤ 10 分钟（资源充足）
  工具: 自定义 concurrent_bench.py
[回滚策略]
  触发: OOM 持续 1 天或 3 并发失败率 > 5%
  步骤: 关闭 3 并发 → 强制 1 并发 → 接受 > 30 分钟
  数据一致性: 部分任务继续运行，无数据丢失
  预计回滚时间: < 5 分钟（配置开关）
[监控指标]
  - concurrency_level 实际值
  - OOM 触发次数
  - 自动降级次数
  - 端到端时长 P95
[来源标注] [TD:PC-003] [TD:SR-004] [TD:ADR-006]
```

---

## PO-004 LLM 调用 token 预算

```
[性能指标] 单次 LLM 调用 token 预算（PC-004）
[目标] ≤ 20k input / ≤ 4k output [TD:PC-004] [PRD:F-005.AC-2/BR-002]
[瓶颈分析]
  模块: M-006 LLM
  原因: 输入超 20k → 章节连贯性损失（PM 决策选项 A）
  资源: HTTP 1-5s
[优化策略]
  1. 截断不切分: PM 决策选项 A（输入 > 20k 截断 + 警告）[调研:S-008]
  2. 输出截断: > 4k 自动截断 + 警告
  3. 字段名前缀校验: M-006 IF-014 防止 LLM 漏写 video_ 前缀 [TD:SR-002/ADR-005]
  4. 强制重试 1 次: YAML 解析失败时 [BR-025]
  5. Fallback 切换: Deepseek 5xx 3 次后切 Qwen-turbo [BR-009]
  6. V2.0 候选: M-019 Embedding 语义缓存（重复转写稿复用）
  7. V2.5 候选: 多 LLM 供应商路由（成本优化）
[实施步骤]
  步骤1: M-006 输入 token 计数（tiktoken 或 len(text) * 1.5）
  步骤2: > 20k → 截断到 20k + log warning
  步骤3: 输出 token 计数（HTTP response usage 字段）
  步骤4: > 4k → 自动截断 + log warning
  步骤5: YAML 解析失败 → 重试 1 次
  步骤6: Deepseek 5xx 3 次 → 切 Qwen-turbo
[验证方法]
  基准测试: 短 (5min) / 中 (30min) / 长 (120min) 视频转写稿
  测量:
    - 短: < 2k input
    - 中: 8-15k input
    - 长: 20-25k input（截断触发）
  通过标准: 单次调用 ≤ 20k+4k，无切分
  工具: 自定义 llm_bench.py + Deepseek/Qwen API
[回滚策略]
  触发: LLM 失败率 > 5% 持续 3 天
  步骤: 关闭 fallback（仅 Deepseek）→ 或切换主备顺序
  数据一致性: 切换后 DE-006 字段完整（fallback_used=true 标记）
  预计回滚时间: < 5 分钟（配置开关）
[监控指标]
  - input_tokens P50/P95/P99
  - output_tokens P95
  - 截断次数 / 总调用次数
  - fallback_used 占比
  - LLM 调用时长 P95
[来源标注] [TD:PC-004] [调研:S-008] [TD:SR-002/ADR-005]
```

---

## PO-005 重试退避

```
[性能指标] 网络/IO 操作重试退避（PC-005）
[目标] 重试 ≤ 2 次 [TD:PC-005] [PRD:NF-009]
[瓶颈分析]
  模块: M-010 错误处理 / M-006 LLM
  原因: 5xx / timeout / connect error 重试
  资源: 重试累计耗时
[优化策略]
  1. 指数退避: 2^n（1s, 2s, 4s）[TD:BR-015]
  2. LLM 特殊: 5xx 3 次后切 fallback（不重试同模型）[BR-009]
  3. 共享 retry_backoff 函数: M-010 / M-006 复用
  4. 错误码区分: 4xx（不重试，参数错误） vs 5xx（重试，服务端错误）
  5. 超时上限: 重试累计 ≤ 30s（避免长时间等待）
  6. V2.0 候选: M-016 LLM 代理（统一重试 + 限流 + 熔断）
[实施步骤]
  步骤1: M-010 retry_backoff(n) 函数（指数退避）
  步骤2: M-006 HTTP 调用包裹 retry_backoff
  步骤3: M-006 5xx 计数，3 次后切 Qwen-turbo（仅 1 次切换）
  步骤4: M-003 yt-dlp 调用包裹 retry_backoff
  步骤5: M-008 落盘重试 1 次
[验证方法]
  基准测试: 模拟 403/412/5xx 注入
  测量:
    - 重试次数分布
    - 总耗时
    - 成功率
  通过标准: 任意操作重试 ≤ 2 次（LLM fallback 切换除外）
  工具: 自定义 mock_failure_injector.py
[回滚策略]
  触发: 网络抖动导致重试耗时长 > 30s
  步骤: 关闭重试 → 仅 1 次尝试 → 接受部分任务失败
  数据一致性: 失败任务由 AG-010 登记，partial_success 标记
  预计回滚时间: < 5 分钟（配置开关）
[监控指标]
  - 重试次数分布
  - 重试总耗时
  - fallback 切换次数
  - 4xx / 5xx 比例
[来源标注] [TD:PC-005] [BR-009/BR-015] [TD:SR-008]
```

---

## 性能优化策略覆盖度自检

| TD 性能指标 | AR 优化策略 | 验证方法 | 回滚策略 | 监控指标 | 状态 |
|------------|------------|---------|---------|---------|------|
| PC-001 端到端 ≤ 8min | 缓存+降档+引擎路由 | pytest-benchmark | 配置开关 | P95/降级次数 | ✓ |
| PC-002 命中 ≤ 3s | 双键+WAL+索引 | stats.py | 关闭 revalidate | 命中率/P99 | ✓ |
| PC-003 3 并发 ≤ 12min | Semaphore+降级+串行化 | concurrent_bench.py | 强制 1 并发 | OOM/降级 | ✓ |
| PC-004 LLM 20k+4k | 截断+重试+fallback | llm_bench.py | 关闭 fallback | token/截断 | ✓ |
| PC-005 重试 ≤ 2 次 | 指数退避+fallback | mock_failure_injector | 关闭重试 | 重试分布 | ✓ |

**覆盖率 = 100%**（5/5 全部含完整 6 维度：策略/步骤/验证/回滚/监控/来源）

---

## 容量规划（CP-001）

```
[容量编号] CP-001
[性能指标] 3 并发 × 30 分钟视频
[当前容量] 8GB RAM（推荐 16GB）
[目标容量] V1.1 维持 3 并发 / V2.0 5 并发 + GPU 服务
[扩容倍数] V2.0 5/3 = 1.67x
[所需资源增量] V2.0 GPU 服务器（独立）
[扩容步骤]
  步骤1: V1.1 维持单机（不扩容）
  步骤2: V2.0 GPU Whisper 服务独立部署（端口 9001）
  步骤3: M-014 REST 调用替换 M-005 内置 faster-whisper
  步骤4: 并发上限 3 → 5
[验证方法]
  - V1.1 压测 3 并发通过
  - V2.0 GPU 服务 P99 < 10s
[回滚策略]
  - V2.0 GPU 失败 → 关闭 M-014，回退 M-005 内置
  - 预计回滚时间: < 10 分钟（开关级）
[来源标注] [TD:PC-003] [TD:ER V-003] [TD:ADR-007]
```

---

## 性能策略总评

- **5/5 性能指标全部含优化策略**（D6 覆盖 100%）
- **所有策略可落地**（基于现有技术栈 TS-001~TS-022）
- **所有策略含回滚**（符合 soul 4.2 约束）
- **演进路径清晰**（V1.1 → V2.0 → V2.5 性能演进）

---

> **本文件结束**。5 套性能优化策略完整覆盖，容量规划 V2.0 演进明确。
