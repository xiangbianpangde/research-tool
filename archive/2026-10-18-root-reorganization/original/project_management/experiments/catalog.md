# research-tool · 实验设计 Catalog

> Phase 6 审核产物（9 个实验合并版）。每个含 baseline / metric / dataset / ablation / setup。
> **状态**：默认 UNVERIFIED（无 GPU / 无 API 配额 / 无 ground-truth 标注），仅设计可跑版本，不要求执行。

## 索引

| 创新点 | 验证状态 | 失败原因 |
|--------|----------|----------|
| 1 六阶段管道 | UNVERIFIED | 需 10 个调研任务 + 注入崩溃 |
| 2 实体拆分 | UNVERIFIED | 需 ground-truth 实体标注集 |
| 3 SSRF / 0600 | PARTIALLY VERIFIED | SSRF payload 可跑；多用户 docker 不可 |
| 4 专家库 | UNVERIFIED | 需 50 个手评专家 |
| 5 后端 + 缓存 | PARTIALLY VERIFIED | mock backend 可跑 |
| 6 FS 契约 | UNVERIFIED | 需复现完整管道 |
| 7 LLM 抽象 | PARTIALLY VERIFIED | mock provider 可跑 |
| 8 视频摄取 | UNVERIFIED | 需 YouTube 访问 + Whisper 模型 |
| 9 Talk over artifacts | UNVERIFIED | 需真实 run + 评测集 |

## 共用原则

- 不允许伪造实验结果；UNVERIFIED 必须有"无法验证原因"
- ablation 必含至少一个开关（如"with/without 缓存"）
- dataset 必标注来源 / 规模 / 是否可公开获取

---

## 1. 六阶段管道 + 反向传播

**Baseline**：B1 = 单进程 deep-research agent 循环；B2 = Celery forward-only pipeline
**Metric**：MTTR（注入异常后恢复时间）/ 调试时间（人工定位 bug wall time）/ 反向传播覆盖率（新增实体数 / 原实体数）
**Dataset**：10 个跨领域调研任务（AI 安全 / 医学 / 经济学 / 编程语言史）；随机在 stage 1-5 制造 NPE/超时/OOM
**Ablation**：A0 = B1；A1 = forward-only 文件管道；A2 = 文件管道 + 反向传播（默认）；A3 = A2 + 显式 schema 校验
**Setup**：M5 16G 本机，无 GPU 需求；mock LLM（echo）
**状态：UNVERIFIED** — 需 10 个完整调研任务的人力标注 + bug 注入台

---

## 2. 实体拆分去锚 + 画像注入

**Baseline**：B1 = 原 query 直接检索；B2 = 原 query + LLM 自动 query 扩展
**Metric**：Coverage（检索实体数 / ground-truth）/ Precision@10 / 锚定偏差（"原 query 主体"占比）
**Dataset**：10 个争议性 query（"Anthropic 安全"、"OpenAI 治理"），每个含 30+ 手评 ground-truth 实体
**Ablation**：A0 = 拆分 ✗ + 画像 ✗；A1 = 拆分 ✓ + 画像 ✗；A2 = 拆分 ✓ + 画像 ✓（本创新点）；A3 = 拆分 ✗ + 画像 ✓
**Setup**：LLM = MiniMax-M3；SearchBackend = mock
**状态：UNVERIFIED** — 需 300+ 条人工标注

---

## 3. SSRF guard + Cookie 0600

**Baseline**：B1 = urllib/requests（无 SSRF 校验）；B2 = scrapy 默认 downloader
**Metric**：SSRF 拦截率 / false positive / 文件权限（stat 检查 0600）
**Dataset**：SSRF payload = PayloadsAllTheThings 列表 100 条；合法 URL = 公开站点 50 条
**Ablation**：A0 = guard ✗ + 0600 ✗；A1 = guard ✓ + 0600 ✗；A2 = guard ✓ + 0600 ✓（本创新点）
**Setup**：仅 Python 标准库 + 项目内 security 模块
**状态：PARTIALLY VERIFIED** — SSRF payload 黑盒测试 5 分钟可跑（已通过）；多用户 docker 隔离因无 docker-for-mac 不可跑

---

## 4. 专家库质量先验

**Baseline**：B1 = GitHub 按 star 倒序；B2 = Google Scholar 按 h-index 倒序
**Metric**：Precision@10 / MRR / 低星高质召回率（star<100 但 quality>0.8 的召回比例）
**Dataset**：50 个手评专家，10 低星高质 + 30 中星中等 + 10 高星低质
**Ablation**：A0 = star ✓ + quality ✗ + domain ✗；A1 = star+quality；A2 = star+quality+domain（本创新点）；A3 = 无 star + quality+domain
**Setup**：数据集需 50 条人工标注，标注指南占位
**状态：UNVERIFIED** — 需 3 名评审一致性 Kappa > 0.7

---

## 5. 可插拔后端 + 缓存装饰器

**Baseline**：B1 = 硬编码单一 backend；B2 = LangChain Retriever 抽象（无缓存）
**Metric**：新增 backend 代码量 / 缓存命中率 / 延迟下降率 / 跨 backend 缓存复用率
**Dataset**：1000 条合成 query（含 30% 重复）；mock backend
**Ablation**：A0 = 抽象 ✗ + 缓存 ✗；A1 = 抽象 ✓ + 缓存 ✗；A2 = 抽象 ✓ + 缓存 ✓（本创新点）
**Setup**：无外部 API，仅 mock
**状态：PARTIALLY VERIFIED** — mock 测试 5 分钟可跑；真实跨 backend 因 API 配额未跑

---

## 6. 文件系统作为类型系统

**Baseline**：B1 = SQLite 中间状态；B2 = in-memory dict（崩溃即丢）
**Metric**：IDE 友好度（grep/cat/jq 可发现比例，人工评分 1-5）/ 演化追溯时间 / 恢复延迟
**Dataset**：单个调研任务跑两次（v0.1.1 prompt A → B），观察 `runs/<id>/*.jsonl` 差异
**Ablation**：A0 = SQLite + 无校验；A1 = 文件 + 无校验；A2 = 文件 + pydantic（本创新点）
**Setup**：无外部 API；手动 diff + 时间测量
**状态：UNVERIFIED** — 需完整跑两次 run 并人工记录耗时

---

## 7. Provider-agnostic LLM 抽象

**Baseline**：B1 = 硬编码 OpenAI SDK；B2 = LiteLLM proxy
**Metric**：新增 provider 代码量 / parity 测试通过率 / 冷启动延迟
**Dataset**：50 条 prompt 模板；5 个 mock provider
**Ablation**：A0 = 无接口 + 无 fallback（= B1）；A1 = 有接口 + 无 fallback；A2 = 有接口 + 有 fallback
**Setup**：mock provider，不调真实 API
**状态：PARTIALLY VERIFIED** — parity 测试 mock 版 5 分钟可跑；真实跨 provider 因 API 配额未跑

---

## 8. V1.1 视频摄取

**Baseline**：B1 = whisper.cpp CLI 单文件转写；B2 = YouTube 官方 transcript API（仅英文）
**Metric**：WER（转写 vs 人工字幕）/ 端到端延迟（URL → 摘要 wall time）/ 字幕优先命中率
**Dataset**：10 个 YouTube URL（5 有官方字幕 + 5 纯音频），覆盖 TED / 学术讲座 / 播客
**Ablation**：A0 = 字幕 ✗ + 转写 ✓ + 摘要 ✗；A1 = 字幕 ✓ + 转写 ✓ + 摘要 ✗；A2 = 全 ✓（本创新点）
**Setup**：需 YouTube 访问 + faster-whisper 模型（首次 ~1.5GB）
**状态：UNVERIFIED** — 无 GPU 加速 + 仅完成 stub

---

## 9. Talk over artifacts

**Baseline**：B1 = ChatGPT 网页对话（无引用强制）；B2 = LangGraph ReAct（内存 conversation state）
**Metric**：引用准确率 / 幻觉率 / 可重放度（人工评分 1-5）
**Dataset**：5 个真实 run，每个 10 轮追问；ground-truth = 每个追问的"正确引用文件"
**Ablation**：A0 = RAG ✗ + 引用 ✗ + 文件化 ✗；A1 = RAG ✓；A2 = RAG ✓ + 引用 ✓ + 文件化 ✓（本创新点）
**Setup**：需 5 个真实 run（本机仅 1 个 demo）
**状态：UNVERIFIED** — run 数量不足 + 标注集未完成