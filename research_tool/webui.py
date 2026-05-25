"""research-tool 的可视化 Web 界面（Gradio），风格对标 pdf2zh。

左栏输入与设置，右栏流式进度 + 报告渲染 + 文件下载。
启动：`research ui` 或 `python -m research_tool.webui`，浏览器开 127.0.0.1:7861。
"""

from __future__ import annotations

import asyncio
import os
import queue
import threading
from pathlib import Path

from .config import load_config
from .pipeline import ResearchPipeline
from .slug import slugify

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


_STAGE_CN = {
    "collect": "采集", "deepen": "反偏差深挖", "clean": "清洗", "extract": "抽取",
    "organize": "构建知识树", "report": "生成报告",
}
_STATUS_ICON = {
    "started": "▶", "completed": "✓", "skipped": "⏭", "failed": "✗", "progress": "…",
}


def run_web(
    mode, topic, engines, rounds, style, max_nodes, min_nodes, do_extract,
    pdf_path, mineru_cmd, translate, provider, model, base_url, api_key, work_dir,
    do_deepen=True,
):
    """Gradio 事件处理：流式产出 (日志, 报告markdown, 文件列表, 输出目录)。"""
    topic = (topic or "").strip()
    if not topic:
        yield "⚠ 请先填写调研主题。", "", None, ""
        return

    stages = ["collect"]
    # 反偏差深挖（仅网页调研有意义；PDF 摄取无需深挖）
    if do_deepen and mode != "PDF 调研":
        stages.append("deepen")
    stages.append("clean")
    if do_extract:
        stages.append("extract")
    stages += ["organize", "report"]

    overrides: dict = {
        "topic": topic,
        "work_dir": work_dir or "./research-output",
        "stages": stages,
        "collector": {
            "search_engines": list(engines) or ["web"],
            "search_rounds": int(rounds),
        },
        "organizer": {"max_nodes": int(max_nodes), "min_nodes": int(min_nodes)},
        "reporter": {"style": style},
    }
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


def build_ui():
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
                    ["网页调研", "PDF 调研"], value="网页调研", label="数据来源"
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
            )

        mode.change(_toggle, inputs=mode, outputs=[web_group, pdf_group])

        run_btn.click(
            run_web,
            inputs=[
                mode, topic, engines, rounds, style, max_nodes, min_nodes, do_extract,
                pdf_path, mineru_cmd, translate, provider, model, base_url, api_key,
                work_dir, do_deepen,
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
        print(f"端口 {server_port} 不可用（可能被系统保留），改用 {port}")
    print(f"Web 界面: http://127.0.0.1:{port}")
    # 默认只绑本机，避免把 API Key 暴露到公网
    app.launch(server_name="127.0.0.1", server_port=port, inbrowser=inbrowser)


if __name__ == "__main__":
    main()
