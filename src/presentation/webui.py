"""research-tool 的可视化 Web 界面（Gradio），风格对标 pdf2zh。

左栏输入与设置，右栏流式进度 + 报告渲染 + 文件下载。
启动：`research ui` 或 `python -m research_tool.webui`，浏览器开 127.0.0.1:7861。

V1.1：新增「视频调研」模式 —— 粘贴 B 站 / YouTube URL（每行 1 个），
自动完成 下载 → 转写 → 笔记 → 清洗 → 抽取 → 知识树 → 报告 全流程。
"""

from __future__ import annotations

import asyncio
import os
import queue
import threading
from pathlib import Path

from ..domain.config import load_config
from ..common.logging_config import get_logger
from ..application.pipeline import ResearchPipeline
from ..application.video_pipeline import VideoPipeline
from ..common.slug import slugify

logger = get_logger(__name__)

# 与 start.bat 一致：从 .env 载入密钥（脚本目录 / 上级 / 用户目录）
_ENV_KEYS = {
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "tavily_api_key": "TAVILY_API_KEY",
    "openai_api_key": "OPENAI_API_KEY",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
}


def _load_env() -> None:
    for cand in [Path(".env"), Path("../.env"), Path.home() / ".env"]:
        if not cand.exists():
            continue
        for line in cand.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env = _ENV_KEYS.get(k.strip().lower())
            if env and not os.environ.get(env):
                os.environ[env] = v.strip()
        break


def _run_threaded(cfg, topic: str, q: "queue.Queue") -> None:
    async def go():
        pipe = ResearchPipeline(cfg)
        async for ev in pipe.stream(topic):
            q.put(("event", ev))
        q.put(("result", pipe._result))

    try:
        asyncio.run(go())
    except Exception as e:  # noqa: BLE001 - 错误回传到 UI
        q.put(("error", str(e)))
    finally:
        q.put(("done", None))


def _run_threaded_video(
    topic: str, urls: list[str], work_dir: str, q: "queue.Queue"
) -> None:
    """视频模式后端：跑 VideoPipeline（内部含 4 阶段管道）。"""
    async def go():
        q.put(("log", f"▶ 启动视频摄入（共 {len(urls)} 个 URL）"))
        pipe = VideoPipeline(topic=topic, work_dir=work_dir, run_pipeline=True)
        result = await pipe.process_urls(urls)

        # 逐 URL 进度
        for r in result.results:
            icon = "✓" if r.status == "success" else "✗"
            tail = f" → {r.markdown_path.name}" if r.markdown_path else f"（{r.error}）"
            q.put(("log", f"  {icon} {r.url}{tail}"))
        q.put(
            ("log", f"✓ 视频摄入：{result.success_count} 成功 / {result.failed_count} 失败")
        )

        # 4 阶段管道结果
        if result.stages_result:
            sr = result.stages_result
            stages = ", ".join(sr.stages_run) or "（无）"
            q.put(("log", f"✓ 4 阶段管道：{stages}（{sr.duration_ms / 1000:.1f}s）"))
        q.put(("result", result))

    try:
        asyncio.run(go())
    except Exception as e:  # noqa: BLE001
        q.put(("error", str(e)))
    finally:
        q.put(("done", None))


def _parse_video_urls(s: str | None) -> list[str]:
    """多行文本 → URL 列表（去空行 + strip）。"""
    return [v.strip() for v in (s or "").splitlines() if v.strip()]


_STAGE_CN = {
    "collect": "采集", "deepen": "反偏差深挖", "clean": "清洗", "extract": "抽取",
    "organize": "构建知识树", "report": "生成报告",
    "backward": "反向传播",   # P2-6：评估知识树质量 → 修正查询 → 回到 collect
}
_STATUS_ICON = {
    "started": "▶", "completed": "✓", "skipped": "⏭", "failed": "✗", "progress": "…",
}


def _csv(s: str | None) -> list[str]:
    return [v.strip() for v in (s or "").split(",") if v.strip()]


def run_web(  # noqa: PLR0915 - Gradio 事件处理，单函数承载多模式分支
    mode, topic, engines, rounds, style, max_nodes, min_nodes, do_extract,
    pdf_path, mineru_cmd, translate, provider, model, base_url, api_key, work_dir,
    do_deepen=True,
    # P1/P2 新增
    core_kw="", facets_csv="", from_year=None, to_year=None,
    deep_search=False, profile_iterations=1,
    relevance_filter=False, backward_rounds=0,
    # V1.1 视频调研
    video_urls="",
):
    """Gradio 事件处理：流式产出 (日志, 报告markdown, 文件列表, 输出目录)。"""
    topic = (topic or "").strip()
    if not topic:
        yield "⚠ 请先填写调研主题。", "", None, ""
        return

    # ===== V1.1 视频模式分支 ===== #
    if mode == "视频调研":
        urls = _parse_video_urls(video_urls)
        if not urls:
            yield "⚠ 视频模式需填写至少 1 个 B 站 / YouTube 链接（每行一个）。", "", None, ""
            return
        out_dir = str(Path(work_dir or "./research-output") / slugify(topic))
        log: list[str] = [
            f"主题：{topic}",
            f"模式：视频调研（{len(urls)} 个 URL）",
            "流程：下载 → 转写 → 笔记 → 清洗 → 抽取 → 知识树 → 报告",
            "—" * 20,
        ]
        yield "\n".join(log), "", None, out_dir

        q: queue.Queue = queue.Queue()
        threading.Thread(
            target=_run_threaded_video,
            args=(topic, urls, work_dir or "./research-output", q),
            daemon=True,
        ).start()

        while True:
            kind, payload = q.get()
            if kind == "log":
                log.append(payload)
                yield "\n".join(log), "", None, out_dir
            elif kind == "result":
                log.append(f"\n📊 视频报告已生成 → {out_dir}/report.md")
            elif kind == "error":
                log.append(f"❌ 视频调研出错：{payload}")
                yield "\n".join(log), "", None, out_dir
                return
            elif kind == "done":
                break

        report = Path(out_dir) / "report.md"
        md = report.read_text(encoding="utf-8") if report.exists() else "（未生成报告）"
        tree_dir = Path(out_dir) / "tree"
        files: list[str] = []
        if report.exists():
            files.append(str(report))
        if tree_dir.exists():
            files += [str(p) for p in sorted(tree_dir.glob("*.md"))]
        log.append("\n✅ 完成！报告见右侧，可下载全部文件。")
        yield "\n".join(log), md, files or None, out_dir
        return

    # ===== 网页 / PDF 模式原有逻辑 ===== #
    stages = ["collect"]
    # 反偏差深挖（仅网页调研有意义；PDF 摄取无需深挖）
    if do_deepen and mode != "PDF 调研":
        stages.append("deepen")
    stages.append("clean")
    if do_extract:
        stages.append("extract")
    stages += ["organize", "report"]

    # collector：P1 核心词/维度标签 + P2 时间窗口 + deep-search
    collector_cfg: dict = {
        "search_engines": list(engines) or ["web"],
        "search_rounds": int(rounds),
    }
    if (core_kw or "").strip():
        collector_cfg["core_keyword"] = core_kw.strip()
    if (facets_csv or "").strip():
        collector_cfg["facets"] = _csv(facets_csv)
    if from_year:
        collector_cfg["from_year"] = int(from_year)
    if to_year:
        collector_cfg["to_year"] = int(to_year)
    if deep_search:
        collector_cfg["deep_search"] = True
    # deepen：P2-4 画像迭代轮数（≥2 启用同名消歧+时间线回溯）
    deepen_cfg: dict = {}
    if int(profile_iterations) >= 2:
        deepen_cfg["profile_iterations"] = int(profile_iterations)
    # cleaner：P2-5 LLM 相关性过滤
    cleaner_cfg: dict = {}
    if relevance_filter:
        cleaner_cfg["relevance_filter"] = True

    overrides: dict = {
        "topic": topic,
        "work_dir": work_dir or "./research-output",
        "stages": stages,
        "collector": collector_cfg,
        "organizer": {"max_nodes": int(max_nodes), "min_nodes": int(min_nodes)},
        "reporter": {"style": style},
    }
    if deepen_cfg:
        overrides["deepen"] = deepen_cfg
    if cleaner_cfg:
        overrides["cleaner"] = cleaner_cfg
    # P2-6 反向传播轮数：>0 启用
    if int(backward_rounds) > 0:
        overrides["max_backward_rounds"] = int(backward_rounds)
    llm: dict = {}
    if provider:
        llm["provider"] = provider
    if model:
        llm["model"] = model
    if base_url:
        llm["base_url"] = base_url
    if api_key:
        llm["api_key"] = api_key
    if llm:
        overrides["llm"] = llm

    if mode == "PDF 调研":
        if not (pdf_path or "").strip():
            yield "⚠ PDF 模式需填写 PDF 文件或文件夹路径。", "", None, ""
            return
        overrides["pdf_dir"] = pdf_path.strip()
        pi: dict = {"translate": bool(translate)}
        if (mineru_cmd or "").strip():
            pi["mineru_cmd"] = mineru_cmd.strip()
        overrides["pdf_ingest"] = pi

    try:
        cfg = load_config(overrides=overrides)
    except Exception as e:  # noqa: BLE001
        yield f"⚠ 配置错误：{e}", "", None, ""
        return

    topic_dir = Path(cfg.work_dir) / slugify(topic)
    q: queue.Queue = queue.Queue()
    threading.Thread(target=_run_threaded, args=(cfg, topic, q), daemon=True).start()

    log: list[str] = [f"主题：{topic}", f"流程：{' → '.join(stages)}", "—" * 20]
    yield "\n".join(log), "", None, str(topic_dir)

    failed = None
    while True:
        kind, payload = q.get()
        if kind == "event":
            ev = payload
            icon = _STATUS_ICON.get(ev.status, "·")
            name = _STAGE_CN.get(ev.stage, ev.stage)
            log.append(f"{icon} [{name}] {ev.message}")
            if ev.status == "failed":
                failed = ev.stage
            yield "\n".join(log), "", None, str(topic_dir)
        elif kind == "error":
            log.append(f"❌ 运行出错：{payload}")
            yield "\n".join(log), "", None, str(topic_dir)
        elif kind == "result":
            pass
        elif kind == "done":
            break

    report = topic_dir / "report.md"
    if failed:
        log.append(f"\n流程在「{_STAGE_CN.get(failed, failed)}」阶段失败。")
        yield "\n".join(log), "", None, str(topic_dir)
        return

    md = report.read_text(encoding="utf-8") if report.exists() else "（未生成报告）"
    tree_dir = topic_dir / "tree"
    files: list[str] = []
    if report.exists():
        files.append(str(report))
    if tree_dir.exists():
        files += [str(p) for p in sorted(tree_dir.glob("*.md"))]
    log.append("\n✅ 完成！报告见右侧，可下载全部文件。")
    yield "\n".join(log), md, files or None, str(topic_dir)


def build_ui():  # noqa: PLR0915 - Gradio 布局 + 事件绑定，单函数承载 UI 组装
    import gradio as gr

    with gr.Blocks(title="research-tool 调研工具") as app:
        gr.Markdown(
            "# 🔍 research-tool 调研工具\n"
            "给定**主题**或一批**PDF**，自动完成 搜索/解析 → 清洗 → 知识树 → 调研报告。"
        )
        with gr.Row():
            # -------- 左栏：输入与设置 -------- #
            with gr.Column(scale=1, min_width=380):
                gr.Markdown("### 1) 模式与主题")
                mode = gr.Radio(
                    ["网页调研", "PDF 调研", "视频调研"], value="网页调研", label="数据来源"
                )
                topic = gr.Textbox(
                    label="调研主题", placeholder="如：扩散模型综述 / 离散数学典型代数系统"
                )

                with gr.Group(visible=True) as web_group:
                    engines = gr.CheckboxGroup(
                        ["web", "arxiv", "openalex", "crossref", "tavily",
                         "semantic_scholar", "wikipedia", "github",
                         "pubmed", "google_news"],
                        value=["web", "openalex"], label="搜索来源（可多选）",
                    )
                    rounds = gr.Slider(1, 3, value=2, step=1, label="搜索轮次（多轮关键词）")
                    do_deepen = gr.Checkbox(
                        label="反偏差深挖（实体拆分 + 缺口补搜，更慢更全）", value=True
                    )
                    # P1：两阶段锚定/去锚
                    core_kw = gr.Textbox(
                        label="核心词（去锚锚点，可空）",
                        placeholder="topic 含机构名时填人物名，如 康怡琳",
                    )
                    facets_csv = gr.Textbox(
                        label="维度标签（逗号分隔，仅去锚阶段生效）",
                        placeholder="博士,论文,南洋理工",
                    )
                    # P2-2/P2-3：时间窗口 + deep-search
                    with gr.Row():
                        from_year = gr.Number(
                            label="发表年≥", value=None, precision=0,
                        )
                        to_year = gr.Number(
                            label="发表年≤", value=None, precision=0,
                        )
                    deep_search = gr.Checkbox(
                        label="深搜（多排序 × 多页翻页，更全但更慢）", value=False,
                    )

                with gr.Group(visible=False) as pdf_group:
                    pdf_path = gr.Textbox(
                        label="PDF 文件 / 文件夹路径",
                        placeholder=r"C:\Users\you\papers 或 单个 .pdf",
                    )
                    translate = gr.Checkbox(label="把英文 PDF 翻译成中文", value=False)
                    mineru_cmd = gr.Textbox(
                        label="mineru 路径（留空走 PATH）",
                        placeholder=r"C:\Users\you\pdf2zh\.venv\Scripts\mineru.exe",
                    )

                with gr.Group(visible=False) as video_group:
                    gr.Markdown(
                        "**V1.1 视频调研** — 粘贴 B 站 / YouTube 链接，每行 1 个，"
                        "自动完成下载 + 转写 + 笔记 + 调研报告全流程。"
                    )
                    video_urls = gr.Textbox(
                        label="视频 URL（每行 1 个，1-10 个）",
                        placeholder="https://www.bilibili.com/video/BV1xxxxxxxxx\nhttps://www.youtube.com/watch?v=xxxxxxxxxxx",
                        lines=4,
                    )

                gr.Markdown("### 2) 输出设置")
                style = gr.Dropdown(
                    ["report", "feasibility", "review", "article"],
                    value="report", label="报告风格",
                )
                with gr.Row():
                    min_nodes = gr.Slider(2, 7, value=4, step=1, label="最少节点")
                    max_nodes = gr.Slider(3, 10, value=7, step=1, label="最多节点")
                do_extract = gr.Checkbox(
                    label="启用实体/三元组抽取（更慢，更细）", value=False
                )

                with gr.Accordion("反偏差与质量（高级）", open=False):
                    profile_iterations = gr.Slider(
                        1, 4, value=1, step=1,
                        label="画像迭代轮数（≥2 启用同名消歧 + 时间线回溯）",
                    )
                    relevance_filter = gr.Checkbox(
                        label="LLM 相关性过滤（按主题给文档评分，剔低分；显著降噪）",
                        value=False,
                    )
                    backward_rounds = gr.Slider(
                        0, 3, value=0, step=1,
                        label="反向传播轮数（>0 启用知识树质量评估循环，每轮重跑后续阶段）",
                    )

                with gr.Accordion("LLM 设置（默认读环境变量）", open=False):
                    provider = gr.Dropdown(
                        ["deepseek", "openai", "ollama", "anthropic"],
                        value="deepseek", label="provider",
                    )
                    model = gr.Textbox(value="deepseek-chat", label="模型")
                    base_url = gr.Textbox(
                        label="base_url（留空用默认）",
                        placeholder="https://api.deepseek.com/v1",
                    )
                    api_key = gr.Textbox(
                        label="API Key（留空读环境变量）", type="password"
                    )
                work_dir = gr.Textbox(value="./research-output", label="输出目录")

                run_btn = gr.Button("▶ 开始调研", variant="primary", size="lg")

            # -------- 右栏：进度与输出 -------- #
            with gr.Column(scale=2, min_width=520):
                gr.Markdown("### 进度")
                log_box = gr.Textbox(
                    label="运行日志", lines=12, max_lines=20, interactive=False
                )
                out_dir_box = gr.Textbox(label="输出目录", interactive=False)
                gr.Markdown("### 报告")
                report_md = gr.Markdown(label="调研报告")
                files = gr.File(label="下载（报告 + 知识树节点）", file_count="multiple")

        def _toggle(m):
            return (
                gr.update(visible=(m == "网页调研")),
                gr.update(visible=(m == "PDF 调研")),
                gr.update(visible=(m == "视频调研")),
            )

        mode.change(_toggle, inputs=mode, outputs=[web_group, pdf_group, video_group])

        run_btn.click(
            run_web,
            inputs=[
                mode, topic, engines, rounds, style, max_nodes, min_nodes, do_extract,
                pdf_path, mineru_cmd, translate, provider, model, base_url, api_key,
                work_dir, do_deepen,
                # P1/P2 新增
                core_kw, facets_csv, from_year, to_year,
                deep_search, profile_iterations,
                relevance_filter, backward_rounds,
                # V1.1 视频调研
                video_urls,
            ],
            outputs=[log_box, report_md, files, out_dir_box],
            api_name="run",
        )
    return app


def _resolve_port(preferred: int) -> int:
    """返回一个可用端口。优先用指定端口；不可用时让操作系统分配一个
    保证空闲、且不在 Windows 保留段内的端口（bind 到 0）。"""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))  # 0 = 由 OS 选一个空闲端口
        return s.getsockname()[1]


def main(server_port: int = 7861, inbrowser: bool = True) -> None:
    _load_env()
    app = build_ui()
    port = _resolve_port(server_port)
    if port != server_port:
        logger.warning("端口 %d 不可用（可能被系统保留），改用 %d", server_port, port)
    logger.info("Web 界面: http://127.0.0.1:%d", port)
    # 默认只绑本机，避免把 API Key 暴露到公网
    app.launch(server_name="127.0.0.1", server_port=port, inbrowser=inbrowser)


if __name__ == "__main__":
    main()
