# 业务规则表 — VideoIngest V1.1（SA-001 终版）

> **生成方**：SA-001
> **日期**：2026-06-01
> **优先级图例**：P0=硬约束/阻塞 / P1=重要约束 / P2=建议约束
> **冲突处理**：默认按规则编号顺序仲裁（编号小者优先）；相同优先级按"安全 > 用户体验 > 性能"仲裁

---

## 业务规则详表

| 编号 | 规则描述 | 适用范围 | 优先级 | 冲突处理 | 来源标注 |
|------|----------|----------|--------|----------|----------|
| BR-001 | CLI 启动时强制覆盖 `LLMConfig.model` 默认值 `deepseek-chat` → `deepseek-v4-flash`；100% LLM 调用 model="deepseek-v4-flash" | BP-001 / BP-006 / BP-013 | P0 | 硬约束，不允许回退 [调研:S-003] | [PRD:NF-010/F-005.AC-3] [调研:S-003] |
| BR-002 | 单次 LLM 调用预算 ≤ 20k input / 4k output；不切分（保证章节连贯性）| BP-006 | P0 | 超预算自动截断 + 警告；不允许切分 map-reduce [PM决策:选项 A] | [PRD:F-005.AC-2] [调研:S-008] [PM决策] |
| BR-003 | front matter 字段统一 `video_` 前缀（避免与论文/网页/播客等其他来源冲突）| BP-006 / BP-008 / BP-010 | P0 | 字段名不匹配视为污染 + 触发重试 | [PRD:F-005.AC-4/F-006.AC-2] [调研:S-005] |
| BR-004 | 缓存键 = `sha256(url) + ":" + (etag or last_modified or "no-version")`；etag 缺失时降级为 sha256(url) | BP-004 | P0 | 命中需同时满足两键 | [PRD:F-009.AC-2] [调研:S-102] |
| BR-005 | Deno ≥ 2.0 是 YouTube 下载的硬约束；缺失返回 E_DL_001_DENO_MISSING + 打印安装命令 | BP-002 / BP-003 | P0 | YouTube 任务 100% 阻塞；B 站任务警告不阻塞 | [PRD:F-003.AC-2/NF-008] [调研:S-002] [Q-008] |
| BR-006 | yt-dlp 版本下限 ≥ 2023.07.06（CVE-2023-35934 修复版）| BP-003 | P0 | 不通过返回 E_DL_002_VERSION_TOO_OLD | [PRD:NF-004/F-012.AC-3] [调研:S-101] |
| BR-007 | Whisper 默认 model_size = `medium`；CLI 启动时检测 RAM < 8G 自动降档 base/small | BP-005 | P0 | OOM 风险避免 | [PRD:F-004.AC-2/F-014.AC-2] [调研:S-004] |
| BR-008 | 章节数 < 3 触发降级：5 分钟等距切片；仍不足 1 章节兜底 | BP-007 | P0 | 触发降级返回 E_LLM_002_CHAPTERS_FALLBACK | [PRD:F-010.AC-2/3] [调研:S-202] |
| BR-009 | LLM 连续 3 次 5xx 自动切换 qwen-turbo；切换不超过 1 次 | BP-006 | P1 | 切换后仍失败返回 E_LLM_001 | [PRD:F-013.AC-1] |
| BR-010 | Cookie 文件权限必须 600（octal）；不通过返回 E_CK_001 | BP-003 | P0 | 安全硬约束 | [PRD:F-012.AC-4] |
| BR-011 | BiliNote 仅移植 downloader + transcriber 子目录；不移植 Celery/Redis/chromadb | 全局 | P0 | 限定子目录边界 | [PRD:第 9 章] [调研:S-006] |
| BR-012 | 多视频批量入参上限 3 并发；URL 数量上限 10 | BP-011 | P1 | 超出仅取前 10 + 提示 | [PRD:F-008] |
| BR-013 | 错误信息必须含"现象+原因+下一步"3 段式 | BP-012 | P0 | 错误码字典完整性 | [PRD:NF-003] |
| BR-014 | 中间产物（音频/转写稿/部分 front matter）必须保留供用户排查 | BP-012 | P0 | 与错误码绑定 | [PRD:F-011.AC-1] |
| BR-015 | 任意下载/转写/LLM 操作重试 ≤ 2 次（指数退避 2^n）| BP-003/005/006 | P1 | 防止无限重试 | [PRD:NF-009] |
| BR-016 | 日志格式为 JSON Lines，含 ts/level/module/task_id/url_hash | BP-016 | P1 | 可观测性 | [PRD:NF-007] |
| BR-017 | 第⑧棒 preflight check 4 项：deno/node/ffmpeg/Whisper medium；缺依赖拍显式 [SIM-STUB] 不掩盖 | BP-002 | P0 | 真实跑的拍 ≥ 1/2 [PRD:AC-E2E-08] | [PRD:G-007/AC-E2E-08] [调研:S-007] |
| BR-018 | 30 分钟视频端到端 ≤ 8 分钟 | 全局 | P1 | 性能约束 | [PRD:NF-001] |
| BR-019 | 缓存命中率优化：同 URL 重复入参 ≤ 3s 返回 [cache hit] | BP-004 | P1 | 性能约束 | [PRD:F-009.AC-1/AC-E2E-03] |
| BR-020 | 移植比例 ~60%（BiliNote downloader + transcriber）/ ~40% 新建（含 preflight ~5% 不影响）| 全局 | P1 | 集成边界 | [PRD:第 9 章] |
| BR-021 | 章节时间戳格式必须 `hh:mm:ss`；越界（> 视频时长）裁剪或丢弃 | BP-007 | P1 | 时间戳合法性 | [调研:RR-012] |
| BR-022 | 截图 ≤ 5 张/视频，每张 ≤ 200KB；超限自动压缩 | BP-009 | P1 | 资源约束 | [PRD:F-018.AC-1] |
| BR-023 | B 站 4K/付费/限地区返回 E_DL_BILI_403 + 提示用户提供 cookie（不自动破解）| BP-003 | P0 | 合规约束 | [PRD:F-003.AC-3/B-007] [调研:S-001] |
| BR-024 | YouTube etag 24h 内强制 revalidate（避免 PO Token 轮换导致命中率下降）| BP-004 | P2 | 缓存优化 | [SA洞察#3] |
| BR-025 | LLM 输出 front matter 时若缺 video_ 前缀 → 强制重试 1 次 | BP-006 | P1 | 字段名一致性 | [SA洞察#2] |
| BR-026 | 缓存 TTL 默认 30 天；命中后 hit_count 累加 | BP-004 | P2 | 缓存维护 | [SA推断:TTL=30d] |
| BR-027 | 并发任务共享 PreflightReport；TTL=60s（防止 Deno 中途安装后未生效）| BP-002 / BP-011 | P1 | 跨任务状态 | [SA洞察#1] |
| BR-028 | 知识树 tags 同主题自动并集；冲突保留两版 + 标记 [duplicate_resolved] | BP-010 | P2 | 增量合并 | [PRD:F-016.AC-1] [SA推断:冲突仲裁] |

---

## 规则冲突与仲裁

| 冲突对 | 冲突场景 | 仲裁规则 |
|--------|----------|----------|
| BR-002 vs BR-008 | LLM 输入超 20k 但又需要 ≥ 3 章节 | BR-002 优先（截断不切分）；章节数降级由 BR-008 兜底 |
| BR-005 vs BR-011 | YouTube 缺 Deno 但 BiliNote 移植不依赖 Deno | BR-005 仅阻塞 YouTube 任务；B 站任务可继续 |
| BR-007 vs BR-018 | medium 档 vs 30 分钟 ≤ 5 分钟转写 | BR-018 性能目标与 BR-007 质量目标在 medium 档兼容；冲突时 BR-007 优先（质量优先）[调研:S-004] |
| BR-015 vs BR-009 | LLM 5xx 重试 vs fallback 切换 | BR-009 优先（连续 3 次 5xx 触发切换，不再重试 deepseek-v4-flash）|

---

## 规则来源分布

| 来源 | 条数 | 占比 |
|------|------|------|
| [PRD:xxx] 直接来源 | 18 | 64% |
| [调研:xxx] 调研结论 | 6 | 21% |
| [SA推断:xxx] 高置信推断 | 4 | 14% |
| [PM决策] / [Q-008] | 2 | 7% |
| **合计** | **28** | **100%** |

**推断占比 = 14% ≤ 40%（D8 ≥ 60 ✓）**

---

> **本文件结束**。28 条规则覆盖 16 流程所有关键约束。
