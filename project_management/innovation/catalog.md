# research-tool · 创新点 Catalog

> Phase 6 审核产物（9 个创新点合并版）。基于 v0.1.1 已有事实 brainstorm。
> 未发明 CVPR/CVF/MCP/sub-agent/专家库（已纳入 plan）。

---

## 1. 六阶段文件管道 + 反向传播（架构 / 算法）

**创新描述**：将"调研"建模为六个文件管道阶段（collect→deepen→clean→extract→organize→report），每阶段产物落盘为有 schema 的 JSONL/MD，下游可触发反向传播回到上游重跑。

**技术原理**：阶段间通过文件系统契约通信（非 in-memory / 非数据库队列）；反向传播：downstream 阶段输出"gap 信号"驱动 upstream 阶段（典型为 deepen）基于新 query 重跑并合并。

**对比**：

| 方案 | 中断恢复 | 调试 | 反向传播 | 外部依赖 |
|------|---------|------|---------|---------|
| 单上下文 agent (deep-research) | 不可恢复 | 上下文截屏 | 不支持 | 无 |
| Celery 队列 (Auto-Research) | 需 broker | 任务日志 | 任务依赖图但无语义 | Redis/RabbitMQ |
| **本方案** | 阶段产物落盘，直接 resume | 文件可 cat/patch/replay | gap 信号驱动上游重跑 | 无 |

---

## 2. 实体拆分去锚 + 画像注入反偏差（算法 / 产品）

**创新描述**：Deepen 阶段先用 LLM 把用户 query 拆为多个独立子实体，并注入每个实体的英文名称 / 别名 / 学术画像，再分别检索，规避单 query 锚定偏差。

**技术原理**：拆分去锚：原 query 暗含锚定时，LLM 拆为 N 个子实体（厂商/监管方/学术界/受影响用户）独立检索。画像注入：为每个子实体附 LLM 生成的英文术语 / 别名 / 学术概念 map。

**为什么创新**：STORM 也做多视角检索，但靠 LLM 生成"perspective"字符串而非实体级拆分；Perplexity 多 query 由检索自动展开，无法显式控制每个子实体的画像；学术检索场景（CVPR/ACL/arXiv）中英文别名对覆盖度影响巨大但常被忽略。

---

## 3. SSRF guard + Cookie 0600 权限硬化（安全 / 产品）

**创新描述**：所有 SearchBackend 拿到 URL 后必须经过白名单 + DNS 解析 + IP 黑名单校验（防 SSRF），且持久化 Cookie / session 文件强制 `chmod 0600`。

**技术原理**：SSRF guard：URL → 解析 host → 解析 IP → 拒绝 RFC1918 / loopback / link-local / cloud-metadata（169.254.169.254）。0600 权限：所有落盘 Cookie / token / session 文件 open 时即 `os.chmod(path, 0o600)`。

**对比**：scrapy/httpx 默认 trust URL 无 SSRF 防护；LangChain WebLoader 把 Cookie 写到 `~/.cache/<tool>/` 默认 0644。

---

## 4. Expert Registry 质量先验 / 低星不弃（算法 / 产品）

**创新描述**：不只按 GitHub star 排序专家候选，用 experts.yaml Registry 记录可信策展人 + 上下文相关信号，赋予低 star 但高质量仓库（如 vggt-omega 类）更高权重。

**技术原理**：experts.yaml 含 `name, repo_url, stars, quality_score, domain_tags, curated_by`。评分函数：`final_score = α·star_log + β·quality_score + γ·domain_match`，β ≥ 0.5。

**为什么创新**：GitHub 搜索默认按 star 倒序 → vggt-omega 类被埋没；Google Scholar 按 h-index 无法统一度量代码+论文+教程三类资产。

---

## 5. 可插拔 18 后端 + CachingBackend 装饰器（架构）

**创新描述**：18 种 SearchBackend 与 5 种 LLM provider 通过统一接口注册；`CachingBackend` 装饰器用内容哈希透明包装任意 backend，自动跳过重复请求。

**技术原理**：`SearchBackend.search(query, top_k) -> list[Result]`；`CachingBackend(inner)` 对每次 search 做 SHA-256(query+top_k+params) 落盘。

**为什么创新**：LangChain Retriever 抽象无内置缓存装饰器；缓存通常与 backend 强绑定，难以跨 backend 复用。

---

## 6. 文件系统作为类型系统的契约（架构 / 产品）

**创新描述**：把每阶段输入/输出 schema 视为"类型"，目录结构视为"模块边界"，文件名为"签名"，使整个调研管道具备 IDE-friendly 可追溯性。

**技术原理**：每阶段读 `runs/<run_id>/<stage>.input.jsonl`，写 `runs/<run_id>/<stage>.output.jsonl`；读入前用 pydantic 校验；`git diff runs/<run_id>/` 可视化整次调研演化。

**为什么创新**：多数工具用 SQLite/JSON 存中间状态；DVC/metaflow 针对 ML 训练而非 LLM 调研；没有现成方案把"文件 schema = 类型"作为一等公民设计。

---

## 7. Provider-agnostic LLM 抽象（架构）

**创新描述**：5 个 LLM provider（OpenAI / DeepSeek / Ollama / Anthropic / MiniMax）通过统一接口暴露，新增 provider 只需实现一个 `complete()` 方法。

**技术原理**：`complete(prompt, *, model=None, temperature=0.7, max_tokens=1024) -> str`；CLI `--llm-provider` 选择；fallback 已纳入 ADR 0002 范围。

**为什么创新**：OpenAI/Anthropic SDK API 完全不兼容，多数项目硬编码一家；LiteLLM 强依赖其 proxy 服务。

---

## 8. V1.1 视频摄取三栈（产品）

**创新描述**：通过 yt-dlp 抓视频元数据 + faster-whisper 本地转写 + LLM 摘要，把 YouTube 视频纳入调研管道，复用同一文件契约。

**技术原理**：yt-dlp `yt-dlp --dump-json` 拿元数据 + 字幕轨；faster-whisper 本地 CTranslate2 转写；LLM 摘要复用 deepen / extract prompt。产物：`runs/<run_id>/collect.video.jsonl`。

**为什么创新**：whisper.cpp CLI 链只产转写不接入下游 LLM；Eightify 闭源；YouTube 官方 transcript 仅给英文且限速。

---

## 9. 基于工件的对话（Talk 阶段）（产品）

**创新描述**：可选 Talk 阶段允许用户以自然语言对已生成的 research report 追问，LLM 始终基于 run 内所有 stage 文件（而非自由对话历史）回答。

**技术原理**：RAG over artifacts：每次对话把"用户问题 + 相关 stage 文件片段"送入 LLM；引用强制：prompt 要求 LLM 必须引用 stage 文件路径。状态最小化：conversation 本身也是文件（`runs/<run_id>/talk.jsonl`）。

**为什么创新**：ChatGPT/Claude 对话历史黑盒不可精确回放；ReAct/LangGraph 内存 state 崩溃即丢；与项目"文件即状态"哲学一致。

---

## 阅读路径

- 想理解架构：1 → 6 → 7 → 5
- 想理解算法：2 → 4
- 想理解安全：3
- 想理解产品边界：8 → 9

详细实验设计见 `experiments/catalog.md`；验证状态见 `validation_report.md`。