"""Behavior coverage for knowledge-tree organization and reporting."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_tool.domain.errors import LLMAuthenticationError
from research_tool.domain.models import OrganizeResult, OrganizerConfig, ReporterConfig
from research_tool.infrastructure.stages.organizer import (
    Organizer,
    _Node,
    _NodePlan,
    _SourceDoc,
    _chunk,
    _filename_safe,
    _load_clean_docs,
    _strip_meta_body,
    organize,
)
from research_tool.infrastructure.stages.reporter import (
    Reporter,
    _count_words,
    _md_to_html,
    report,
)


class ScriptLLM:
    def __init__(self, structured=None, chats=None) -> None:
        self.structured = structured or (lambda prompt, schema: schema())
        self.chats = list(chats or ["generated body"])
        self.prompts: list[str] = []

    async def chat_structured(self, prompt, schema, system=None):
        self.prompts.append(prompt)
        value = self.structured(prompt, schema)
        if isinstance(value, BaseException):
            raise value
        return value

    async def chat(self, prompt, system=None, temperature=None):
        self.prompts.append(prompt)
        value = self.chats.pop(0) if self.chats else "generated body"
        if isinstance(value, BaseException):
            raise value
        return value


def write_clean(directory: Path, name: str = "01-paper.md", *, body: str = "paper body") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(
        "<!-- title: Paper title -->\n<!-- source: https://paper.test -->\n\n" + body,
        encoding="utf-8",
    )
    return path


def organize_result(tree: Path, nodes: list[Path]) -> OrganizeResult:
    return OrganizeResult(main_table=tree / "00-主表.md", nodes=nodes, tree_dir=tree)


def test_organizer_text_helpers_and_clean_document_loading(tmp_path):
    assert _filename_safe('  A / B:*?"<>| C  ') == "A-B-C"
    assert _filename_safe(" / ") == "node"
    assert _strip_meta_body("\n<!-- a -->\n\nBody\nTail") == "Body\nTail"
    assert _strip_meta_body("") == ""
    assert _chunk("abcde", 2) == ["ab", "cd", "e"]
    assert _chunk("", 2) == [""]

    clean = tmp_path / "clean"
    write_clean(clean)
    (clean / "02-empty.md").write_text("<!-- title: Empty -->\n", encoding="utf-8")
    (clean / "03-plain.md").write_text("plain", encoding="utf-8")
    docs = _load_clean_docs(clean)
    assert [(d.sid, d.title, d.url, d.text) for d in docs] == [
        ("01", "Paper title", "https://paper.test", "paper body"),
        ("03", "", "", "plain"),
    ]


@pytest.mark.asyncio
async def test_run_clean_map_reduce_writes_nodes_cross_refs_and_source_index(tmp_path):
    clean = tmp_path / "clean"
    write_clean(clean)

    def structured(prompt, schema):
        if schema.__name__ == "_Points":
            return schema(points=["method", "result"])
        if schema.__name__ == "_NodePlan":
            return schema(
                nodes=[
                    {"title": "A / method", "core_question": "how"},
                    {"title": "B", "core_question": "why"},
                ],
                main_thread="A to B",
            )
        return schema()

    llm = ScriptLLM(structured, chats=["node A", "node B", "main body"])
    result = await Organizer(OrganizerConfig(min_nodes=2, max_nodes=2)).run(
        clean, llm, tmp_path, topic="topic"
    )

    assert [p.name for p in result.nodes] == ["N1-A-method.md", "N2-B.md"]
    assert result.cross_refs == {"A / method": ["B"], "B": ["A / method"]}
    main = result.main_table.read_text(encoding="utf-8")
    assert "## \u6765\u6e90\u7d22\u5f15" in main
    assert "\u6765\u6e9001\uff1aPaper title" in main


@pytest.mark.asyncio
async def test_run_structured_path_reads_optional_relations_and_triples(tmp_path):
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    (extracted / "entities.json").write_text('[{"name":"E"}]', encoding="utf-8")
    (extracted / "relations.json").write_text('[{"subject":"E"}]', encoding="utf-8")
    (extracted / "triples.json").write_text('[{"head":"E"}]', encoding="utf-8")

    def structured(prompt, schema):
        return schema(nodes=[{"title": "Only", "core_question": "what"}], main_thread="one")

    llm = ScriptLLM(structured, chats=["node", "main"])
    result = await Organizer(OrganizerConfig(min_nodes=1, max_nodes=1)).run(extracted, llm)
    assert result.main_table.exists()
    assert "\"relations\"" in llm.prompts[0]
    assert "\"triples\"" in llm.prompts[0]

    (extracted / "relations.json").unlink()
    (extracted / "triples.json").unlink()
    evidence = Organizer()._structured_evidence(extracted)
    assert '"relations": []' in evidence
    assert '"triples": []' in evidence


@pytest.mark.asyncio
async def test_summarize_doc_chunks_caps_points_and_uses_url_fallback(monkeypatch):
    import research_tool.infrastructure.stages.organizer as module

    monkeypatch.setattr(module, "_DOC_CHUNK", 1)
    monkeypatch.setattr(module, "_MAX_CHUNKS_PER_DOC", 20)
    llm = ScriptLLM(lambda p, s: s(points=["p1", "p2"]))
    source = _SourceDoc(sid="07", title="", url="https://source.test", text="abcdefghij")

    await Organizer()._summarize_doc("", source, llm)

    assert len(source.points) == 18
    assert source.digest.startswith("【\u6765\u6e9007\uff5chttps://source.test】")


@pytest.mark.asyncio
async def test_summarize_doc_regular_error_yields_no_digest_and_auth_raises():
    source = _SourceDoc(sid="01", title="T", url="u", text="body")
    await Organizer()._summarize_doc(
        "topic", source, ScriptLLM(lambda p, s: RuntimeError("bad summary"))
    )
    assert source.points == []
    assert source.digest == ""

    with pytest.raises(LLMAuthenticationError):
        await Organizer()._summarize_doc(
            "topic", source, ScriptLLM(lambda p, s: LLMAuthenticationError("401"))
        )


@pytest.mark.asyncio
async def test_summarize_all_accepts_empty_sources():
    await Organizer()._summarize_all("topic", [], ScriptLLM())


@pytest.mark.asyncio
async def test_plan_nodes_empty_result_returns_fallback_without_mutating_provider_value():
    returned = _NodePlan()
    llm = ScriptLLM(lambda p, s: returned)
    plan = await Organizer()._plan_nodes("", "evidence", llm)
    assert plan.nodes[0].title == "\u6982\u8ff0"
    assert returned.nodes == []


@pytest.mark.asyncio
async def test_node_body_and_main_table_cover_single_node_and_no_sources(tmp_path):
    llm = ScriptLLM(chats=["node body", "main body"])
    organizer = Organizer()
    node = _Node(title="Only", core_question="what")
    body = await organizer._node_body(1, node, ["Only"], "evidence", llm)
    assert body == "node body"
    assert "\uff08\u65e0\uff09" in llm.prompts[0]

    path = tmp_path / "N1-Only.md"
    path.write_text(body, encoding="utf-8")
    main = await organizer._main_table(_NodePlan(nodes=[node]), [path], [], llm)
    assert "## \u6765\u6e90\u7d22\u5f15" not in main


@pytest.mark.asyncio
async def test_assess_feedback_early_return_sparse_success_regular_error_and_auth(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    strong = tree / "N1-strong.md"
    strong.write_text("# N1 Strong\n\u6765\u6e9001 \u6765\u6e9002 \u6765\u6e9003", encoding="utf-8")
    organizer = Organizer(OrganizerConfig(min_evidence_per_node=3))

    early = await organizer.assess_and_feedback(organize_result(tree, [strong]), ScriptLLM(), "t")
    assert early.queries == []

    second = tree / "N2-empty.md"
    second.write_text("", encoding="utf-8")
    result = organize_result(tree, [strong, second])
    llm = ScriptLLM(lambda p, s: s(queries=[" q ", ""], notes=["n"]))
    feedback = await organizer.assess_and_feedback(result, llm, "topic")
    assert feedback.sparse_nodes == ["N2-empty"]
    assert feedback.queries == ["q"]
    assert feedback.notes == ["n"]

    failed = await organizer.assess_and_feedback(
        result, ScriptLLM(lambda p, s: RuntimeError("bad feedback")), "topic"
    )
    assert failed.sparse_nodes == ["N2-empty"]
    assert failed.queries == []

    with pytest.raises(LLMAuthenticationError):
        await organizer.assess_and_feedback(
            result, ScriptLLM(lambda p, s: LLMAuthenticationError("401")), "topic"
        )


@pytest.mark.asyncio
async def test_assess_feedback_two_strong_nodes_uses_non_sparse_prompt(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    nodes = []
    for index in (1, 2):
        path = tree / f"N{index}.md"
        path.write_text(f"# N{index} Node{index}\n\u6765\u6e9001 \u6765\u6e9002 \u6765\u6e9003", encoding="utf-8")
        nodes.append(path)
    llm = ScriptLLM(lambda p, s: s())
    feedback = await Organizer().assess_and_feedback(organize_result(tree, nodes), llm, "topic")
    assert feedback.sparse_nodes == []
    assert "\u672a\u68c0\u51fa\u660e\u663e\u7a00\u758f\u8282\u70b9" in llm.prompts[0]


@pytest.mark.asyncio
async def test_module_level_organize_delegates(tmp_path):
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    (extracted / "entities.json").write_text("[]", encoding="utf-8")
    llm = ScriptLLM(
        lambda p, s: s(nodes=[{"title": "Only"}]) if s.__name__ == "_NodePlan" else s(),
        chats=["node", "main"],
    )
    result = await organize(extracted, OrganizerConfig(min_nodes=1, max_nodes=1), llm)
    assert result.main_table.exists()


def test_reporter_word_count_and_html_state_machine():
    assert _count_words("\u4e2d\u6587 two English words") == 5
    html = _md_to_html("- first\n- second\n# Heading\nparagraph\n* tail", "Title")
    assert html.count("<ul>") == 2
    assert html.count("</ul>") == 2
    assert "<h1>Heading</h1>" in html
    assert "<p>paragraph</p>" in html
    assert "<title>Title</title>" in html
    assert "<ul>\n<li>item</li>\n</ul>\n<p>after</p>" in _md_to_html(
        "- item\nafter", "Title"
    )


def test_reporter_reads_tree_and_sources_missing_valid_and_invalid(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    reporter = Reporter()
    assert reporter._read_tree(tree) == ("", "")
    assert reporter._load_sources(tree) == []

    (tree / "00-主表.md").write_text("main", encoding="utf-8")
    (tree / "N2.md").write_text("second", encoding="utf-8")
    (tree / "N1.md").write_text("first", encoding="utf-8")
    main, nodes = reporter._read_tree(tree)
    assert main == "main"
    assert nodes == "first\n\n---\n\nsecond"

    raw = tmp_path / "raw"
    raw.mkdir()
    sources = raw / "sources.json"
    sources.write_text("not json", encoding="utf-8")
    assert reporter._load_sources(tree) == []
    sources.write_text(
        json.dumps([{"title": "Paper", "url": "https://paper"}, {}]), encoding="utf-8"
    )
    assert reporter._load_sources(tree) == [
        {"sid": "01", "title": "Paper", "url": "https://paper"},
        {"sid": "02", "title": "", "url": ""},
    ]


@pytest.mark.asyncio
async def test_reporter_markdown_custom_output_references_strip_and_truncate(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "00-主表.md").write_text("main", encoding="utf-8")
    (tree / "N1.md").write_text("node", encoding="utf-8")
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "sources.json").write_text(
        json.dumps([{"title": "", "url": "https://paper"}]), encoding="utf-8"
    )
    output = tmp_path / "custom.md"
    llm = ScriptLLM(chats=["body text\n## \u53c2\u8003\u8d44\u6599\nmodel invented"])
    result = await Reporter(ReporterConfig(max_length=90)).run(tree, llm, "Topic", output)
    text = output.read_text(encoding="utf-8")
    assert result.report_path == output
    assert result.source_count == 1
    assert "model invented" not in text
    assert "\u2026\uff08\u5df2\u622a\u65ad\uff09" in text


@pytest.mark.asyncio
async def test_reporter_default_html_without_sources_and_module_wrapper(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "N1.md").write_text("node", encoding="utf-8")
    html_result = await Reporter(ReporterConfig(format="html")).run(
        tree, ScriptLLM(chats=["# Body\n- item"]), topic=""
    )
    assert html_result.report_path.name == "report.html"
    assert "<!DOCTYPE html>" in html_result.report_path.read_text(encoding="utf-8")

    md_result = await report(tree, ReporterConfig(), ScriptLLM(chats=["English body"]))
    assert md_result.report_path.name == "report.md"
    assert md_result.word_count > 0
