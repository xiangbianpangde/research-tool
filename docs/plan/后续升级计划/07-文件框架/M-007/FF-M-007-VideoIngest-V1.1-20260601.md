# 文件框架结构 — M-007 笔记组装器（DD-M-007）

> **生成方**：DD-M-007
> **日期**：2026-06-01
> **负责模块**：M-007（笔记组装器 / notes_schema）
> **设计模式**：管道-过滤器 + 模板方法（6 步 Markdown 拼装）
> **上游依据**：[DD-001:MD-007] [DD-001:IC-016/017/018/019/020/021] [DD-001:FS-007] [DD-001:CS-001]
> **下游交付**：DD-S（结构设计师），由其按本框架填充代码骨架

---

## 1. 文件框架

```
research_tool/                              # 核心包
└── notes_schema.py                         # M-007 笔记组装器（主文件）
    ├── 类注释
    │   - FrontMatterParser                 # YAML 解析与字段名校验
    │   - VideoMetaInjector                 # VideoMeta 注入 front_matter
    │   - ChapterDegrader                   # 章节降级（5min 等距切片）
    │   - MarkdownAssembler                 # Markdown 总装（模板方法 6 步）
    │   - ScreenshotEmbedder                # 截图引用嵌入（占位图兜底）
    │   - ReferenceGenerator                # 参考来源章节生成
    │   - NotesSchemaOrchestrator           # 顶层编排（按状态机调度 6 步）
    ├── 函数签名注释
    │   - parse_front_matter()              # IC-016 入口前置
    │   - inject_video_meta()               # IC-017
    │   - degrade_chapters()                # IC-016 + IC-021（钩子）
    │   - embed_screenshots()               # IC-018
    │   - generate_references()             # IC-019
    │   - assemble_markdown()               # IC-020（顶层）
    │   - _build_chapter_fallback()         # 内部钩子（EP-002）

research_tool/tests/
└── test_notes_schema.py                    # M-007 单元测试
    ├── 测试场景注释（核心 8 + 边界 4 + 异常 4 = 16）
```

[来源标注] [DD-001:FS-007 §模块文件结构规范] [DD-001:MD-007 §M-007 笔记组装器]

---

## 2. 文件间依赖关系

```
notes_schema.py (M-007)
  ├─→ datatypes.py (DE-002 VideoMeta / DE-006 LLMSummary / DE-008 Transcript / DE-005 ScreenshotFrame / DE-003 Chapter)
  ├─→ error_handler.py (M-010)  [降级与错误码登记]
  ├─→ structured_logger.py (M-011)  [JSON Lines 日志]
  └─ 不依赖 M-005/006/009（被调用方，依赖关系由 M-001 在调用时传递数据）

# 横切依赖（所有模块）
notes_schema.py → error_handler.py (M-010)
notes_schema.py → structured_logger.py (M-011)
```

[来源标注] [DD-001:FS-007 §文件依赖关系图 notes_schema.py 行]

---

## 3. 6 步拼装流水线（状态机视角）

| 步骤 | 子模块 | 对应 IC | 状态转换 | 失败降级 |
|------|--------|---------|----------|----------|
| Step 1 | front_matter_parser | (前置) | INIT → PARSED | YAML 解析失败 → 字段 fallback（null 占位） |
| Step 2 | video_meta_injector | IC-017 | PARSED → META_INJECTED | null 占位继续 |
| Step 3 | chapter_degrader | IC-016 + IC-021 | META_INJECTED → CHAPTERS_FALLBACK | LLM 未返回 video_chapters → 5min 等距 |
| Step 4 | screenshot_embedder | IC-018 | → SCREENSHOTS_EMBEDDED | 路径不存在 → 占位图 |
| Step 5 | reference_generator | IC-019 | → REFERENCES_ADDED | （无降级，meta 为空则跳过） |
| Step 6 | markdown_assembler | IC-020 | → MARKDOWN_READY | 局部失败 → 部分降级 → 主链继续 |

[来源标注] [DD-001:MD-007 §状态机] [DD-001:IC-016/017/018/019/020/021]

---

## 4. 设计模式实现说明

- **管道-过滤器（Pipeline-Filter）**：6 个子模块作为过滤器串联，每步接收上一步输出并产生下一步输入。
- **模板方法（Template Method）**：`NotesSchemaOrchestrator.assemble_markdown()` 定义 6 步骨架（final method），各步骤由具体子模块实现（可被子类替换）。
- **钩子方法（Hook, EP-002）**：`_build_chapter_fallback()` 是 V1.2 扩展点，当前固定 5min 等距切片，V1.2 可替换为聚类/语义切片。

[来源标注] [DD-M推断:依据 = DD-001:MD-007 §设计模式 6 步 Markdown 拼装 + DD-001:ADR-008 EP-002 扩展点]

---

## 5. 文件命名与路径约束

- 主文件路径：`research_tool/notes_schema.py`（含 M-007 模块标识前缀 notes_schema 与模块编号无关，但通过文件头注释强制声明 M-007）
- 测试文件路径：`research_tool/tests/test_notes_schema.py`
- 禁止触碰其他模块文件（M-001~M-006、M-008~M-012 全部 R28 红线）

[来源标注] [soul §6.1 文件命名 + §五 R28 禁止跨模块操作]

---

## 6. DD-M 洞察

1. **钩子方法命名**：`_build_chapter_fallback()` 显式声明为钩子（命名带 `_` 前缀 + 方法注释标注 "EP-002"），便于 V1.2 替换。
2. **降级链路可观测**：6 步中 3 步存在降级路径（YAML / chapter / screenshot），每步降级都通过 M-011 写 WARN 日志，结构设计师应确保日志格式符合 `{"module": "M-007", "step": "..."}` 约定。
3. **依赖方向**：M-007 自身不导入 M-005/M-006/M-009，避免反向依赖；这些模块的产出（Transcript/LLMSummary/ScreenshotFrame）通过参数注入。
4. **测试 Mock 边界**：YAML 解析用 `fixtures/front_matter.yaml`，截图用 `fixtures/expected_output.md`，不引入真实 LLM/ffmpeg 调用。

[来源标注] [DD-M推断:依据 = soul §4.6 洞察注入机制 4 类典型洞察]

---

> **本文件结束**。M-007 文件框架已就绪，交付 DD-S。
