# 文件框架结构 — M-006 LLM 客户端（DD-M-006）

> **生成方**：DD-M-006
> **日期**：2026-06-01
> **负责模块**：M-006（LLM 客户端）
> **DDI 上游门禁**：0.985（≥ 0.85，通过）

---

## 模块标识

```
[模块编号]  M-006
[模块名称]  LLM 客户端
[设计模式]  适配器模式（DeepseekClient/QwenClient）+ 责任链模式（Deepseek→Qwen fallback）
[关联上游]  DD-001 V1.1（FS-006/MD-006/IC-013/IC-014/IC-015/DE-005/DE-006/CS-001）
```

## 文件框架

```
产出物/07-文件框架/M-006/
├── FF-M-006-VideoIngest-V1.1-20260601.md          ← [本文件：文件框架结构]
├── API-M-006-VideoIngest-V1.1-20260601.md         ← [接口注释清单]
├── FC-M-006-VideoIngest-V1.1-20260601.md          ← [文件结构合规报告]
├── FDR-M-006-VideoIngest-V1.1-20260601.md         ← [框架决策记录]
├── FH-M-006-VideoIngest-V1.1-20260601.md          ← [文件框架健康度仪表盘]
└── research_tool/                                 ← [代码文件根]
    ├── llm_client.py                              ← [M006 主体：6 类 + 6 顶层函数]
    └── tests/                                     ← [测试包]
        ├── __init__.py                            ← [测试包初始化]
        └── test_llm_client.py                     ← [M006 测试：15 用例]
```

## 文件清单

| 文件路径 | 文件职责 | 注释状态 | 业务代码状态 | 依赖 |
|---------|---------|---------|------------|------|
| `M-006/research_tool/llm_client.py` | M-006 主体：双模型适配 + 责任链 + 校验 + token 计数 | 完整（文件头+6 类+6 顶层函数+模块级常量+__all__） | 骨架占位（`...` / `pass`） | datatypes / M-010 / M-011 |
| `M-006/research_tool/tests/__init__.py` | 测试包标识 | 完整 | 无 | - |
| `M-006/research_tool/tests/test_llm_client.py` | M-006 测试套件 | 完整（文件头+3 fixtures+7 测试类+15 用例） | 骨架占位 | M-006 主体 + fixtures |

## 类与函数清单

### 6 个类（来自 MD-006）

| 序号 | 类名 | 关联 IC | 方法数 | 状态机 |
|------|------|---------|--------|--------|
| 1 | `DeepseekClient` | IC-013 | 5 | IDLE/CALLING/DONE/FALLBACK_TRIGGER |
| 2 | `QwenClient` | IC-013 | 5 | IDLE/CALLING/DONE/ERROR_RAISED |
| 3 | `PromptBuilder` | IC-013/IC-015 | 3 | 无状态 |
| 4 | `FrontMatterValidator` | IC-014 | 3 | ALL_VALID/HAS_INVALID/REGENERATED |
| 5 | `RAGQuery` | IC-004/IC-015 | 2 | 责任链串联 |
| 6 | `TokenCounter` | IC-013 | 3 | 无状态 |

### 6 个顶层函数（来自 MD-006）

| 序号 | 函数名 | 关联 IC | 复杂度 |
|------|--------|---------|--------|
| 1 | `summarize` | IC-013 | 高（责任链+校验+章节降级） |
| 2 | `call_deepseek` | IC-013 | 中（含 5xx 重试） |
| 3 | `call_qwen` | IC-013 | 中（fallback 终端） |
| 4 | `validate_front_matter` | IC-014 | 低（剔除非法键） |
| 5 | `rag_query` | IC-015/IC-004 | 中（单次调用） |
| 6 | `truncate_to_20k` | IC-013 | 低（粗估截断） |

## 文件间依赖关系

```
research_tool/llm_client.py (M-006)
  ├─→ datatypes.py (DE-005 Transcript / DE-006 LLMSummary / DE-007 Chapter)
  ├─→ error_handler.py (M-010, register_error)
  └─→ structured_logger.py (M-011, emit_log)

research_tool/tests/test_llm_client.py
  └─→ research_tool/llm_client.py (M-006 主体)
```

**DAG 检测**：无循环依赖；M-006 仅被 M-001/M-007/M-012 反向引用（不在本模块文件内）。

## 来源标注

- 框架主结构：[DD-001:FS-006]
- 类与函数签名：[DD-001:MD-006]
- 接口契约：[DD-001:IC-013] [DD-001:IC-014] [DD-001:IC-015]
- 数据结构：[DD-001:DE-005 Transcript] [DD-001:DE-006 LLMSummary]
- 代码风格：[DD-001:CS-001]
- 设计模式：[DD-001:DP-006 适配器+责任链]
- 多方案对比：[DD-M-006 推断:基于 DD-001 FS 与 MD 的 2 方案对比]

## 框架统计

| 项 | 数量 |
|----|------|
| 文件数 | 3（含 tests/__init__.py） |
| 类数 | 6 |
| 顶层函数 | 6 |
| 私有方法 | 8（_post_chat/_parse_response/_handle_5xx/_truncate_input/_is_valid_key 等） |
| 模块级常量 | 14 |
| 测试用例 | 15（核心 6 + 边界 4 + 异常 5） |
| 测试类 | 7 |
| Fixtures | 3 |
| 注释行占比 | 100%（无业务代码） |

---

> **本文件结束**。M-006 文件框架结构就绪，交付 DD-S。
