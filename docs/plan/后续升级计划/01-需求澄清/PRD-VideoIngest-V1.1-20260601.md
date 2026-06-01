# PRD — VideoIngest V1.1（research-tool 视频摄入能力扩展 · 终版）

> **项目代号**：VideoIngest
> **版本**：V1.1（终版，由 V1.0 增量合并 RA-001 调研反馈修订）
> **日期**：2026-06-01
> **作者**：PM-001（产品经理 Agent）
> **上游输入**：RA-001 调研修订建议（12 条 = 3 major + 9 minor）
> **文档状态**：终版，可交付 SA-001 / DD-001 / 第⑦棒 / 第⑧棒
> **V1.0 → V1.1 变更说明**：本文档仅在 V1.0 基础上**增量合并 RA 修订建议**（无 blocking 项），保留 V1.0 全部条目并标注本轮采纳/驳回理由。

---

## 0. 来源标注图例

| 标注 | 含义 |
|------|------|
| `[用户原话]` | 来自用户原始需求字面摘录，未做语义改写 |
| `[PM推断:依据]` | PM 基于上下文的高置信推断，附推断依据 |
| `[V1.1采纳:S-NNN]` | V1.1 轮由 RA-001 建议并经 PM 采纳，写明建议编号 |
| `[V1.1驳回:S-NNN]` | V1.1 轮 RA 建议未被采纳，写明驳回理由 |
| `[V1.0→V1.1修订]` | 标识本轮相对于 V1.0 的变更点 |

---

## 1. 需求背景与目标（V1.0 保留）

### 1.1 背景

[用户原话] "让 research-tool 不仅能'搜文字'，还能'听视频'——用户粘贴一个 B站/YouTube 视频链接，系统自动把视频内容转写成结构化调研笔记，无缝接入现有知识树和报告生成流程。"

[用户原话] "建议集成 BiliNote 以复用其'听懂视频'能力。"

research-tool 当前调研管道由 Collect → Clean → Extract → Organize → Report 五阶段组成，数据源以纯文本/URL 爬取为主，缺乏"音视频源 → 文本 → 结构化笔记"的摄入路径。

BiliNote 是开源 AI 视频笔记工具，已实现"多平台链接 → 音视频下载 → 字幕/Whisper 转写 → LLM 总结 → Markdown 笔记"完整链路。

### 1.2 核心目标（V1.0 保留）

| 编号 | 目标 | 来源 |
|------|------|------|
| G-001 | CLI 支持 `research run --video-url <url>` 一键入管道 | [用户原话] |
| G-002 | 复用 BiliNote 的 downloader + transcriber（约 60% 代码） | [用户原话] |
| G-003 | 视频产物以标准 Markdown+YAML front matter 写入 `raw/`，**下游五阶段管道零改动** | [PM推断:依据] |
| G-004 | 全链路使用 `deepseek-v4-flash` 模型 | [用户原话]硬约束 1 |
| G-005 | 第⑦棒只产空文件骨架，第⑧棒真实模拟 8~15 拍 + 6 份文件 | [用户原话]硬约束 2 & 3 |
| G-006 | RA 调研阶段用 research-tool 真实运行 ≥4 次 | [用户原话]硬约束 5 |
| G-007 **[V1.1新增:S-007]** | 第⑧棒启动前 preflight check：deno/node/ffmpeg/Whisper 模型 4 项 | [V1.1采纳:S-007]（依据风险报告 1.4+3.1 段） |
| G-008 **[V1.1新增:S-002]** | YouTube 下载必须先确认 Deno ≥ 2.0 已安装 | [V1.1采纳:S-002]（依据 yt-dlp 2025-09-23 公告） |

### 1.3 成功指标（V1.0 保留，V1.1 增 E2E-08）

| 编号 | 指标 | 目标值 | 测量方式 | 来源 |
|------|------|--------|----------|------|
| SI-001 | B 站公开视频转写成功率 | ≥ 80%（[V1.1采纳:S-001]） | RA 跑 10 样本 | [V1.1采纳:S-001] 实测约 80% |
| SI-002 | YouTube 公开视频转写成功率 | ≥ 85% | RA 跑 10 样本 | [V1.0] |
| SI-003 | 30 分钟视频端到端延迟 | ≤ 8 分钟 | RA 端到端 | [V1.0] |
| SI-004 | 调研管道接入测试通过率 | 100% | CI | [V1.0] |
| SI-005 | LLM 调用 `deepseek-v4-flash` 一致性 | 100% | 日志检索 | [V1.0] |
| SI-006 | 第⑧棒端到端模拟跑通 | 8~15 拍 | 运行报告 | [V1.0] |
| SI-007 **[V1.1新增:S-007]** | 8~15 拍中真实跑的拍 ≥ 1/2；缺依赖拍显式 `[SIM-STUB]` | 100% 合规 | 产物日志审计 | [V1.1采纳:S-007] |

---

## 2. 用户角色定义（V1.0 保留）

| 编号 | 角色 | 核心诉求 |
|------|------|----------|
| U-001 | 一线研究员 | 一行命令把视频变笔记 |
| U-002 | 技术调研分析师 | 视频内容与已有知识树无缝合并 |
| U-003 | 内容策展人 | 摘要质量、结构稳定 |
| U-004 | 系统维护者 | 集成边界清晰，移植 vs 新建明确 |

---

## 3. 功能需求清单（V1.1 增量合并）

> 优先级定义：P0 ≤30% / P1 20-35% / P2 20-30% / P3 ≥10%
> 总计：P0=6（30%）/ P1=6（30%）/ P2=5（25%）/ P3=3（15%）= 20 项，**P0 占比 30% 仍合规**。
> 增量变更：F-003/F-005/F-006/F-012/F-014 验收标准细化；F-014 增加 `--transcriber-size`；F-018 增加 ffmpeg 技术细节；新增错误码 E_DL_001_DENO_MISSING、E_DL_BILI_403、E_DL_002_VERSION_TOO_OLD、E_LLM_002_CHAPTERS_FALLBACK。

### 3.1 P0 核心必须

| 编号 | 功能名 | 描述 | 用户 | 验收标准 | 依赖 | 冲突边界 | V1.1 变更 |
|------|--------|------|------|----------|------|----------|-----------|
| F-001 | CLI 视频链接入参 | `--video-url` 支持 B 站/YouTube | U-001,U-002 | 合法 BV/watch URL，2s 内进入下载 | 无 | 抖音等 v1.1 | — |
| F-002 | 平台识别器 | 自动识别 bilibili/youtube | U-001 | 5 种 URL 形态识别 100% | F-001 | 抖音降级 v1.1 | — |
| F-003 | BiliNote downloader 移植 | yt-dlp+ffmpeg 下载/字幕 | U-004 | (1) 公开视频 10 分钟内下载成功<br>(2) **[V1.1新增:S-002]** preflight 检查 `deno --version` ≥ 2.0；缺失返回 `E_DL_001_DENO_MISSING` 并提示安装<br>(3) **[V1.1新增:S-001]** 4K/付费/限地区视频返回 `E_DL_BILI_403` 并提示用户提供 cookie | F-002 | 仅公开视频 | F-003 增 2 条验收 + 2 错误码 |
| F-004 | BiliNote transcriber 移植 | fast-whisper 转写 | U-004 | (1) 30 分钟视频 ≤ 5 分钟完成<br>(2) **[V1.1新增:S-004]** **默认 model_size = `medium`**（BiliNote v2.2 默认 tiny 系性能妥协，本集成优先质量）<br>(3) 二次入参命中缓存 | F-003 | — | F-004 默认档 tiny→medium |
| F-005 | LLM 总结为 Markdown+YAML | deepseek-v4-flash 总结 | U-002,U-003 | (1) 30 分钟转写稿 ≤ 60s<br>(2) front matter 字段齐全，YAML 可解析<br>(3) **[V1.1采纳:S-008]** 单次调用 ≤ **20k input / 4k output**（30 分钟视频典型 8-15k 转写稿 + 4k prompt ≈ 12-20k input）<br>(4) **[V1.1采纳:S-003]** CLI 启动时强制覆盖 `LLMConfig.model` 默认值 `deepseek-chat` → `deepseek-v4-flash`<br>(5) **[V1.1新增:S-005]** front matter 字段统一加 `video_` 前缀 | F-004 | — | F-005 增 3 条验收（单次预算/模型覆盖/video_ 前缀） |
| F-006 | 管道适配器 | 落 `raw/<topic>/<video-id>.md`，触发五阶段管道 | U-001,U-002 | (1) Markdown 落 raw/ 后 5 阶段自动跑完<br>(2) **[V1.1新增:S-005]** 字段命名空间隔离：`video_*` 前缀避免与未来其他来源（论文/网页/播客）冲突 | F-005 | 下游零修改 | F-006 增 1 条验收 |

### 3.2 P1 重要必需

| 编号 | 功能名 | 描述 | 用户 | 验收标准 | 依赖 | 冲突边界 | V1.1 变更 |
|------|--------|------|------|----------|------|----------|-----------|
| F-007 | 本地视频文件入参 | `--video-file` 支持 mp4/mkv/mov | U-001 | 1GB 文件 30 分钟内完成 | F-004,F-005 | 不支持 m3u8 | — |
| F-008 | 多视频批量入参 | `--video-url url1,url2,url3` 3 并发 | U-002 | 3 个 30 分钟视频 ≤ 12 分钟 | F-006 | >10 个 v1.1 队列 | — |
| F-009 | 缓存命中 | 重复入参 ≤ 3s | U-001 | (1) `[cache hit]` 日志<br>(2) **[V1.1采纳:S-102]** 缓存键 = `sha256(url) + etag_or_last_modified`（避免作者更新内容后命中陈旧笔记）<br>(3) **[V1.1新增:S-102]** CLI 增加 `--no-cache` 强制刷新 | F-006 | — | F-009 缓存键升级 + `--no-cache` |
| F-010 | 章节时间戳 | front matter `chapters[]` + 正文 `[hh:mm:ss]` 锚点 | U-003 | (1) 章节数 ≥ 3<br>(2) **[V1.1采纳:S-202]** **降级策略**：`chapters < 3` 时按 5 分钟等距切片；仍不足则 1 章节兜底<br>(3) **[V1.1新增:S-202]** 触发降级时返回 `E_LLM_002_CHAPTERS_FALLBACK` | F-005 | — | F-010 增降级策略 |
| F-011 | 错误降级 | E_DL/E_TR/E_LLM 错误码 + 中间产物 | U-001 | 错误码 100% 可解析 | F-003~F-005 | 不重试 >2 次 | — |
| F-012 | Cookie 注入 | `--cookie-file` 突破登录视频 | U-001,U-004 | (1) 合法 cookie 10 分钟视频获取成功<br>(2) **[V1.1采纳:S-001]** **默认提供 `cookies.txt` 模板**（含空占位），用户填空即用<br>(3) **[V1.1采纳:S-101]** CLI 启动时校验 `yt-dlp --version` ≥ **2023.07.06**（CVE-2023-35934 修复版），不通过返回 `E_DL_002_VERSION_TOO_OLD`<br>(4) cookie 文件权限 600 校验 | F-003 | — | F-012 增 cookie 模板 + 版本下限 |

### 3.3 P2 期望增强

| 编号 | 功能名 | 描述 | 用户 | 验收标准 | 依赖 | V1.1 变更 |
|------|--------|------|------|----------|------|-----------|
| F-013 | 多 LLM 供应商回退 | deepseek-v4-flash 5xx 时回退 qwen-turbo | U-004 | 连续 3 次 5xx 自动切换 | F-005 | — |
| F-014 | 转写引擎切换 | `--transcriber {whisper\|bcut\|groq}` | U-002 | (1) 三选项均能完成<br>(2) **[V1.1新增:S-004]** 新增 `--transcriber-size {tiny\|base\|small\|medium\|large}` 切档；CLI 启动时检测 RAM < 8G 自动降档 base/small | F-004 | F-014 增 `--transcriber-size` |
| F-015 | 总结风格预设 | `--style {academic\|casual\|keypoints}` | U-003 | 3 风格差异化 | F-005 | — |
| F-016 | 增量知识树合并 | tags 去重合并 | U-002 | tags 自动并集 | F-006 | — |
| F-017 | 视频元信息抓取 | 标题/作者/封面/简介/时长/标签 | U-001 | front matter ≥ 6 字段 | F-005 | — |

### 3.4 P3 锦上添花

| 编号 | 功能名 | 描述 | 用户 | 验收标准 | 依赖 | V1.1 变更 |
|------|--------|------|------|----------|------|-----------|
| F-018 | 截图采样 | 关键帧嵌入 Markdown | U-003 | (1) 30 分钟 ≤ 5 张 ≤ 200KB<br>(2) **[V1.1采纳:S-201]** ffmpeg `-vf select='eq(pict_type,I)' -vsync vfr -frames:v 5` | F-005 | F-018 增技术细节 |
| F-019 | AI 问答 RAG | `research ask` 引用视频 | U-002 | CLI 返回基于本次视频的答案 | F-016 | — |
| F-020 | 报告段落自动引用 | 报告参考来源含视频链接 | U-003 | 报告 `## 参考来源` 含原始链接 | F-006 | — |

---

## 4. 非功能需求（V1.1 增 2 条）

| 编号 | 类别 | 指标 | 量化值 | V1.1 变更 |
|------|------|------|--------|-----------|
| NF-001 | 性能 | 单视频端到端延迟 | 30 分钟 ≤ 8 分钟 | — |
| NF-002 | 性能 | 并发吞吐 | 3 并发 / 8 核 16G | — |
| NF-003 | 可用性 | 错误信息可读性 | 含"现象+原因+下一步"3 段 | — |
| NF-004 | 安全 | Cookie 文件权限 | 600 + **[V1.1新增:S-101]** yt-dlp ≥ 2023.07.06 | NF-004 增 yt-dlp 版本下限 |
| NF-005 | 安全 | API Key 存储 | 仅 `.env` / sqlite | — |
| NF-006 | 可维护性 | 模块边界 | `docs/MODULE_MAP.md` 100% 标注 | — |
| NF-007 | 可观测性 | 日志结构 | JSON Lines 含 ts/level/module/task_id/url_hash | — |
| NF-008 | 兼容性 | 三平台 | **[V1.1新增:S-002]** Win11 / macOS 14+ / Ubuntu 22.04+ **预装/需自装 Deno ≥ 2.0** | NF-008 增 Deno 文档化 |
| NF-009 | 鲁棒性 | 网络抖动 | 重试 2 次，指数退避 | — |
| NF-010 | 合规 | 模型一致 | **[V1.1采纳:S-003]** 100% LLM 调用 `model = "deepseek-v4-flash"`（CLI 启动时强制覆盖 research-tool 既有 `LLMConfig.model = "deepseek-chat"` 默认值） | NF-010 显式声明模型名 |

---

## 5. 功能边界（不做什么，V1.0 保留）

| 编号 | 不做项 | 原因 |
|------|--------|------|
| B-001 | 抖音/快手/小宇宙/西瓜视频 | 反爬严、Cookie 政策变化频繁，v1.1 评估 |
| B-002 | 直播流（m3u8/RTMP）实时转写 | 与离线批处理冲突 |
| B-003 | 多模态视频帧理解 | deepseek-v4-flash v1.0 限定文本 |
| B-004 | BiliNote 浏览器插件 | CLI 定位 |
| B-005 | BiliNote 前端/桌面 | CLI 路线 |
| B-006 | 视频剪辑/合并/转码 | 超出"摄入"范围 |
| B-007 | 付费视频/会员视频破解 | 合规风险 |
| B-008 | 视频笔记人工编辑界面 | CLI 定位 |
| B-009 | 自动上传 Notion/飞书 | v1.1 "导出器"规划 |
| B-010 | 字幕/视频多语言实时翻译 | LLM 后处理覆盖 |

---

## 6. 验收标准（V1.1 增 AC-E2E-08）

### 6.1 端到端必跑（V1.0 准入，V1.1 增 1 条）

1. **AC-E2E-01**：B 站公开带字幕视频，8 分钟内出 `reports/demo.md`，含视频 front matter + ≥3 章节。
2. **AC-E2E-02**：YouTube 公开无字幕视频，12 分钟内完成转写+总结+报告。
3. **AC-E2E-03**：同 URL 第二次入参 3 秒内 `[cache hit]`。
4. **AC-E2E-04**：非法 URL 1 秒内返回 E_DL_001（含"现象+原因+下一步"）。
5. **AC-E2E-05**：Whisper 不可用时返回 E_TR_001，中间产物保留。
6. **AC-E2E-06**：100% LLM 调用 `model = "deepseek-v4-flash"`。
7. **AC-E2E-07**：RA 真实跑 4+ 次并逐条核对假设。
8. **AC-E2E-08 [V1.1新增:S-007]**：第⑧棒 8~15 拍启动前 preflight check 4 项（deno/node/ffmpeg/Whisper medium）全部通过；缺依赖拍在产物日志显式标注 `[SIM-STUB]`，**不掩盖**；真实跑的拍 ≥ 1/2。

### 6.2 第⑧棒系统模拟（V1.1 增 preflight 子条目）

- 输入：`research run "技术大会 talk 调研" --video-url "https://www.bilibili.com/video/BVxxxxxxxxx"`
- 启动前 preflight：
  1. `deno --version` ≥ 2.0（缺则 `[SIM-STUB]`）
  2. `node --version` ≥ 18（缺则 `[SIM-STUB]`）
  3. `ffmpeg -version` ≥ 6.0（缺则 `[SIM-STUB]`）
  4. `~/.cache/huggingface/hub/models--Systran--faster-whisper-medium` 存在（缺则 `[SIM-STUB]`）
- 8~15 拍端到端串起 CLI→模块→数据流→产物
- 6 份文件落 `产出物/08-系统模拟运行/`

---

## 7. 待确认项（V1.0 7 条 → V1.1 全部闭环）

| 编号 | 开放问题 | V1.0 假设 | V1.1 闭环结果 | 来源 |
|------|----------|-----------|----------------|------|
| Q-001 | B 站反爬与登录墙 | 默认可下 | 风险等级"中" → **[V1.1采纳:S-001]** 维持中，约 80% 可下；F-012 默认 cookie 模板 | R-001 调研 |
| Q-002 | deepseek-v4-flash context | 64k | **[V1.1采纳:S-003]** 风险等级"高"→"低"，实测支持 1M context（远超 30 分钟视频 8-15k 转写稿） | R-003 调研 |
| Q-003 | Whisper 中文 CER | ≥ 85% | **[V1.1采纳:S-004]** 风险等级"中"→"低"，标准普通话 CER 4-8%（92-96% 准确率） | R-004 调研 |
| Q-004 | raw/ schema 冲突 | 不冲突 | **[V1.1采纳:S-005]** 风险等级"中"→"低"，字段加 `video_` 前缀隔离 | R-005 调研 |
| Q-005 | M-NNN 编号 | 按 4 类编号 | **[V1.1部分采纳]** 默认按 4 类编号；最终由 SA/DD-001 在下游棒产出 | R-007 调研 |
| Q-006 | 缓存粒度 | URL hash | **[V1.1采纳:S-102]** 升级为 `sha256(url) + etag/last-modified` 双键 | R-102 调研 |
| Q-007 | LLM 单次调用预算 | ≤ 2k input | **[V1.1采纳:S-008]** ⚠️ **PM 决策（选项 A：单次整合）**：扩为 ≤ 20k input / 4k output；30 分钟视频典型 8-15k 转写稿 + 4k prompt ≈ 12-20k input；分段 map-reduce 不采纳（章节连贯性优先） | R-103 调研 |

**V1.1 新增 1 条待确认项**（由 S-002 引入）：

| 编号 | 开放问题 | 默认假设 | 风险等级 | 关联功能 | 来源 |
|------|----------|----------|----------|----------|------|
| Q-008 [V1.1新增:S-002] | Deno 运行时是否已在用户机器预装？ | 默认不预装；CLI 启动时检查并打印安装命令 | **高** | F-003, NF-008 | [V1.1采纳:S-002] |

**V1.1 待确认项总数**：1 条（Q-008，由 V1.0 的 7 条闭环 + V1.1 新增 1 条），≤10 合规。

---

## 8. 作用域变更记录（V1.1 增 2 次）

| 轮次 | 变更类型 | 变更内容 | 影响 | 来源 |
|------|----------|----------|------|------|
| 0 | 初始化 | 用户原始需求 | 起点 | [用户原话] |
| 1 | [PM推断]收窄 | 视频源限定 B 站/YouTube | B-001 锁定 | [PM推断] |
| 2 | [PM推断]收窄 | 不做 GUI（沿用 CLI） | B-005 锁定 | [PM推断] |
| 3 | [PM推断]收窄 | LLM 统一 deepseek-v4-flash | F-005/F-013 受限 | [用户原话] |
| 4 | [PM推断]收窄 | 第⑦棒只产空文件骨架 | DD-001 输出物限定 | [用户原话] |
| 5 | [PM推断]扩大 | 纳入第⑧棒端到端模拟 | G-005 落地 | [用户原话] |
| 6 | [PM推断]收窄 | BiliNote 集成边界 = downloader + transcriber | 60% 移植 / 40% 新建 | [用户原话] |
| **7** [V1.1新增:S-002] | **[PM+RA共识]扩大** | **YouTube 下载需 Deno 运行时**（v1.0→v1.1 新硬约束） | **F-003 增 E_DL_001_DENO_MISSING；NF-008 文档化** | [V1.1采纳:S-002] |
| **8** [V1.1新增:S-007] | **[PM+RA共识]扩大** | **第⑧棒 8~15 拍启动前 preflight check 4 项 + 缺依赖拍显式 [SIM-STUB]** | **G-007 + AC-E2E-08 + 风险 RR-010** | [V1.1采纳:S-007] |

> D7 作用域稳定性 = 100%（变更全部发生在 L0→L1 启动阶段 + V1.1 启动期，**无 L1+ 阶段反复摇摆**）。

---

## 9. 集成边界（BiliNote 移植 vs research-tool 新建）— V1.1 限定子目录

> 满足 [用户原话]硬约束 4 + [V1.1采纳:S-006]（限定子目录范围）。

| 模块 | 来源 | 代码量 | 接口契约 | V1.1 变更 |
|------|------|--------|----------|-----------|
| `downloader.bilibili` | **[V1.1采纳:S-006] 移植 BiliNote `backend/app/services/downloader/bilibili.py`** | ~800 行 | `download(url, cookie) -> Path` | 限定子目录路径 |
| `downloader.youtube` | **[V1.1采纳:S-006] 移植 BiliNote `backend/app/services/downloader/youtube.py`** | ~300 行 | `download(url, cookie) -> Path` | 限定子目录路径 |
| `transcriber.whisper` | **[V1.1采纳:S-006] 移植 BiliNote `backend/app/services/transcriber/whisper.py`** | ~600 行 | `transcribe(audio_path, model_size='medium') -> List[Segment]` | 限定子目录路径 + 默认 medium |
| `transcriber.bcut` | **[V1.1采纳:S-006] 移植 BiliNote `backend/app/services/transcriber/bcut.py`** | ~400 行 | `transcribe(audio_path) -> List[Segment]` | 限定子目录路径 |
| `transcriber.subtitle` | **[V1.1采纳:S-006] 移植 BiliNote `backend/app/services/transcriber/subtitle.py`** | ~200 行 | `fetch_subtitle(url) -> Optional[List[Segment]]` | 限定子目录路径 |
| `llm_client` | **新建** | ~400 行 | 替换 BiliNote SQLite 配置为 `.env`；CLI 启动强制 `model=deepseek-v4-flash` | — |
| `video_ingestor` | **新建** | ~500 行 | 编排 downloader→transcriber→llm 串行流 | — |
| `pipeline_adapter` | **新建** | ~250 行 | 落 `raw/<topic>/<video-id>.md` 触发既有管道 | — |
| `cache_manager` | **新建** | ~300 行 | 缓存键 = `sha256(url) + etag/last_modified` | V1.1 缓存键升级 |
| `cli_bindings` | **新建** | ~300 行 | 解析 `--video-url` / `--video-file` / `--cookie-file` / `--transcriber-size` / `--no-cache` 等 | V1.1 增 `--transcriber-size` / `--no-cache` |
| `preflight` | **[V1.1新增:S-007] 新建** | ~150 行 | 检查 deno/node/ffmpeg/Whisper 模型 4 项 | V1.1 新模块 |
| `notes_schema` | **新建** | ~150 行 | 字段集：`video_id / video_source_url / video_title / video_author / video_duration / video_cover_url / video_tags / video_chapters[] / video_created_at` | V1.1 字段加 `video_` 前缀 |
| BiliNote 前端/桌面 | **不移植** | — | — | — |
| BiliNote `tasks/db/vector_store/routers` | **[V1.1采纳:S-006] 不移植** | — | research-tool 无 Celery/Redis 栈 | 显式跳过 |

**移植比例**：~60%（BiliNote downloader + transcriber 子目录）；**新建比例**：~40%（含新增 preflight 模块）。**接口契约核心**：
1. `transcribe(audio_path, model_size='medium') -> List[Segment] {start, end, text}`；
2. `notes_schema` 字段集统一 `video_` 前缀；
3. 既有五阶段管道 **零修改**，仅在 Collect 阶段新增"raw/ 下发现 video_*.md 即纳入"1 行配置；
4. **[V1.1采纳:S-006] 锁版本下限**：`yt-dlp>=2025.03.31, faster-whisper>=1.1.1, ffmpeg-python>=0.2.0, youtube-transcript-api>=1.0.0`，**Python ≥ 3.11**；
5. **[V1.1采纳:S-002] 前置依赖**：`deno >= 2.0`（仅 YouTube 必需，缺失返回 E_DL_001_DENO_MISSING）。

---

## 10. 假设日志（V1.1 更新假设状态）

| 编号 | 假设 | V1.0 状态 | V1.1 状态 | 失败回退 | 来源 |
|------|------|-----------|-----------|----------|------|
| A-001 | B 站公开视频可下载/取字幕 | TODO | ✅ 实测约 80% 可下；F-012 默认 cookie 模板 | 转 F-012 强制 Cookie | [V1.1采纳:S-001] |
| A-002 | YouTube 公开视频可下载 | TODO | ⚠️ **必装 Deno ≥ 2.0**（2025-09-23 yt-dlp 公告） | 改 cookies-from-browser 或 v1.0 砍 YouTube | [V1.1采纳:S-002] |
| A-003 | fast-whisper medium 中文 ≥ 85% | TODO | ✅ 标准普话 CER 4-8%（92-96% 准确率） | 切 Groq API | [V1.1采纳:S-004] |
| A-004 | deepseek-v4-flash 支持 64k context | TODO | ✅ **实测支持 1M context**（远超需求） | 切分 8k 块 map-reduce | [V1.1采纳:S-003] |
| A-005 | research-tool 现有 raw/ 接纳新 front matter | TODO | ✅ `yaml.safe_load` 宽容未识别字段；建议 `video_` 前缀 | 双前缀隔离 | [V1.1采纳:S-005] |
| A-006 | BiliNote 移植模块与 research-tool 依赖兼容 | TODO | ✅ 核心依赖完全兼容（同 Pydantic v2 / Python 3.11+ / faster-whisper==1.1.1 / yt-dlp==2025.3.31）；跳过 Celery/Redis 栈 | 抽离为 standalone package | [V1.1采纳:S-006] |
| A-007 [V1.1新增:S-007] | 第⑧棒 preflight check 4 项可全部命中 | NEW | ⚠️ 风险 RR-010（高）—— 用户机器可能缺 Deno/Node/ffmpeg/Whisper 模型；缺则 `[SIM-STUB]` 不掩盖 | 模拟桩 + 显式标注 | [V1.1采纳:S-007] |
| A-008 [V1.1新增:S-008] | LLM 单次调用 ≤ 20k input 可控成本与延迟 | NEW | ✅ 按 deepseek-v4-flash 价（$0.28/百万 token 输出）单次成本 ≈ $0.0011；20 次/天 ≈ $0.20 | 切分段 map-reduce | [V1.1采纳:S-008]（PM 决策选项 A） |

---

## 11. 风险评估（V1.1 增 RR-010）

| 编号 | 风险 | 概率 | 影响 | 缓解 | V1.1 变更 |
|------|------|------|------|------|-----------|
| R-001 | B 站/YouTube 反爬升级 | **[V1.1采纳:S-002] 中 → 高** | 高 | Cookie 注入 + 重试 + Deno 必装 + 错误码 | R-001 升级为高 |
| R-002 | Whisper 大模型 OOM | 中 | 中 | **[V1.1采纳:S-004] 默认 medium；RAM < 8G 自动降档 base/small** | R-002 缓解细化 |
| R-003 | deepseek-v4-flash 总结质量不达预期 | **[V1.1采纳:S-003] 中 → 低** | 中 | 实测 1M context；F-013 供应商回退 | R-003 降级 |
| R-004 | 缓存命中导致陈旧笔记 | 低 | 中 | **[V1.1采纳:S-102] 缓存键 = sha256(url) + etag/last-modified** | R-004 缓解升级 |
| R-005 | 移植 BiliNote 引入未维护依赖 | 中 | 中 | **[V1.1采纳:S-006] 锁版本下限 + 抽离为独立子包** | R-005 缓解细化 |
| R-006 | 第⑧棒 8~15 拍无法全部跑通（环境缺） | 中 | 中 | **[V1.1采纳:S-007] preflight check 4 项 + 缺依赖显式 `[SIM-STUB]` 不掩盖** | R-006 缓解升级 |
| **RR-010** [V1.1新增:S-007] | **第⑧棒 preflight 4 项全部缺失导致全部拍均为 `[SIM-STUB]`** | **中** | **高** | **(1) preflight 提前 1 拍报告环境状态；(2) 至少保证 1/2 拍真实跑；(3) 模拟产物显式标注可追溯** | **V1.1 新风险** |

---

## 12. 交付物清单（本棒 + 下游）

### 12.1 本棒（PM-001）已落盘

- `产出物/01-需求澄清/PRD-VideoIngest-V1.0-20260601.md`（V1.0 初版归档）
- `产出物/01-需求澄清/PRD-VideoIngest-V1.1-20260601.md`（**本文档，终版**）
- `产出物/01-需求澄清/调研需求清单.md`（V1.1 增量更新）
- `产出物/01-需求澄清/需求追溯矩阵.md`（V1.1 增量更新）
- `产出物/01-需求澄清/待确认项清单.md`（V1.0 7 条全闭环 + V1.1 新增 Q-008）
- `产出物/01-需求澄清/原始需求记录.md`（V1.0 保留）
- `产出物/01-需求澄清/对齐过程记录.md`（V1.1 最终 CCI）
- `产出物/01-需求澄清/backlog.md`（V1.1 增量）
- `产出物/01-需求澄清/作用域变更轨迹.md`（V1.1 增 Round 7/8）

### 12.2 下游接收方

- **RA-001（已闭环）**：所有 13 条调研任务已 RA 调研完成；本轮无新增调研任务
- **SA-001（系统分析师）**：以本 PRD V1.1 为输入做架构/接口/数据流设计；重点关注 NF-008（Deno 必装）、第 9 章接口契约、preflight 模块
- **DD-001（详细设计）**：M-NNN 模块划分需包含 preflight 子模块 M-NNN（具体编号由 SA 给出）
- **第⑦棒 文件框架**：仅产空文件骨架（不写实现代码）；空骨架需含 preflight 模块占位
- **第⑧棒 系统模拟**：跑 `research run "技术大会 talk 调研" --video-url "<BV id>"`；**启动前 preflight check 4 项 + 缺依赖 `[SIM-STUB]` 显式标注**

---

## 13. CCI 七维评分（V1.1 终版）

### 13.1 各维度分项得分

| 维度 | 名称 | V1.0 得分 | V1.1 得分 | 满分 | 占比 | 权重 | 贡献 | 差距依据（V1.1） |
|------|------|-----------|-----------|------|------|------|------|------------------|
| D1 | 需求完整度 | 100 | **100** | 100 | 100% | 0.25 | 0.250 | 20 功能全映射；V1.1 增 preflight 模块 + 4 错误码，无新增功能缺失 |
| D2 | 需求清晰度 | 90  | **95** | 100 | 95% | 0.20 | 0.190 | V1.1 后 20 功能均含 ≥1 验收；P3 描述仍略宽但已增技术细节（ffmpeg 参数） |
| D3 | 边界明确度 | 95  | **95** | 100 | 95% | 0.20 | 0.190 | 10 条不做 + 4 条新错误码边界（E_DL_001_DENO/E_DL_BILI_403/E_DL_002_VERSION/E_LLM_002_CHAPTERS） |
| D4 | 用户确认覆盖度 | 75  | **90** | 100 | 90% | 0.15 | 0.135 | V1.0 7 条 [PM推断] 经 RA 调研全闭环；V1.1 新增 Q-008（Deno 预装）1 条 |
| D5 | 优先级排序完备度 | 100 | **100** | 100 | 100% | 0.05 | 0.050 | 20 条均含 P0-P3；P0=6(30%) 合规 |
| D6 | 验收标准明确度 | 92  | **95** | 100 | 95% | 0.10 | 0.095 | V1.1 8 条 E2E（+AC-E2E-08）；F-003/F-004/F-005/F-006/F-009/F-010/F-012/F-014/F-018 均增具体验收 |
| D7 | 作用域稳定性 | 100 | **100** | 100 | 100% | 0.05 | 0.050 | V1.0 6 次 + V1.1 2 次变更**全部在 L0→L1 启动阶段**；无 L1+ 阶段反复摇摆 |
| **CCI** | **综合收敛指数** | **0.925** | — | — | — | **1.00** | **0.960** | **通过 ≥0.90 交付线，提升 0.035** |

### 13.2 最弱维度

- **D4 用户确认覆盖度 = 0.90**（V1.1 提升 0.15，仍为最弱但已 ≥0.90）
- 差距依据：V1.1 新增 Q-008（Deno 是否预装）需用户在 CLI 首次启动时确认
- 缓解：CLI 启动时自动检测 + 打印安装命令 + E_DL_001_DENO_MISSING 错误码

### 13.3 交付门禁（V1.1 终版）

- [x] 功能点完整覆盖 100%
- [x] P0 占比 30%（≤30% 合规）
- [x] P0/P1 全部含验收标准（含 V1.1 新增细节）
- [x] "不做什么" 10 条
- [x] 待确认项 1 条（Q-008，≤10 合规）
- [x] 总功能点 20（≤30）
- [x] **CCI = 0.960 ≥ 0.90（V1.0 0.925 → V1.1 0.960，提升 0.035）**
- [x] 无模糊验收标准
- [x] 作用域变更记录完整（V1.0 6 + V1.1 2 = 8 次）
- [x] 错误码字典完整（V1.0 3 类 + V1.1 新增 4 码）
- [x] BiliNote 移植子目录明确（第 9 章限定）
- [x] Deno 必装硬约束显式（NF-008 + F-003 + Q-008）
- [x] preflight 模块显式（G-007 + AC-E2E-08 + preflight 子模块）

**门禁结论**：通过交付。**最终 CCI = 0.960**（供下游 RA-001（已闭环）/ SA-001 / DD-001 / 第⑦棒 / 第⑧棒 质量门禁校验）。

---

## 14. V1.0 → V1.1 修订采纳/驳回记录（透明化追溯）

| 修订建议 | 指向条目 | 等级 | PM 决策 | 采纳/驳回理由 | 文档落点 |
|----------|----------|------|---------|---------------|----------|
| S-001 | F-003 / F-012 / Q-001 | minor | ✅ 采纳 | 实测 80% 可下，默认 cookie 模板提升可用性 | F-003 验收 + F-012 验收 + Q-001 闭环 |
| S-002 | F-003 / NF-008 / R-001 | **major** | ✅ 采纳 | yt-dlp 2025-09-23 公告确认 Deno 必装；用户门槛+1 但与"必做 YouTube"成正比 | F-003 增 E_DL_001_DENO_MISSING + NF-008 增 Deno + R-001 升级为高 + Q-008 新增 |
| S-003 | F-005 / NF-010 / Q-002 | minor | ✅ 采纳 | 实测 1M context（远超需求 15x）；研究工具默认 deepseek-chat 需被覆盖 | F-005 增验收 + NF-010 显式 model 名 + Q-002 闭环降为低 |
| S-004 | F-004 / F-014 / Q-003 | minor | ✅ 采纳 | 实测标准普话 CER 4-8% 远超 85% 目标；medium 优先质量；RAM < 8G 降档 | F-004 默认 medium + F-014 增 `--transcriber-size` + Q-003 闭环降为低 + R-002 缓解细化 |
| S-005 | F-006 / notes_schema / Q-004 | minor | ✅ 采纳 | `yaml.safe_load` 宽容未识别字段；显式 `video_` 前缀防误读 | 第 9 章字段集 + F-006 增验收 + Q-004 闭环降为低 |
| S-006 | 第 9 章 / A-006 | minor | ✅ 采纳 | 核心依赖完全兼容；限定子目录避免拉入 Celery/Redis 栈 | 第 9 章限定子目录 + 锁版本下限 + Python ≥ 3.11 |
| S-007 | G-005 / AC-E2E / R-007 | **major** | ✅ 采纳 | preflight check 4 项 + `[SIM-STUB]` 显式标注保证产物可追溯 | G-007 + AC-E2E-08 + 第 11 章 preflight 子模块 + RR-010 新风险 |
| S-008 | Q-007 / F-005 | **major** | ✅ 采纳 | ⚠️ **PM 决策选项 A（单次整合）**：30 分钟视频 8-15k 转写稿 + 4k prompt ≈ 12-20k input，远超原 2k 假设 6-10x；扩为 ≤ 20k input / 4k output；分段 map-reduce 不采纳（章节连贯性优先） | F-005 增验收 + Q-007 闭环 + A-008 新假设 |
| S-101 | F-012 / NF-004 | minor | ✅ 采纳 | CVE-2023-35934 必须 yt-dlp ≥ 2023.07.06；轻量影响 | NF-004 增 yt-dlp 版本下限 + F-012 增验收 + E_DL_002_VERSION_TOO_OLD |
| S-102 | F-009 / Q-006 | minor | ✅ 采纳 | 单纯 URL hash 无法感知作者更新；etag/last-modified 双键解决 | F-009 增验收 + `--no-cache` + Q-006 闭环 + R-004 缓解升级 |
| S-201 | F-018 | minor | ✅ 采纳 | ffmpeg `-vf select=eq(pict_type,I)` 已是业界标准 | F-018 增技术细节 |
| S-202 | F-010 / R-202 | minor | ✅ 采纳 | 章节 < 3 时降级等距切片；保证结构稳定 | F-010 增降级策略 + E_LLM_002_CHAPTERS_FALLBACK |

**V1.1 修订采纳率**：12/12 = **100%**（无驳回；RA 所有建议均纳入）；3 条 major 全部采纳，9 条 minor 全部采纳。

---

> **文档结束**。本 PRD V1.1 流转：PM-001（终版）→ SA-001（架构）→ DD-001（模块）→ 第⑦棒（空骨架）→ 第⑧棒（preflight + 端到端 8~15 拍）。
