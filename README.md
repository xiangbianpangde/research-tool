# research-tool

给定主题，通过多源检索、正文清洗、事实抽取、证据检视与补搜，生成可追溯的报告和知识树。正式入口统一使用 `ResearchPipeline` 九阶段管线。

历史恢复基线为 `4ab1412`，开发来自 `codex/nine-stage-only`。当前验证与分支见 [STATUS.md](STATUS.md)；来源与裁剪边界见 [九段裁剪记录](docs/plan/NINE-STAGE-ONLY-TRIM-PLAN.md)。历史 52.77 分尚未在本实现上复现。

## 九阶段

| 阶段 | 职责 |
|---|---|
| ① collect | 多源采集或摄入 Markdown/PDF；内部保留实体拆分、画像、时间线、消歧与查询扩展 |
| ② clean | 正文降噪、近似去重、50,000 字符上限；可配置 LLM 相关性过滤 |
| ③ extract | 抽取事实，绑定原文、来源、正文哈希与偏移 |
| ④ knowledge | 聚合事实、来源和主题节点，拒绝无效引用 |
| ⑤ inspect | 检查独立来源覆盖，检视证据缺口、冲突与限制 |
| ⑥ targeted | 依据缺口实际补搜，新正文再清洗与抽取 |
| ⑦ merge | 保留原图并融合增量证据，检查摘要及身份冲突 |
| ⑧ qgate | 按 high / total findings 阈值确定 CONTINUE 或 STOP_SUCCESS |
| ⑨ report | 章节规划、正文综合、逐段模型支持核验，生成报告与知识树 |

初次知识构建后先 inspect/qgate；CONTINUE 才 targeted → merge 并重新检视。无缺口时记录补搜、合并为不适用。只有 STOP_SUCCESS 才出报告；预算耗尽、取消或错误不发布成功成果。既有模型缺口只有在记录新增事实 ID 及解决理由后才可关闭，不能靠下一轮省略而消失。

阶段使用 Protocol v1 JSON envelope；runtime-state.json 记录输入指纹、run_id、阶段产物摘要与 CAS。`--resume` 只复用输入和已提交文件哈希均有效的缓存。损坏缓存使恢复失败；输入变化建立新运行。

所有成果位于指定 output 的主题目录：`artifacts/<run_id>/` 保存阶段 envelope，`runs/<run_id>/initial/` 保存首次 raw/clean/extracted，`targeted-N/` 保存补搜资料。最终 `runs/<run_id>/final/` 包含 report.md、sources.json、tree/00-主表.md、tree/N*.md 和 tree/knowledge.json。根目录 run-summary.json 是当前索引；消费者校验运行身份、runtime 与全部 final 哈希。历史根目录报告和知识树保留原样。

引用覆盖率 1.0 表示报告段落绑定了通过原文跨度校验的事实并通过模型支持检查。标题和正文均接受支持核验。模型可能判断错误；这不是语义正确性的形式化证明或调研质量评分。

## 安装与配置

Python >= 3.11。安装核心：`pip install -e ".[llm,search]"`；WebUI 增加 ui extra；PDF/视频等使用 all extra 及相应本地工具。`research setup` 提供 minimal/recommended/full 档位，`research setup --check-only` 检查环境。

CLI overrides > 环境变量 > YAML > 默认值。完整模板见 [docs/config.example.yaml](docs/config.example.yaml)。密钥放本地 .env 或环境变量，不提交 Git；`research config` 显示脱敏配置。

full、standard、deep 执行同一九段链，collector.depth 必须至少为 2；搜索摘要和未转写的视频发现记录不能作为正文证据。brief/fast、--skip、阶段重排、关闭 extract、旧 deepen/max_backward_rounds 和 legacy/shadow 开关报错。画像策略使用 `collector.strategies`；补搜上限与时限使用 `nine_loop.max_rounds`、`nine_loop.wall_s_max`。

## CLI 与 WebUI

```bash
research run "固态电池商业化现状" -s tavily --mode full --output ./research-output
research run "Graph Neural Networks" -s openalex -s arxiv --from-year 2022 --to-year 2026
research run "电池循环寿命" --input-dir ./materials/raw
research run "长上下文技术" --pdf-dir ./papers --translate
research run "Transformer 原理" --video-url "https://www.bilibili.com/video/BV1xx411c7mD"
research run "电池循环寿命" --dry-run
research status ./research-output/topic-slug
research wiki-stage ./research-output/topic-slug --build --package-output ./wiki-packages
research ui --port 7861
```

搜索保留 DuckDuckGo、Tavily、OpenAlex、Crossref、arXiv、Semantic Scholar、PubMed、Wikipedia、GitHub、Google News、CVPR/DBLP、Bilibili、YouTube、X 和 OpenCLI。`--official-url` 指定先抓取的官方来源，成功后扩展学术检索；其余配置见 `research run --help`。

独立 collect/clean/extract/organize/report 命令和 quick_collect 已移除。ingest-pdf 与 ocr-engines 是输入准备工具，正式报告使用 run。wiki-stage 只打包当前成功 final；旧 publish-wiki 不支持九段结果。

WebUI 固定九阶段，成功后展示当前报告和知识树下载，失败清空上次成果。视频笔记面板选择“同时生成完整九段调研报告”后使用同一主链。

## SDK 与开发

```python
from research_tool import ResearchPipeline, load_config

config = load_config(overrides={
    "topic": "具身智能感知与规划",
    "work_dir": "./research-output",
    "collector": {"search_engines": ["tavily", "openalex"]},
})
# 在 async 函数中：
result = await ResearchPipeline(config).run()
if result.verdict == "STOP_SUCCESS" and result.report_result:
    print(result.report_result.report_path)
```

便捷入口 `research(topic, work_dir=..., llm_provider=..., llm_model=..., config_path=...)`；流式进度使用 ResearchPipeline.stream()。编排在 application/pipeline.py，证据、图谱、合并、报告和状态在 nine_loop/，外部能力在 infrastructure/。旧 Organizer/Reporter 底层工具仍有有效测试，但不是第二条正式执行链。

```bash
python -B -m pytest -q -p no:cacheprovider research_tool/tests
ruff check --no-cache research_tool/ pyproject.toml
python -B scripts/smoke_e2e.py --output /tmp/research-nine-smoke
```

冒烟仅模拟搜索、抓取和模型外部 I/O，实际执行生产管线，含一轮补搜和无模型调用的恢复。它不验证联网表现、报告质量或历史分数。完整回归与真实界面证据见 STATUS。

使用 [MIT License](LICENSE)。
