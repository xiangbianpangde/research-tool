# HANDOFF — VideoIngest V1.1 实施交接

> **生成日期**：2026-06-01
> **生成上下文**：plan-writing 流水线 8 棒完成、设计包就位后
> **目标读者**：新会话的 Claude agent 接手开发
> **项目代号**：VideoIngest（research-tool 视频/音频转图文笔记）
> **当前阶段**：✅ 设计完成 → ❌ 实施未开始

---

## 0. 30 秒速览

设计已 8 棒走完、12 模块文件框架已就位、6 份系统模拟产物已生成。**唯一没做的是写实现代码**。新 agent 进来是 **从骨架到血肉** 的阶段，不应再触发 `/plan-writing`。

---

## 1. 必读文件（按顺序）

| # | 文件 | 为什么读 |
|---|---|---|
| 1 | `产出物/00-索引.md` | 设计包入口 |
| 2 | `产出物/08-系统模拟运行/00-索引.md` | 8 棒全景 + 模块归属 |
| 3 | `产出物/08-系统模拟运行/end-to-end-trace.md` | 15 拍端到端轨迹，看系统如何"实际跑" |
| 4 | `产出物/01-需求澄清/PRD-VideoIngest-V1.1-20260601.md` | 终版 PRD（5 FR + 5 NFR + 5 风险） |
| 5 | `产出物/04-整体结构设计/MP-VideoIngest-V1.1-20260601.md` | **12 模块划分**（M-001~M-012） |
| 6 | `产出物/06-详细设计/IC-VideoIngest-V1.1-20260601.md` | **接口契约**（IC-001~IC-030） |
| 7 | `产出物/06-详细设计/FS-VideoIngest-V1.1-20260601.md` | 文件结构规范 |
| 8 | `产出物/07-文件框架/M-XXX/` | 每个模块 5 份设计文档 + 一个 .py 骨架 |

---

## 2. 集成目标与移植源

| 角色 | 路径 |
|---|---|
| **集成目标**（写入位置） | `C:\Users\yhn\Desktop\research-tool` |
| **移植源**（复用代码） | `C:\Users\yhn\BiliNote` |
| **复用率** | 下载器 + 转写器 ≈ 60% 代码量 |
| **不移植** | BiliNote 的 Web UI、数据库、任务队列、截图、链接 |

---

## 3. 关键约束（违反任何一条 = 回退重做）

| 编号 | 约束 | 来源 |
|---|---|---|
| **NFR1** | `yt-dlp` + `faster-whisper` 均为可选 extras，未装不影响现有功能 | PRD |
| **NFR2** | 现有 92 个测试必须全绿 | PRD |
| **NFR3** | 网络/转写/ffmpeg 失败 → 明确报错，不静默 | PRD |
| **NFR4** | 同 URL 二次运行跳过转写（缓存） | PRD |
| **NFR5** | 默认中文 zh，可配 en/ja | PRD |
| **模型** | LLM 客户端用 `deepseek-v4-flash` | 用户硬约束 |
| **代码风格** | 遵循 `产出物/06-详细设计/CS-VideoIngest-V1.1-20260601.md` | DD-001 |
| **入口** | CLI 命令：`research run "主题" --video-url "URL"` | FR4.2 |

---

## 4. 12 模块依赖与实施顺序

```
M-002 预检 ─┐
            ├→ M-003 下载器 ─→ M-005 转写器 ─→ M-007 笔记结构 ─→ M-008 管道适配
M-001 CLI  ─┘                                                              │
  ↑                                                                      ↓
  └────────────────────── M-012 并发编排 (3 并发) ←────────────────────────┘

横切：M-004 缓存 / M-006 LLM 客户端 / M-009 ffmpeg 包装 / M-010 错误 / M-011 日志
```

| 顺序 | 模块 | 内容 | 预计工时 |
|---|---|---|---|
| 1 | **M-002** 预检 | 探测 yt-dlp/ffmpeg/whisper 可用性 | 1h |
| 2 | **M-003** 下载器 | 移植 BiliNote 下载器（yt-dlp 包装） | 2-3h |
| 3 | **M-005** 转写器 | fast-whisper + Groq 双后端 | 3-4h |
| 4 | **M-007** 笔记结构 | Markdown + YAML front matter | 1h |
| 5 | **M-008** 管道适配器 | 写入 research-tool `raw/` 目录，对接 collect 阶段 | 2-3h |
| 6 | **M-001** CLI 入口 | `research run --video-url` 真实命令，URL 白名单校验 | 1-2h |
| 7 | **M-012** 并发编排 | Semaphore(3) 多 URL 处理 | 1h |
| 8 | **M-006** LLM 客户端 | deepseek-v4-flash 客户端 | 1-2h |
| 9 | **M-009** ffmpeg 包装 | 音频提取（m4a→wav 等） | 1h |
| 10 | **M-004** 缓存 | 按 URL hash 存转写结果 | 1h |
| 11 | **M-010** 错误处理 | 统一错误码 + 退出码仲裁 | 1-2h |
| 12 | **M-011** 日志 | 结构化日志 + argv sha256 + 敏感字段过滤 | 1-2h |

**总计：18-25 小时**（单人）

---

## 5. 实施检查清单

每完成一个模块：

- [ ] 读该模块 `产出物/07-文件框架/M-XXX/{API,FC,FDR,FF,FH}-M-XXX-*.md` 5 份
- [ ] 看 `.py` 骨架里的"功能描述"+"依赖关系"+"注意事项"（在文件头注释里）
- [ ] 用 `pytest tests/test_XXX.py -v` 跑通该模块的测试
- [ ] 跑全量 `pytest tests/ -v`（**必须 92 + 新增全绿**）
- [ ] 提交前：`git diff` 看是否动了不该动的文件（其他模块）

---

## 6. 绝对不要做的事

- ❌ 改 research-tool 现有 92 个测试的任何一行
- ❌ 把 `yt-dlp`/`faster-whisper` 加到核心依赖（必须放 `[project.optional-dependencies]`）
- ❌ 直接调用 LLM 而走非 `M-006 llm_client` 通道
- ❌ 写同步阻塞代码（全部用 `asyncio`）
- ❌ 把 BiliNote 的 Web UI/数据库/任务队列搬过来
- ❌ 触发 `/plan-writing` 重新设计（设计已完成）

---

## 7. 完成定义（DoD）

- [ ] 12 个模块全部实现且单元测试覆盖
- [ ] `research run "技术大会 talk" --video-url "https://www.bilibili.com/video/BVxxx"` 端到端跑通
- [ ] 现有 92 测试 + 新增测试全绿
- [ ] 文档更新：`research-tool/README.md` 增加 "视频摄入" 章节
- [ ] 提交后跑一次 `产出物/08-系统模拟运行/end-to-end-trace.md` 里的 15 拍验证清单

---

## 8. 上下文参考

- 多 Agent 协同系统建设规范：`C:\Users\yhn\.claude\projects\C--Users-yhn-Desktop-workflow\memory\multi-agent-pipeline-system.md`
- 上一个会话的运行日志：`产出物/_流水线执行报告.md`（旧位置，保留作历史）
- plan-writing 脚本源：`C:\Users\yhn\Desktop\workflow\.claude\workflows\plan-writing.js`
- 原始 PRD：`C:\Users\yhn\Desktop\workflow\00-PRD.md`（V0.2，已被 V1.1 替代）

---

**新 agent 第一句话建议**：

> "我读完了 `产出物/00-索引.md` 和 `HANDOFF.md`，准备从 M-002 预检模块开始实现。先 Read `产出物/07-文件框架/M-002/` 下的 5 份设计文档和 `.py` 骨架，然后开干。"
