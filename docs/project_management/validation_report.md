# research-tool 创新点诚实性报告（validation report）

> Phase 6 审核产物。本报告就每个创新点的"是否经过实证验证"给出诚实声明。
> **原则**：禁止伪造实验结果；任何 UNVERIFIED 必须给出"无法验证原因"。

## 总览

| # | 创新点 | 类型 | 状态 | 原因 |
|---|--------|------|------|------|
| 1 | 六阶段文件管道 + 反向传播 | 架构 / 算法 | UNVERIFIED | 需 10 个调研任务 + 异常注入台；仅完成骨架 |
| 2 | 实体拆分去锚 + 画像注入 | 算法 / 产品 | UNVERIFIED | ground-truth 实体标注集需 300+ 条人工标注 |
| 3 | SSRF guard + Cookie 0600 | 安全 / 产品 | PARTIALLY VERIFIED | SSRF payload 黑盒测试已通过；多用户隔离 UNVERIFIED |
| 4 | Expert Registry 质量先验 | 算法 / 产品 | UNVERIFIED | 50 个手评专家 + 3 评审一致性（Kappa）未启动 |
| 5 | 可插拔后端 + 缓存装饰器 | 架构 | PARTIALLY VERIFIED | mock 测试可跑；真实跨 backend 缓存 UNVERIFIED |
| 6 | 文件系统作为类型系统 | 架构 / 产品 | UNVERIFIED | 需两次完整 run + 人工耗时记录 |
| 7 | Provider-agnostic LLM | 架构 | PARTIALLY VERIFIED | mock parity 可跑；真实跨 provider UNVERIFIED |
| 8 | V1.1 视频摄取三栈 | 产品 | UNVERIFIED | 需 YouTube 访问 + faster-whisper 模型 + GPU（可选） |
| 9 | 基于工件的对话（Talk 阶段） | 产品 | UNVERIFIED | 仅 1 个 demo run；缺 5 run × 10 追问标注集 |

## 统计

- 创新点总数：9
- **VERIFIED**：0
- **PARTIALLY VERIFIED**：3（创新点 3、5、7）
- **UNVERIFIED**：6（创新点 1、2、4、6、8、9）

## 失败原因分布

### 数据/标注不足（5 项）
- 2（实体 ground-truth 300+ 条）
- 4（专家手评 50 + Kappa 一致性）
- 5（真实跨 backend 行为）
- 8（YouTube 数据 + WER 标注）
- 9（追问标注集 5×10）

### 资源/环境不足（3 项）
- 1（10 个调研任务人力）
- 6（两次完整 run 耗时记录）
- 8（GPU / 模型下载）

### 已可执行但未实跑（1 项）
- 3（多用户 docker 隔离 — 本机无 docker-for-mac）

## 与已知计划的边界

未在本报告 brainstorm（已在 plan）：
- CVPR / CVF / Poster / Oral / YouTube discovery 链路
- Expert Registry 完整实现
- MCP server / sub-agent

## 风险与建议

1. 创新点 1、2 是项目核心算法价值，建议优先补 ground-truth 标注集。
2. 创新点 3 是已落地部分，建议补 docker-for-mac 隔离测试以达 VERIFIED。
3. 创新点 8 受网络/资源约束，建议列为可后续启动项。
4. 所有 PARTIALLY VERIFIED 项应在里程碑结束时升级为 VERIFIED 或显式 DEFERRED。

## 诚实性声明

本报告无任何伪造数据；所有"已通过"测试均为 5 分钟内 mock / 黑盒可跑范围；所有 UNVERIFIED 项均给出具体无法验证原因。

---

**报告完成时间**：2026-07-21
**关联文件**：`innovation/catalog.md`（9 创新点）+ `experiments/catalog.md`（9 实验设计）