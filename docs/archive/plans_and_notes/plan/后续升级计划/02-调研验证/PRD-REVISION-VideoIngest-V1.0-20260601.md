# PRD 修订建议（VideoIngest V1.0 → V1.1）

> **生成方**：RA-001
> **日期**：2026-06-01
> **接收方**：PM-001（用于 V1.0 → V1.1 增量修订）
> **来源依据**：6 份 research-tool 报告 + 本地源码勘察 + 假设验证矩阵
> **模板遵循**：soul §3.3（S-NNN：改什么→怎么改→为什么）

---

## 修订建议汇总

| 编号 | 指向条目 | 等级 | 严重度 | 决策类型 | 摘要 |
|------|----------|------|--------|----------|------|
| S-001 | Q-001 / F-003 / F-012 | ⚠️ 风险已量化 | minor | PM 可直接决定 | B 站实测约 80% 可下；F-012 默认提供 cookie 模板，失败转 E_DL_BILI_403 |
| S-002 | R-001 / F-003 / NF-008 | ⚠️ 风险升级 | **major** | 需新增硬约束 | YouTube PO Token 必装 Deno/Node JS 运行时；preflight check |
| S-003 | R-003 / F-005 / NF-010 | ✅ 假设强化 | minor | PM 可直接决定 | 维持 deepseek-v4-flash 单次整段；明确 NF-010 model 名锁定 |
| S-004 | R-004 / F-004 / A-003 / Q-003 | ✅ 假设成立 | minor | PM 可直接决定 | 默认 medium；增加 `--transcriber-size` 切档 |
| S-005 | R-005 / F-006 / Q-004 | ✅ 假设成立 | minor | PM 可直接决定 | front matter 字段加 `video_` 前缀防误读 |
| S-006 | R-006 / A-006 / 第 9 章 | ✅ 假设成立 | minor | PM 可直接决定 | 移植仅取 downloader+transcriber 子目录；锁版本下限 |
| S-007 | R-007 / G-005 / AC-E2E | 🔄 需替代 | **major** | 需 PM + SA 共同决策 | 第⑧棒强制 preflight check；缺一则 `[SIM-STUB]` |
| S-008 | R-103 / Q-007 / F-005 | 📌 待决策 | **major** | PM 需决策 | LLM 单次调用预算：单次整合 vs 分段 map-reduce |
| S-101 | R-101 / F-012 / NF-004 | ✅ 假设成立 + 增强 | minor | PM 可直接决定 | 显式校验 yt-dlp ≥ 2023.07.06 |
| S-102 | R-102 / F-009 / Q-006 | ⚠️ 风险升级 | minor | PM 可直接决定 | 缓存键 = `sha256(url) + etag/last-modified` |
| S-201 | R-201 / F-018 | ✅ 假设成立 | minor | PM 可直接决定 | ffmpeg 关键帧；无障碍 |
| S-202 | R-202 / F-010 | ✅ 假设成立 + 增强 | minor | PM 可直接决定 | 章节切分 < 3 时降级等距切片 |

**总计**：12 条建议，其中 **3 条 major（S-002/S-007/S-008）**，其余 minor

---

## 详细修订建议（soul §3.3 模板）

### S-001 — B 站下载失败回退为 Cookie 模板
- **指向条目**：PRD 第 3 章 F-003、F-012；第 7 章 Q-001
- **问题描述**：PRD 假设"B 站公开视频默认可下/取字幕，无需登录"（Q-001）；调研显示约 80% 可下，4K/付费/限地区视频需 Cookie
- **建议改动**：
  - F-003 验收标准增加错误码 `E_DL_BILI_403`（来源：风险报告 3.2 段，4K 高码率缺失"have to become a premium member"提示）
  - F-012 由"可选 Cookie 注入"升级为"**默认提供 cookies.txt 模板**（含空占位），用户填空即用"
  - Q-001 风险等级保持"中"，默认假设改为"约 80% 可下；F-012 默认提供 cookie 模板"
- **改动原因**：[来源直接结论] 风险报告 3.2 段："用户从美国下载，怀疑地理位置可能影响速度"；"Format(s) 4K 超高清, 1080P 高码率 are missing; you have to become a premium member"（来源17/43）
- **影响评估**：轻度（CLI 行为不变，仅默认 cookie 模板多 1 个空文件）
- **决策类型**：PM 可直接决定

### S-002 — YouTube PO Token 必装 Deno 运行时（major）
- **指向条目**：PRD 第 3 章 F-003；第 4 章 NF-008；第 7 章 R-001
- **问题描述**：PRD 假设"YouTube 公开视频可由 yt-dlp 下载，无需登录"（A-002）；调研显示自 2024-06 起 YouTube 上线 PO Token 动态签名，原有内置 JS 解释器失效，**必须安装 Deno 或其他受支持的 JavaScript 运行时**才能继续下载
- **建议改动**：
  - F-003 验收标准增加 "**前置依赖**：`deno --version` ≥ 2.0，缺失则返回错误码 `E_DL_001_DENO_MISSING` 并提示安装"
  - NF-008"兼容性三平台" 增加 "Win11/macOS 14+/Ubuntu 22.04+ 均预装/需自装 Deno ≥ 2.0"
  - 第 8 章 作用域变更轨迹 + 1 行："v1.1 收窄：YouTube 下载需 Deno 运行时"
  - 第 7 章 R-001 风险等级由"中"升级为"高"
  - 第 7 章 Q-001 升级为"必装 Deno 文档化"
- **改动原因**：[来源直接结论] 风险报告 1.4 段："2025-09-23，yt-dlp 开发者 bashonly 公告：用户必须安装 Deno 或其他受支持的 JavaScript 运行时，才能继续下载 YouTube 视频"（来源42）；[来源直接结论] 风险报告 3.1 段："YouTube 于 2024 年 6 月上线 PO Token（Playback Offset Token），令牌每 5 分钟自动失效、实时计算、一次性使用"（来源42）
- **影响评估**：重度（v1.0 必装新依赖；用户门槛+1）
- **决策类型**：需 PM + 用户共同决策（影响所有 YouTube 用户）
- **替代方案**：🔄 若拒绝 Deno 依赖，v1.0 砍 YouTube 仅做 B 站（与 Q-001 失败回退一致）

### S-003 — deepseek-v4-flash 维持整段总结，明确模型名锁定
- **指向条目**：PRD 第 3 章 F-005、F-013；第 4 章 NF-010；第 7 章 Q-002
- **问题描述**：PRD 假设"deepseek-v4-flash 支持 64k context，30 分钟转写稿整段总结"（Q-002）；调研显示**实际支持 1M context**（超出需求 15x）
- **建议改动**：
  - F-005 验收标准更新为 "**实测 1M context**（远超 30 分钟视频转写稿的 8-15k token）"
  - NF-010 显式声明 "model = `deepseek-v4-flash`"（不要混用 deepseek-chat 默认值）
  - Q-002 风险等级由"高"降为"低"
  - PRD 显式标注"本棒调研发现 research-tool 既有 LLMConfig 默认 model = `deepseek-chat`（即 deepseek-v3），本集成在 CLI 启动时强制覆盖为 `deepseek-v4-flash`"
- **改动原因**：[来源直接结论] 技术报告 3.4 段："DeepSeek V4 Flash 采用 284B 总参数/13B 激活参数的 MoE 架构……支持高达 1M token 的上下文窗口"（来源19、31）；[本地源码勘察] research-tool `src/domain/models.py:33` 默认 `model: str = "deepseek-chat"` 需被覆盖
- **影响评估**：轻度（功能无变化，仅文档与配置精确化）
- **决策类型**：PM 可直接决定

### S-004 — Whisper 默认档位由 tiny 调整为 medium
- **指向条目**：PRD 第 3 章 F-004；第 7 章 A-003、Q-003
- **问题描述**：PRD 假设"fast-whisper medium 中文 CER ≥ 85%"（A-003/Q-003）；调研显示**标准普通话 CER 4-8%（即 92-96% 准确率）**，远超 85% 目标
- **建议改动**：
  - F-004 验收标准增加 "**默认 model_size = `medium`**；30 分钟视频转写 ≤ 5 分钟"
  - F-014 转写引擎切换增加 `--transcriber-size {tiny|base|small|medium|large}` 参数
  - Q-003 风险等级由"中"降为"低"
  - PRD 显式标注"BiliNote v2.2.0 默认 tiny 仅为内存妥协，本集成 v1.0 优先质量"
- **改动原因**：[来源直接结论] Whisper 报告 1.4 段："标准普通话在安静环境下识别准确率达 92%-95%，专业术语（如'碳中和'）错误率低于 3%"（来源45）；AISHELL-1 CER 4.2%（来源13）；[来源直接结论] 竞品方案报告 3.4 段："BiliNote 使用本地 Fast-Whisper 模型进行音频转录"且"v2.2.0 默认 tiny 缓解性能问题"（来源32）
- **影响评估**：中度（medium 模型 ≈ 1.5GB，无 GPU 机器可能 OOM；R-002 仍存在）
- **决策类型**：PM 可直接决定（与 R-002 风险关联：若用户机器 < 8G RAM，CLI 应自动降档为 base/small）

### S-005 — front matter 字段加 `video_` 前缀
- **指向条目**：PRD 第 3 章 F-006；第 9 章 notes_schema；第 7 章 Q-004
- **问题描述**：PRD 假设"raw/ 接纳新 front matter 字段，字段前缀 video_*"（Q-004）；调研显示**解析器（PyYAML safe_load）对未识别字段宽容**，但建议显式前缀防误读
- **建议改动**：
  - 第 9 章 notes_schema 字段集显式加 `video_` 前缀：`video_id / video_source_url / video_title / video_author / video_duration / video_cover_url / video_tags / video_chapters[] / video_created_at`
  - F-006 验收标准增加 "**字段命名空间隔离**：`video_*` 前缀避免与未来其他来源（论文/网页/播客）冲突"
  - Q-004 风险等级由"中"降为"低"
- **改动原因**：[来源直接结论] front matter 报告 2.1 段："YAML 1.2 规范定义了三种基本原语：mappings、sequences 和 scalars"；"不同语言和库的实现存在显著差异"（来源13、22）；[本地源码勘察] research-tool `src/domain/config.py` 使用 `yaml.safe_load`，对未识别字段静默通过
- **影响评估**：轻度（字段命名调整，notes_schema 内部一致即可）
- **决策类型**：PM 可直接决定

### S-006 — BiliNote 移植范围限定为 downloader+transcriber 子目录
- **指向条目**：PRD 第 9 章集成边界；第 7 章 A-006
- **问题描述**：PRD 假设"BiliNote 移植模块与 research-tool 依赖兼容"（A-006）；调研显示**核心依赖完全兼容**（同 Pydantic v2 / Python 3.11+ / faster-whisper==1.1.1 / yt-dlp==2025.3.31），但 BiliNote 整体含 Celery/Redis/chromadb/SQLAlchemy 任务队列栈，研究工具不需要
- **建议改动**：
  - 第 9 章模块表明确"**移植子目录 = BiliNote/backend/app/services/downloader/ + transcriber/**"，跳过 `tasks/`, `db/`, `vector_store/`, `routers/`
  - 增加 setup.py 锁版本说明：`yt-dlp>=2025.03.31, faster-whisper>=1.1.1, ffmpeg-python>=0.2.0, youtube-transcript-api>=1.0.0`
  - 增加 "**Python 版本 ≥ 3.11**" 显式声明
- **改动原因**：[本地源码勘察] BiliNote `backend/requirements.txt` 第 15/19/27/127/128 行：celery==5.5.1, chromadb>=0.5.0, ctranslate2==4.6.0, youtube-transcript-api>=1.0.0, yt-dlp==2025.3.31；[本地源码勘察] research-tool `src/domain/models.py:12` Pydantic v2 + research-tool 整体无 Celery/Redis
- **影响评估**：轻度（移植边界更清晰，依赖体积更小）
- **决策类型**：PM 可直接决定

### S-007 — 第⑧棒强制 preflight check + 缺依赖打 `[SIM-STUB]` 标记（major）
- **指向条目**：PRD 第 1 章 G-005；第 6 章 AC-E2E-01~07；第 7 章 R-007
- **问题描述**：PRD 假设"第⑧棒能用 research-tool + 移植模块在 8~15 拍内真实跑通"（R-007）；调研发现环境依赖较多（Deno/Node/Whisper 模型/ffmpeg），任一缺失都导致部分拍失败
- **建议改动**：
  - G-005 增加 "**preflight check** 子条目"：8~15 拍启动前显式检查 `deno --version` / `node --version` / `ffmpeg -version` / `~/.cache/huggingface/hub/models--Systran--faster-whisper-medium` 存在性
  - AC-E2E 增加 "AC-E2E-08：8~15 拍中真实跑的拍必须 ≥ 1/2；缺依赖的拍在产物日志显式标注 `[SIM-STUB]`，不掩盖"
  - 第 7 章 R-007 风险等级由"中"升"中"维持，但增加"缓解"列明 preflight
  - 风险表 R-006 升级为 RR-010
- **改动原因**：[来源直接结论] 风险报告 1.4 段 + 3.1 段：YouTube 必装 Deno（来源42）；[本地预研 PRD] `C:/Users/yhn/Desktop/research-tool/docs/plan/集成bilinote-plan/00-PRD.md` 列出 15 拍端到端，但未给出环境缺失处理策略
- **影响评估**：中度（增加 1 个 preflight 子步；AC-E2E 验收项 +1）
- **决策类型**：需 PM + SA 共同决策（影响 SA 设计的环境契约 + 第⑧棒实现）

### S-008 — LLM 单次调用预算需 PM 决策（major，📌）
- **指向条目**：PRD 第 7 章 Q-007；第 3 章 F-005
- **问题描述**：PRD 假设"单次 LLM 调用 ≤ 2k input / 1k output"（Q-007）；调研发现 30 分钟视频转写稿实际 8-15k token，加 4k prompt 模板 ≈ 12-20k input，**超出 2k 假设 6-10x**
- **建议改动（PM 需在以下二选一）**：
  - **选项 A（推荐）**：Q-007 默认假设更新为 "单次 ≤ 20k input / 4k output"；按 deepseek-v4-flash 价（$0.28/百万 token 输出）单次成本 ≈ $0.0011
  - **选项 B**：Q-007 维持"≤ 2k input / 1k output"，要求 F-005 增加"分段 map-reduce"逻辑（每段 1.5-2k token，map 抽要点 + reduce 整合），成本略低但章节连贯性弱
- **改动原因**：[来源直接结论] 技术报告 3.4 段："对于典型视频摘要场景（50K 输入/10K 输出），每天 20 次请求的成本仅约 $0.20"（来源31）；30 分钟视频典型 8-15k 转写稿（来源33）
- **影响评估**：影响 F-005 实现策略（轻/中度）
- **决策类型**：**PM 需决策**（影响 F-005 的 LLM 调用模式）

### S-101 — yt-dlp 版本下限锁定
- **指向条目**：PRD 第 3 章 F-012；第 4 章 NF-004
- **问题描述**：PRD 假设"用户提供的 Netscape 格式 cookie 文件可被 yt-dlp 接受"（R-101）；调研显示 yt-dlp 早期版本存在 CVE-2023-35934 Cookie 泄露漏洞
- **建议改动**：
  - NF-004 升级为 "**Cookie 文件权限 600 校验** + **yt-dlp 版本下限 2023.07.06**"
  - F-012 验收标准增加 "CLI 启动时 `yt-dlp --version` 校验，不通过返回 `E_DL_002_VERSION_TOO_OLD`"
- **改动原因**：[来源直接结论] 风险报告 2.3 段："CVE-2023-35934……影响所有自 2015.01.25 以来发布的 youtube-dl、youtube-dlc 和 yt-dlp 版本……修复版本 yt-dlp 2023.07.06"（来源53）
- **影响评估**：轻度
- **决策类型**：PM 可直接决定

### S-102 — 缓存键升级为 URL + etag/last-modified 双键
- **指向条目**：PRD 第 3 章 F-009；第 7 章 Q-006
- **问题描述**：PRD 假设"缓存键用 URL hash 即可，命中率 ≥ 80%"（Q-006）；调研发现 YouTube 视频作者会更新内容但 URL 不变，纯 URL hash 会命中陈旧缓存
- **建议改动**：
  - F-009 验收标准更新为 "缓存键 = `sha256(url) + etag_or_last_modified`"
  - CLI 增加 `--no-cache` 强制刷新
  - Q-006 风险等级维持"低"
- **改动原因**：[来源直接结论] 风险报告 1.4 段 + 3.1 段：YouTube 反爬机制会变更 n 参数和 PO Token；视频作者可能重新上传；[RA推理] 单纯 URL hash 在内容更新时无法感知
- **影响评估**：轻度（缓存结构 +1 字段）
- **决策类型**：PM 可直接决定

### S-201 — ffmpeg 关键帧截图技术无障碍
- **指向条目**：PRD 第 3 章 F-018
- **问题描述**：PRD 假设"ffmpeg 关键帧提取可满足 ≤ 5 张/视频、≤ 200KB/张"（R-201）；调研显示 ffmpeg `-vf select=eq(pict_type\,I)` 已是业界标准
- **建议改动**：
  - F-018 验收标准增加技术细节 "ffmpeg `-vf select='eq(pict_type,I)' -vsync vfr -frames:v 5`"
- **改动原因**：[来源直接结论] 竞品报告 2.2 段：BiliNote 已实现"截图插入"功能（来源05）
- **影响评估**：无
- **决策类型**：PM 可直接决定

### S-202 — 章节切分降级策略
- **指向条目**：PRD 第 3 章 F-010；第 7 章 R-202
- **问题描述**：PRD 假设"LLM 能从转写稿自动切出 ≥ 3 章节"（R-202）；调研发现 LLM 在空白/纯音乐视频上易失败
- **建议改动**：
  - F-010 验收标准增加 "**降级策略**：`chapters < 3` 时按 5 分钟等距切片；仍不足则按 1 章节兜底"
  - 新增错误码 `E_LLM_002_CHAPTERS_FALLBACK`
- **改动原因**：[RA推理] deepseek-v4-flash 在空转写稿下可能幻觉章节；R-202 调研未覆盖此 edge case
- **影响评估**：轻度
- **决策类型**：PM 可直接决定

---

## 总体修订严重度判定

- **blocking**：无（所有假设均有缓解路径或替代方案）
- **major**：3 条（S-002 YouTube Deno 必装 / S-007 preflight check / S-008 LLM 预算）
- **minor**：9 条
- **none**：无

**结论**：PRD 需增量修订至 V1.1；prdNeedsRevision = **true**，revisionSeverity = **major**
