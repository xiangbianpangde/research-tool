# 文件框架结构 — M-008 管道适配器（DD-M-008）

> **生成方**：DD-M-008
> **日期**：2026-06-01
> **模块编号**：M-008（pipeline_adapter）
> **DDI 接收**：0.985（≥ 0.85 门禁通过）
> **设计模式**：适配器模式 + 协调器模式（落盘 + 既有管道触发）

---

## 文件框架

```
[模块编号] M-008
[模块名称] pipeline_adapter（管道适配器）
[文件框架]
  research_tool/
    pipeline_adapter.py           ← [职责：M-008 核心实现，含 4 类 + 4 函数 + 模块常量]
      - MarkdownWriter             ← [职责：Markdown 落盘（IC-022）]
      - PipelineTrigger            ← [职责：5 阶段管道触发（IC-023）]
      - TagsMerger                 ← [职责：tags 合并 + 冲突仲裁（IC-024）]
      - CollectConfigInjector      ← [职责：Collect 配置注入（CE-009）]
      - StagesResult               ← [职责：管道触发结果数据类]
      - write_markdown()           ← [职责：IC-022 模块级入口]
      - trigger_pipeline()         ← [职责：IC-023 模块级入口]
      - merge_tags()               ← [职责：IC-024 模块级入口]
      - inject_collect_config()    ← [职责：CE-009 模块级入口]
    tests/
      test_pipeline_adapter.py    ← [职责：M-008 单元测试（4 子模块 + 模块边界合规）]
        - TestMarkdownWriter       ← [职责：落盘测试 7 用例]
        - TestPipelineTrigger      ← [职责：管道触发测试 4 用例]
        - TestTagsMerger           ← [职责：tags 合并测试 3 用例]
        - TestCollectConfigInjector ← [职责：Collect 配置测试 1 用例]
        - TestModuleBoundaryCompliance ← [职责：D7=100 模块边界守护测试]

[文件间依赖关系]
  pipeline_adapter.py
    ├─→ research_tool.datatypes（DE-002 VideoMeta / DE-006 LLMSummary / DE-008 Transcript）[类型注解]
    ├─→ research_tool.error_handler（M-010）[错误码登记 E_PIPE_001 / E_PIPE_DISK_FULL]
    └─→ research_tool.structured_logger（M-011）[INFO/WARN/ERROR 日志]

  test_pipeline_adapter.py
    └─→ pipeline_adapter.py [被测对象]

[来源标注] [DD-001:FS-008] [DD-001:MD-008] [DD-001:CS-001 测试规范]
```

---

## 模块边界合规自检（D7=100 强制）

| 检查项 | 校验内容 | 通过 |
|--------|---------|------|
| 操作文件范围 | 仅操作 `产出物/07-文件框架/M-008/` 内的文件 | ✓ |
| 跨模块文件操作数 | = 0（未触碰 M-001~M-007、M-009~M-012 任何文件） | ✓ |
| 模块标识 | 所有产出物含 M-008 标识（FF/API/FC/FDR/FH-M-008-...） | ✓ |
| 文件路径 | 业务代码文件路径含 M-008 模块编号（research_tool/pipeline_adapter.py） | ✓ |
| import 范围 | 仅 import M-010 (error_handler) + M-011 (structured_logger) + datatypes，未 import 其他模块业务 | ✓ |

---

## 设计模式落点

| 模式 | 应用位置 | 说明 |
|------|---------|------|
| 适配器模式 | PipelineTrigger | 将 subprocess 异步调用适配为同步风格的 `trigger_pipeline(file_path)` 入口 |
| 适配器模式 | CollectConfigInjector | 将 Collect 子系统配置格式适配为 V1.1 内部 `COLLECT_CONFIG_VERSION` |
| 协调器模式 | MarkdownWriter + PipelineTrigger | 协调"落盘 → 触发管道 → tags 合并"三阶段串行执行（MD-008 状态机） |
| 模板方法模式 | PipelineTrigger.parse_stages | 5 阶段 stdout 解析为可复用的模板方法 |

---

## 阶梯退出检查

| 阶梯 | 退出条件 | 通过 |
|------|---------|------|
| L0 全局框架识别 | M-008 已分类到"接口入口型"框架主题 | ✓ |
| L1 文件结构创建 | 2 个文件已创建（pipeline_adapter.py + test_pipeline_adapter.py） | ✓ |
| L2 注释编写 | F3 文件头 + F4 类/函数 + F4.5 测试场景 + F5 接口注释全部就位 | ✓ |
| L3 风格检查 | Python 4 空格 / 120 行宽 / snake_case / 类型注解 全部符合 CS-001 | ✓ |

---

> **本文件结束**。M-008 文件框架就绪，可交付 DD-S 进行代码骨架搭建。
