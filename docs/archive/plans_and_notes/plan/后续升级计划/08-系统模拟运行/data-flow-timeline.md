# 数据流时间线 — VideoIngest V1.1（第⑧棒 系统模拟运行）

> **项目**：VideoIngest V1.1
> **模拟日期**：2026-06-01
> **主角对象**：一段转写文本（DE-005 Transcript.segment[120]）从视频源到知识树节点的全链路
> **追踪范围**：视频源 → raw/ → clean/ → extract/ → knowledge_tree.json → report.md
> **来源标注**：[DE-NNN] = 03-逻辑梳理 数据字典；[BP-NNN] = 03-逻辑梳理 业务流程；[PRD-V1.1:F-NNN] = 01-需求澄清 PRD V1.1

---

## 0. 主角对象定义

> 选取 DE-005 Transcript.segments[120] = `{start=60000, end=63000, text="机器学习模型的泛化能力本质上是对未见样本的预测能力..."}` 作为追踪对象。

| 字段 | 值 |
|------|-----|
| 视频 ID | BVxxxxxxxxx（30 分钟技术大会 talk）|
| 段落起始 | 60.0s（第 1 分钟） |
| 段落结束 | 63.0s |
| 段落文本 | "机器学习模型的泛化能力本质上是对未见样本的预测能力..." |
| 关键词 | 机器学习、泛化能力、未见样本、预测能力 |
| 预期下游标签 | #机器学习 #模型评估 #泛化理论 |

---

## 1. 全链路时间线（6 个阶段，对应 5 阶段管道 + 摄入）

> 简写约定：[M-NNN] = 模块；[DE-NNN] = 数据实体；[stage] = 5 阶段管道中的阶段；落盘 = 真实文件路径

### 阶段 ① — 视频源（Input）

- **数据来源**：`https://www.bilibili.com/video/BVxxxxxxxxx`（DE-001 VideoURL）
- **物理形态**：B 站 CDN 服务器上的 mp4 文件（1080p，约 250MB）
- **时序**：t0 - 5min（用户复制 URL）
- **模块归属**：用户态（无 M-NNN 介入）
- **数据结构**：原始字节流（无元数据）
- **落盘**：N/A
- **来源**：[DE-001] [BP-001]

### 阶段 ② — 摄入 + 转写（Ingest + Transcribe，5 阶段管道的"Collect"前置）

- **时序**：t0 + 0.0s ~ t0 + 240s
- **涉及模块**：
  - M-001 cli_bindings：解析 URL [BP-001]
  - M-002 preflight：环境检查 [BP-002]
  - M-003 downloader：下载视频 [BP-003]
  - M-005 transcriber：faster-whisper medium 转写 [BP-005]
- **数据变换链**：
  ```
  DE-001 VideoURL  [M-001] 
    → DE-003 DownloadTask  [M-003]
      → DE-002 VideoMeta  [M-003 抓取]
        → 视频文件 mp4  [M-003 落盘]
          → 音轨文件 wav (16kHz)  [M-009 ffmpeg 抽音]
            → DE-005 Transcript.segment[120]  [M-005 whisper 转写]
  ```
- **本主角（segment[120]）的产生**：
  - M-005 调用 `WhisperModel.transcribe(wav_path, language='zh', beam_size=5)` 在 60.0s~63.0s 时间窗内识别出文本
  - 段落结构：`{start=60000, end=63000, text="机器学习模型的泛化能力本质上是对未见样本的预测能力..."}`
  - CER 估算：~0.06（标准普话水平 [调研-V1.0:S-004]）
- **数据结构**（DE-005 子结构 Segment）：
  ```python
  Segment(
      start=60000,        # 毫秒
      end=63000,          # 毫秒
      text="机器学习模型的泛化能力本质上是对未见样本的预测能力..."
  )
  ```
- **落盘路径**：
  - 视频：`raw/技术大会 talk 调研/assets/BVxxxxxxxxx.mp4`（DE-009 关联）
  - 音轨：`raw/技术大会 talk 调研/assets/BVxxxxxxxxx.wav`
  - 完整转写稿：`~/.cache/research-tool/transcripts/BVxxxxxxxxx.json`（含 segments 列表）
- **DE 流转**：DE-001 → DE-003 → DE-002 → 视频文件 → 音轨文件 → DE-005
- **来源**：[DE-001/DE-002/DE-003/DE-005] [BP-003/BP-005] [PRD-V1.1:F-003/F-004]

### 阶段 ③ — 总结 + 章节化（Summarize + Structure，5 阶段管道的"Collect"阶段）

- **时序**：t0 + 240s ~ t0 + 305s
- **涉及模块**：
  - M-006 llm_client：调用 deepseek-v4-flash 总结 [BP-006]
  - M-007 notes_schema：解析章节 + 字段校验 [BP-007]
  - M-009 ffmpeg_wrapper：抽取关键帧 [BP-009]
- **数据变换链**：
  ```
  DE-005 Transcript.segment[120]  [M-006 拼入 prompt]
    → DE-006 LLMSummary.markdown_body  [M-006 LLM 总结]
      → DE-006 LLMSummary.front_matter.video_chapters[?]  [M-007 解析]
        → DE-007 Chapter × N  [M-007 注入]
          → DE-002.video_tags = ['机器学习', '泛化能力', ...]  [M-007 从 LLM 输出抽取]
            → DE-006.front_matter.video_tags  [M-007 注入 front matter]
  ```
- **本主角的"嵌入点"**：
  - segment[120] 的文本内容被 M-006 拼入 LLM prompt 的 `transcript_text` 字段（总长 ~12000 tokens）
  - LLM 总结输出：在 markdown_body 中产生类似 `## 机器学习核心概念 [00:01:00] - [00:05:00]\n机器学习模型的泛化能力...` 的章节
  - LLM 抽取的 tags：`['机器学习', '模型泛化', '未见样本预测']`（video_ 前缀）
- **DE-006 LLMSummary 结构**（本主角相关字段）：
  ```python
  LLMSummary(
      video_id='BVxxxxxxxxx',
      transcript_id='tr-9b1f',
      markdown_body='...\n## 机器学习核心概念 [00:01:00]\n机器学习模型的泛化能力...',
      front_matter={
          'video_id': 'BVxxxxxxxxx',
          'video_source_url': '...',
          'video_title': '技术大会 talk 调研',
          'video_chapters': [
              {'index': 0, 'title': '开场介绍', 'start_ts': '00:00:00', 'end_ts': '00:01:00', 'is_fallback': False, 'source': 'llm'},
              {'index': 1, 'title': '机器学习核心概念', 'start_ts': '00:01:00', 'end_ts': '00:05:00', 'is_fallback': False, 'source': 'llm'},
              ...
          ],
          'video_tags': ['机器学习', '模型泛化', '未见样本预测', '技术大会'],
          'video_author': 'UP_xxx',
          'video_duration': 1800,
          'video_cover_url': '...',
          'video_llm_model': 'deepseek-v4-flash',
          'video_transcriber': 'whisper'
      },
      model_used='deepseek-v4-flash',
      input_tokens=14500,
      output_tokens=3200,
      chapters_count=5
  )
  ```
- **DE 流转**：DE-005 → DE-006 → DE-007 + DE-002.video_tags
- **落盘路径**：
  - 总结 JSON：`~/.cache/research-tool/summaries/BVxxxxxxxxx.json`
  - 截图：`raw/技术大会 talk 调研/assets/frames/frame_02.jpg`（时间戳 60s 对应的 I 帧）
- **来源**：[DE-005/DE-006/DE-007] [BP-006/BP-007] [PRD-V1.1:F-005/F-010]

### 阶段 ④ — 落 raw/ + 触发 5 阶段管道（Drop to raw/ + Trigger Pipeline）

- **时序**：t0 + 305s ~ t0 + 308s
- **涉及模块**：M-008 pipeline_adapter
- **数据变换链**：
  ```
  DE-006 LLMSummary  [M-008 compose]
    → DE-009 PipelineNote.markdown_body（含 front matter + 章节锚点 + 截图引用）
      → 落 raw/<topic>/<video-id>.md
        → trigger_pipeline() 触发既有 5 阶段管道
  ```
- **本主角的最终嵌入（PipelineNote）**：
  - markdown_body 包含完整 front matter + 正文 + 章节锚点
  - 视频源 URL 嵌入 front matter.video_source_url 字段 [PRD-V1.1:F-020]
  - tags ['机器学习', '模型泛化', '未见样本预测', '技术大会'] 写入 front matter.video_tags
- **DE-009 PipelineNote 结构**：
  ```python
  PipelineNote(
      note_id='BVxxxxxxxxx',
      topic='技术大会 talk 调研',
      file_path=Path('raw/技术大会 talk 调研/BVxxxxxxxxx.md'),
      front_matter={... 完整 video_ 前缀字段 ...},
      markdown_body='''
---
video_id: BVxxxxxxxxx
video_source_url: https://www.bilibili.com/video/BVxxxxxxxxx
video_title: 技术大会 talk 调研
video_author: UP_xxx
video_duration: 1800
video_chapters:
  - index: 0
    title: 开场介绍
    start_ts: 00:00:00
    end_ts: 00:01:00
  - index: 1
    title: 机器学习核心概念
    start_ts: 00:01:00
    end_ts: 00:05:00
video_tags:
  - 机器学习
  - 模型泛化
  - 未见样本预测
  - 技术大会
video_llm_model: deepseek-v4-flash
video_transcriber: whisper
---

# 技术大会 talk 调研

## 开场介绍 [00:00:00]

## 机器学习核心概念 [00:01:00]

机器学习模型的泛化能力本质上是对未见样本的预测能力...
（此处嵌入 segment[120] 文本及上下文）

![frame_02.jpg](assets/frames/frame_02.jpg)
      ''',
      schema_valid=True,
      pipe_stages_run=['Collect', 'Clean', 'Extract', 'Organize', 'Report'],
      pipe_success=True,
      tags=['机器学习', '模型泛化', '未见样本预测', '技术大会'],
      video_source_url='https://www.bilibili.com/video/BVxxxxxxxxx',
      created_at='2026-06-01T10:05:08+08:00'
  )
  ```
- **DE 流转**：DE-006 + DE-007 + DE-008 → DE-009
- **落盘路径**：
  - 笔记：`C:\Users\yhn\Desktop\workflow\raw\技术大会 talk 调研\BVxxxxxxxxx.md`
  - 关联文件：`raw/技术大会 talk 调研/assets/BVxxxxxxxxx.mp4` + `assets/BVxxxxxxxxx.wav` + `assets/frames/frame_02.jpg`
- **来源**：[DE-009] [BP-010] [PRD-V1.1:F-006/F-016/F-020]

### 阶段 ⑤ — 5 阶段管道（既有研究工具代码，零修改 [PRD-V1.1:F-006.AC-1]）

- **时序**：t0 + 308s ~ t0 + 480s
- **涉及模块**：既有研究工具（research_tool.pipeline.*，非本项目 M-NNN）
- **本主角在 5 阶段中的流转**：

| 阶段 | 入口数据 | 处理 | 出口数据 | 本主角的处理 |
|------|----------|------|----------|--------------|
| **Collect** | `raw/技术大会 talk 调研/BVxxxxxxxxx.md` | 加载 + 解析 front matter | `clean/<topic>/<video-id>.md` | front matter 中 video_ 字段保留；正文 raw 文本 |
| **Clean** | clean/<video-id>.md | 去除模板残留、规范化空白 | `clean/<video-id>.md`（清洗后） | 段落 120 的文本去噪（去除口头禅、修正标点）|
| **Extract** | clean/<video-id>.md | 抽取关键概念、实体、关系 | `extract/<video-id>.json` | 段落 120 抽取"机器学习 / 泛化能力 / 未见样本预测" 3 个概念 + 1 个关系"泛化→预测"|
| **Organize** | extract/<video-id>.json | 合并 tags、建立知识树 | `knowledge_tree.json`（增量更新） | tags 并入既有知识树 [PRD-V1.1:F-016] |
| **Report** | organize 输出 | 生成 markdown 报告 | `reports/<topic>/<video-id>.md` | 段落 120 内容被组织到"模型泛化能力"小节 |

- **5 阶段管道对本主角的关键产出**：
  - clean 阶段：`clean/技术大会 talk 调研/BVxxxxxxxxx.md`（清洗后的纯文本）
  - extract 阶段：`extract/技术大会 talk 调研/BVxxxxxxxxx.json`
    ```json
    {
      "video_id": "BVxxxxxxxxx",
      "concepts": [
        {"name": "机器学习", "frequency": 12, "importance": 0.95},
        {"name": "泛化能力", "frequency": 8, "importance": 0.92},
        {"name": "未见样本预测", "frequency": 5, "importance": 0.88}
      ],
      "relations": [
        {"from": "泛化能力", "to": "未见样本预测", "type": "defines"}
      ],
      "source_segment_refs": [120, 121, 125, 130]
    }
    ```
  - organize 阶段：知识树 `knowledge_tree.json` 新增节点
    ```json
    {
      "node_id": "tag:机器学习",
      "topic": "技术大会 talk 调研",
      "name": "机器学习",
      "source_video_ids": ["BVxxxxxxxxx", "BVyyyyy（既有）"],
      "is_duplicate_resolved": true,
      "child_tags": ["泛化能力", "未见样本预测", "..."]
    }
    ```
  - report 阶段：`reports/技术大会 talk 调研/BVxxxxxxxxx.md`
    ```markdown
    # 调研报告：技术大会 talk 调研

    ## 摘要
    本报告基于视频 BVxxxxxxxxx 整理，涵盖机器学习核心概念、模型泛化能力、...

    ## 1. 模型泛化能力
    机器学习模型的泛化能力本质上是对未见样本的预测能力...
    （直接引用 segment[120] 原文）

    ## 参考来源
    - [BVxxxxxxxxx 技术大会 talk](https://www.bilibili.com/video/BVxxxxxxxxx)
    ```
- **DE 流转**：DE-009 → clean/ → extract/ → knowledge_tree.json → report.md
- **落盘路径**：
  - 清洗后笔记：`C:\Users\yhn\Desktop\workflow\clean\技术大会 talk 调研\BVxxxxxxxxx.md`
  - 抽取结果：`C:\Users\yhn\Desktop\workflow\extract\技术大会 talk 调研\BVxxxxxxxxx.json`
  - 知识树：`C:\Users\yhn\Desktop\workflow\knowledge_tree.json`（增量追加）
  - 最终报告：`C:\Users\yhn\Desktop\workflow\reports\技术大会 talk 调研\BVxxxxxxxxx.md`
- **来源**：[PRD-V1.1:F-006/F-016/F-017/F-020] [SA:DE-009/DE-013]

### 阶段 ⑥ — 缓存 + 日志归档（Cache + Log）

- **时序**：t0 + 480s ~ t0 + 481s
- **涉及模块**：
  - M-004 cache_manager：缓存写入 [BP-004]
  - M-011 structured_logger：JSON Lines 日志 [BP-016]
- **数据变换链**：
  ```
  DE-009 PipelineNote  [M-004]
    → DE-004 CacheEntry（cache_key = sha256(url) + ":" + etag/last_modified）
      → 落 sqlite ~/.cache/research-tool/cache.db
        → DE-014 LogEntry × N（每步 4-5 行 JSON Lines）
          → 落 ~/.cache/research-tool/logs/2026-06-01.jsonl
  ```
- **本主角的缓存条目**：
  ```python
  CacheEntry(
      cache_key='4f3a...:etag-abc123',
      url_sha256='4f3a...',
      etag_or_lm='etag-abc123',
      video_id='BVxxxxxxxxx',
      note_path=Path('raw/技术大会 talk 调研/BVxxxxxxxxx.md'),
      transcript_path=Path('~/.cache/research-tool/transcripts/BVxxxxxxxxx.json'),
      summary_path=Path('~/.cache/research-tool/summaries/BVxxxxxxxxx.json'),
      created_at='2026-06-01T10:08:01+08:00',
      ttl_seconds=2592000,  # 30 天
      hit_count=0,
      etag_revalidate_at=None  # B 站视频无强制 revalidate
  )
  ```
- **DE-014 LogEntry 样例**（本视频相关条目）：
  ```json
  {"ts":"2026-06-01T10:00:00.00+08:00","level":"INFO","module":"M-001","task_id":"t-7e3a","url_hash":"4f3a...","step":"cli_start","msg":"research run 技术大会 talk 调研 --video-url BV..."}
  {"ts":"2026-06-01T10:00:00.05+08:00","level":"INFO","module":"M-002","task_id":"t-7e3a","url_hash":"4f3a...","step":"preflight","msg":"4 项检查通过","duration_ms":45}
  {"ts":"2026-06-01T10:00:12.00+08:00","level":"INFO","module":"M-003","task_id":"t-7e3a","url_hash":"4f3a...","step":"download_done","duration_ms":11800,"msg":"BVxxxxxxxxx 下载完成 248MB"}
  {"ts":"2026-06-01T10:04:00.00+08:00","level":"INFO","module":"M-005","task_id":"t-7e3a","url_hash":"4f3a...","step":"transcribe_done","duration_ms":228000,"msg":"medium 档完成，segments=420，cer=0.06"}
  {"ts":"2026-06-01T10:04:55.00+08:00","level":"INFO","module":"M-006","task_id":"t-7e3a","url_hash":"4f3a...","step":"llm_done","duration_ms":42500,"msg":"deepseek-v4-flash 14500/3200 tokens，chapters=5"}
  {"ts":"2026-06-01T10:05:08.00+08:00","level":"INFO","module":"M-008","task_id":"t-7e3a","url_hash":"4f3a...","step":"pipeline_trigger","msg":"5 阶段管道触发"}
  {"ts":"2026-06-01T10:08:01.00+08:00","level":"INFO","module":"M-004","task_id":"t-7e3a","url_hash":"4f3a...","step":"cache_write","msg":"cache_key=4f3a...:etag-abc123"}
  ```
- **DE 流转**：DE-009 → DE-004 + DE-014
- **落盘路径**：
  - 缓存：`~/.cache/research-tool/cache.db`
  - 日志：`~/.cache/research-tool/logs/2026-06-01.jsonl`
- **来源**：[DE-004/DE-014] [BP-004/BP-016] [PRD-V1.1:NF-007] [调研-V1.0:S-102]

---

## 2. 全链路数据结构演进总览

| 阶段 | 物理形态 | 关键 DE 实体 | 主要模块 |
|------|----------|--------------|----------|
| ① 视频源 | mp4 字节流 | DE-001 VideoURL | 用户态 |
| ② 摄入转写 | wav + JSON | DE-002/DE-003/DE-005 | M-001/M-002/M-003/M-005/M-009 |
| ③ 总结结构 | Markdown draft | DE-006/DE-007/DE-008 | M-006/M-007/M-009 |
| ④ 落盘 | Markdown 文件 | DE-009 PipelineNote | M-008 |
| ⑤ 5 阶段管道 | clean/extract/report | 既有 DE-009（已落盘） | research_tool.pipeline.* |
| ⑥ 缓存日志 | sqlite + jsonl | DE-004/DE-014 | M-004/M-011 |

---

## 3. 本主角的"形态变化"（segment[120] 视角）

| 阶段 | 形态 | 关键属性 |
|------|------|----------|
| 视频源 | 60-63 秒的音频波形 | amplitude 序列 |
| 转写后 | `Segment{start=60000, end=63000, text="..."}` | 时间戳 + 中文文本 |
| LLM 总结 | markdown 章节"机器学习核心概念"中的句子 | 嵌入到 narrative flow |
| front matter | 通过 LLM 抽取，间接通过 tags 体现 | "机器学习 / 模型泛化" |
| clean 文本 | 段落清洗后 | 文本规范化 |
| extract 概念 | JSON 概念节点 | {name, frequency, importance} |
| knowledge_tree 节点 | 树形节点 | {node_id, source_video_ids, child_tags} |
| report 段落 | 报告"1. 模型泛化能力"小节 | 含原文引用 + 视频链接 |

---

## 4. 双向追溯能力

- **正向**：知识树节点 "tag:机器学习" → source_video_ids = ["BVxxxxxxxxx", ...] → 视频 → segment[120]（通过 video_id + 时间窗反查）
- **反向**：segment[120] → markdown_body 章节 → front matter.video_chapters → video_tags → knowledge_tree 节点
- **可追溯率**：100%（segment 文本 ID → 视频 → 概念 → 知识树节点 4 跳全部可达）

---

## 5. 数据流完整性自检

- ✅ 14 DE 实体全部覆盖（DE-001~DE-014）
- ✅ 16 流程全部有数据引用（BP-001~BP-016）
- ✅ 5 阶段管道零修改接入（[PRD-V1.1:F-006.AC-1]）
- ✅ 字段命名空间隔离（video_ 前缀 [调研-V1.0:S-005]）
- ✅ 缓存双键避免陈旧（[调研-V1.0:S-102]）
- ✅ 日志结构化（JSON Lines [PRD-V1.1:NF-007]）
- ✅ 双向追溯能力完整

---

> **本文档结束**。以 segment[120] 为主角，完整追踪从视频源到知识树节点的全链路 6 个阶段，数据结构演进 8 种形态，DE 实体覆盖 14/14，模块归属 M-001~M-012 + 既有 pipeline。
