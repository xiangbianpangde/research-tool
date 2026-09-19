# 执行清单与实施计划 — ExpertLib + CVPRTalk（2026-07-11）

> **状态**：执行中
> **依据**：
> - 设计：`~/Downloads/DESIGN-ExpertLib+CVPRTalk-V1.0.md`
> - 参考 P0：`~/Downloads/_ref/`（files.zip）
> - 调研：`research-output/github-low-star-high-quality-repository-discovery`、`cvpr-paper-and-youtube-video-search-backend-implementation`、`talk-linker-deep-research`
> - 会话：`2026-07-11-172620-research-tool1.txt`、`2026-07-11-185610-reaserch-tool-ai.txt`
> - 问题登记：`worklogs/2026-07_使用问题登记.md`

---

## 0. 目标与验收

### 用户方向

| # | 方向 | 核心痛点 | 验收标准 |
|---|------|----------|----------|
| 1 | CVPR + 对应 YouTube 视频 | Collect 搜不到会议 venue 限定论文；YouTube 只能手动 `--video-url` | `research collect "VGGT" -s cvpr` 命中 CVPR 论文；可选 talk 关联后产出 `video_*.md` |
| 2 | 专家库 | `github_backend` 写死 `sort=stars`，低 star 高质量仓（如 vggt-omega）被 top-N 挤掉 | `experts_file` 启用后，vggt-omega 作为 `expert_seed` **置顶保证纳入** |

### 非目标（本轮不做）

- 通用会议平台大爬虫、付费/登录墙破解
- 专家库全自动写入（保持人工策展闸）
- 改下游 Clean/Extract/Organize/Report 契约

---

## 1. 调研结论（决策锁定）

| 决策 | 选择 | 证据 |
|------|------|------|
| 论文源主实现 | **DBLP API**（引擎名 `cvpr`） | venue=CVPR 原生过滤；CVPR 2024 约 3536 篇；ee 指向 openaccess.thecvf.com；JSON 稳定、无 key |
| 放弃参考实现的裸 CVF 正则 | 参考 `_ENTRY_RE` 对真实页 **0 命中** | 真实 HTML 为 `<dt class="ptitle"><br><a ...>`，正则期望 `\s*<a` 漏 `<br>`（已在 7/11 验证） |
| YouTube 发现 | **yt-dlp `ytsearchN:`**（零新依赖） | 已验证返回 id/title/duration/channel |
| 专家库 v1 | **强档直注**（seed_urls + extra_urls） | 设计 P0-a；参考实现 + 测试已就绪；匹配用 domains 词重叠 |
| talk 关联 | 后置、门控 | `--with-talks` 默认关；置信 ≥0.7；`--max-talks`；复用 video_pipeline |
| 视频 URL 路由 | Collect 识别 host → VideoIngest | bilibili/youtube 搜到后应转写而非当 HTML 抓 |

交叉例：`facebookresearch/vggt-omega` = CVPR 论文（DBLP）+ 专家 org 强档 seed + 可关联 YouTube talk。

---

## 2. 任务执行清单

### 阶段 A — ExpertLib 强档（P0-a）✅ 最高优先

| ID | 任务 | 文件 | 状态 |
|----|------|------|------|
| A1 | 数据模型：`ExpertEntry` / `ExpertLibrary`；`CollectorConfig` +4 字段；`SearchHit.expert` | `domain/models.py`, `search/base.py` | ✅ |
| A2 | `ExpertRegistry`（load/match/seed_urls_for/scoped_queries_for） | `infrastructure/experts/` | ✅ |
| A3 | 仓库根 `experts.yaml` seed（含 vggt-omega） | `experts.yaml` | ✅ |
| A4 | collector 强档直注 `_direct_inject_hits` | `stages/collector.py` | ✅ |
| A5 | 配置示例 + 可选启用 | `config.yaml`, `docs/config.example.yaml` | ✅ |
| A6 | 测试（加载/匹配/注入/空库回退） | `tests/test_experts.py` | ✅ |
| A7 | 验收：`pytest` + dry collect 含 vggt-omega | — | ✅ live inject 命中 |

### 阶段 B — CVPR 论文源（P0-b，DBLP）

| ID | 任务 | 文件 | 状态 |
|----|------|------|------|
| B1 | `CvprBackend`：DBLP 关键词+CVPR 锚 + 客户端 venue 过滤 | `search/cvpr_backend.py` | ✅ |
| B2 | 注册 `cvpr`：`SearchEngine` / `_build_inner` / config 注释 | models, search/__init__, config | ✅ |
| B3 | 测试（fixture，不打网） | `tests/test_cvpr_backend.py` | ✅ |
| B4 | 验收：collect VGGT 命中 CVPR 论文 | — | ✅ live 命中 CVPR 2025 VGGT |

### 阶段 C — YouTube 搜索源（1a）

| ID | 任务 | 文件 | 状态 |
|----|------|------|------|
| C1 | `YouTubeBackend`：yt-dlp ytsearch，镜像 bilibili | `search/youtube_backend.py` | ✅ |
| C2 | 注册 `youtube` | models, __init__, config | ✅ |
| C3 | 测试（monkeypatch 子进程） | test_search_backends | ✅ |

### 阶段 D — 视频 URL discovery 路由（1c 轻量闭环）

| ID | 任务 | 文件 | 状态 |
|----|------|------|------|
| D1 | collector 识别 youtube/bilibili host，写 discovery 笔记（不全量 whisper） | `stages/collector.py` | ✅ |
| D2 | 视频路径不走 Fetcher + host 判定测试 | `tests/test_video_discovery.py` | ✅ |

### 阶段 E — GitHub 多信号重排（P2 弱优化）

| ID | 任务 | 文件 | 状态 |
|----|------|------|------|
| E1 | 拉一批后客户端复合分（log stars / recency / desc / topic） | `github_backend.py` | ✅ |
| E2 | 权重可配置 + vggt-omega 回归说明 | config | ⬜ 权重仍代码内常量；有单测重排 |

### 阶段 F — Talk 关联（P1 亮点，可选）

| ID | 任务 | 文件 | 状态 |
|----|------|------|------|
| F1 | `TalkLinker`：标题相似度置信闸 | `application/talk_linker.py` | ✅ |
| F2 | pipeline `--with-talks` / `--max-talks` 钩子 | pipeline, cli | ✅ |
| F3 | 测试置信边界 0.69 拒 / 0.71 收 | tests | ✅ |

### 横切

| ID | 任务 | 状态 |
|----|------|------|
| X1 | 使用问题即时登记 `worklogs/2026-07_使用问题登记.md` | ✅ P12 已登记 |
| X2 | 不改 openai 代理补丁等已有修复 | ✅ |
| X3 | 现有测试零回归 | ✅ 479 passed, 2 skipped |

---

## 3. 推荐实施顺序

```
A (专家库强档) → B (cvpr/DBLP) → C (youtube 源) → D (视频路由) → E (github 重排) → F (talk)
     ↑ 最快验证 vggt-omega
```

- A/B 可并行（无依赖）
- C 完成后 D 才有意义
- F 依赖 B + C + D

---

## 4. 文件改动一览（最终态）

| 动作 | 路径 |
|------|------|
| 新增 | `experts.yaml` |
| 新增 | `research_tool/infrastructure/experts/__init__.py` |
| 新增 | `research_tool/infrastructure/experts/registry.py` |
| 新增 | `research_tool/infrastructure/search/cvpr_backend.py` |
| 新增 | `research_tool/infrastructure/search/youtube_backend.py` |
| 新增 | `research_tool/application/talk_linker.py`（阶段 F） |
| 新增 | `research_tool/tests/test_experts.py` |
| 新增 | `research_tool/tests/test_cvpr_backend.py` |
| 改动 | `domain/models.py`、`search/base.py`、`search/__init__.py`、`stages/collector.py`、`github_backend.py`、`config.yaml`、`docs/config.example.yaml` |

---

## 5. 风险与缓解

| 风险 | 缓解 |
|------|------|
| yt-dlp ytsearch 反爬 | flat-playlist；失败 SearchError → collector warnings |
| DBLP venue 命名 | contains `CVPR`；已验证 |
| collector 视频路由误伤 | host 白名单；非视频走原 Fetcher |
| 专家库腐化 | 人工策展；match 用收敛 domains |
| talk 成本爆炸 | 默认关；max_talks；仅核心节点 |

---

## 6. 与参考实现差异

| 参考 P0 | 本执行计划 |
|---------|------------|
| 引擎名 `cvf` + HTML 正则 | 引擎名 **`cvpr`** + **DBLP API**（正则已证失效） |
| 仅强档 + 注册 | 同，并预留 youtube / 视频路由 / talk |
| 未改 github stars | 阶段 E 再做多信号重排 |

---

## 7. 进度记录

| 日期 | 完成 |
|------|------|
| 2026-07-11 | 调研管道 talk-linker 跑通；P9–P11 代理/超时/缓冲问题登记；本执行清单落盘 |
| 2026-07-11 | 阶段 A–E 主体落地（专家库 / cvpr / youtube / 视频 discovery / github 重排） |
| 2026-07-13 | P12：`_http.get_json` 无代理时 `trust_env=False`；cvpr 查询改关键词+客户端 venue 闸；live 验收 VGGT；全量 479 passed |
| — | **剩余**：阶段 F TalkLinker；E2 权重配置化；视频 discovery → 可选全量 VideoIngest |
| 2026-07-13 | 阶段 F TalkLinker 落地；P13 yt-dlp 代理；slug/openai/logging 顺手修；全量 488+ passed |
