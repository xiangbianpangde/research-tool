# research-tool 文档中心 (Documentation Center)

欢迎查阅 `research-tool` 统一文档中心。本项目为基于大语言模型（LLM）的自主深度调研引擎，采用原生九段闭环架构与双模确定性 Identity 引擎。

> **💡 快速导航提示**  
> - **人类开发者/研究者**：请从下方文档矩阵或根目录 [`README.md`](../README.md) 开始阅读。  
> - **AI Agent 智能体**：项目全局权威上下文与交互规则请遵从根目录 [`AGENTS.md`](../AGENTS.md)。  
> - **历史文档查阅**：历史阶段交付物与旧版过程记录请前往 [`archive/`](archive/README.md)。

---

## 1. 活跃文档矩阵 (Active Documentation Matrix)

| 目录/文件 | 分类定位 | 核心说明 | 适用对象 |
|:---|:---|:---|:---|
| [`PROJECT.md`](PROJECT.md) | 核心规格 | 九段闭环架构、F01–F20 特性清单、里程碑与契约规范 | 架构师 / 开发者 / 审计员 |
| [`TEST_INFRA.md`](TEST_INFRA.md) | 核心规格 | 分层测试架构（Tier 1–4、对抗压力测试）、黑盒契约与测试基建 | 测试工程师 / 审计员 |
| [`TEST_READY.md`](TEST_READY.md) | 核心规格 | 220 项黑盒 E2E 测试用例交付与质量门控签署报告 | 质量负责人 / 审计员 |
| [`STATUS.md`](STATUS.md) | 核心规格 | 当前系统功能实现状态看板（FP01–FP12）与权威指标 | 全员 |
| [`CHANGELOG.md`](CHANGELOG.md) | 核心规格 | 版本发布历史、重要特性切换（P7 九段闭环转正）记录 | 全员 |
| [`architecture/`](architecture/NINE-LOOP.md) | 架构蓝图 | 原生九段管线（Collect→Clean→Extract→Knowledge→Inspect→Targeted→Merge→QGate→Report）时序与拓扑流程图 | 架构师 / 开发者 |
| [`config.example.yaml`](config.example.yaml) | 配置参考 | 生产环境完整 YAML 配置示例（含搜索源、LLM、九段参数及预算租约） | 部署运维 / 开发者 |
| [`experts.yaml`](experts.yaml) | 配置参考 | 专家库（ExpertLib）实体策展、权威机构与领域词库映射参考 | 调研策略师 / 开发者 |
| [`conventions/`](conventions/README-规范导航.md) | 开发规范 | 涵盖架构分层、代码风格、Git 协作、API 设计、测试驱动与文档标准规范 | 开发者 / Code Reviewer |
| [`meta/`](meta/CODE_MAP.md) | 工具元数据 | 代码拓扑图谱（CODE_MAP）、Commitlint 规则与重构技术笔记 | 开发者 / 工具链维护者 |
| [`templates/`](templates/README-模板索引.md) | 交付物模板 | AI 启动提示词、BDD 规格、审计报告、收束报告等 15 种标准工程模板 | 全员 / AI Agent |
| [`paper/`](paper/paper_zh.md) | 学术成果 | 题为《跨越洞察鸿沟》的学术预印本（中英双语、LaTeX 源码与已编译 PDF） | 科研人员 / 算法研究员 |
| [`archive/`](archive/README.md) | 历史归档 | 历史阶段交付物（`deliverables/`）、技术债审查（`project_management/`）与开发计划（`plans_and_notes/`） | 历史追溯 / 法证审计 |

---

## 2. 目录层次结构 (Directory Hierarchy)

```
docs/
├── README.md                      # 本文档中心统一索引入口
├── PROJECT.md                     # 核心架构契约与特性追踪白皮书
├── TEST_INFRA.md                  # E2E 测试基础设施与分级测试规格
├── TEST_READY.md                  # E2E 交付验收与测试通过矩阵
├── STATUS.md                      # 开发进展与功能点状态速查
├── CHANGELOG.md                   # 版本发布与演进历史
├── config.example.yaml            # 完整且经过自动化单测校验的配置模板
├── experts.yaml                   # 专家库实体与白名单配置
├── architecture/                  # 系统核心架构设计蓝图
│   ├── NINE-LOOP.md               #   九段闭环核心调度逻辑说明
│   └── nine-stage-loop.png        #   九段闭环拓扑与自愈数据流图
├── conventions/                   # 研发工程规范（架构、编码、Git、测试等）
│   ├── README-规范导航.md         #   人类开发者规范速查入口
│   ├── AGENTS-规范导航.md         #   AI Agent 任务维度规范导航
│   └── ai-workflow_AI协作开发流程/ #   七步全周期 AI 协作研发流程
├── meta/                          # 仓库级元数据与辅助配置
│   ├── CODE_MAP.md                #   源码目录树与模块职责映射表
│   ├── commitlint.config.js       #   Git 提交信息校验配置
│   └── gitmessage                 #   Git commit 模板提示
├── templates/                     # 团队工程模板库（15 个标准化模板）
│   └── README-模板索引.md         #   模板使用指引与对应场景映射
├── paper/                         # 官方学术预印本与理论支撑成果
│   ├── paper_zh.md                #   学术论文中文稿（九段架构与 DRB 评测）
│   ├── paper_en.md                #   学术论文英文稿
│   ├── main.tex / main.pdf        #   LaTeX 排版源文件与编译 PDF
│   ├── references.bib             #   文献引用库
│   └── figure1_nine_loop.png      #   论文图表资产
└── archive/                       # 历史文档封存专区（只读）
    ├── README.md                  #   归档专区全景自述
    ├── CLAUDE.md                  #   历史 AI 上下文文档
    ├── deliverables/              #   历史 Track 交付报告与收束大屏
    ├── project_management/        #   历史审计报告、技术债与隔离工作日志
    └── plans_and_notes/           #   历史设计方案、升级计划与问答纪要
```

---

## 3. 文档演进与更新纪律 (Documentation Maintenance Rules)

1. **单一点权威原则 (Single Source of Truth)**：
   系统业务状态以 `STATUS.md` 为准；测试权威值以 `pytest --collect-only` 与 `TEST_READY.md` 为准；架构规格以 `PROJECT.md` 为准。禁止在其他文档中重复维护互相矛盾的拷贝。
2. **测试驱动保护 (Protected by Automated Tests)**：
   `docs/config.example.yaml` 受到自动化测试（`test_errors.py`、`test_f11_config_template.py`）强断言校验。任何对配置项的增删改均须保证与 `research_tool/domain/config.py` 模型双向同步。
3. **随码同步演进 (Docs-As-Code Sync)**：
   任何引入新架构阶段、接口变更或配置项改动的代码提交，必须在同一 Git 提交中同步更新相关规范与规格说明。
