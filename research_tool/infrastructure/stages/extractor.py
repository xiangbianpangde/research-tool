"""Stage 3: Extractor —— LLM 抽取实体/关系/三元组（可选阶段）。

依据 01 §4、04 extractor 段、07 §4（OneKE 三任务 + AutoSchemaKG 自动 Schema）。
- 按 chunk_size/overlap 分块送 LLM
- tasks 决定抽取 ner/re/triple
- entity_types/relation_types 为 null 时自动归纳 Schema → schema.json
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from pydantic import BaseModel, Field

from ..llm.base import LLMClient, gather_fail_fast
from ...domain.errors import LLMAuthenticationError, LLMError, StageError
from ...domain.models import Entity, ExtractorConfig, ExtractResult, Relation, Triple
from .base import ensure_dir, write_json
from ...nine_loop.evidence import parse_source

_SYSTEM = (
    "你是信息抽取专家。从给定文本中精确抽取知识，只依据文本本身，不臆造。" "所有输出为合法 JSON。"
)


class _LEntity(BaseModel):
    name: str
    type: str = "Concept"
    confidence: float = 1.0


class _LRelation(BaseModel):
    subject: str
    predicate: str
    object: str
    confidence: float = 1.0


class _LTriple(BaseModel):
    head: str
    relation: str
    tail: str


class _ChunkResult(BaseModel):
    entities: list[_LEntity] = Field(default_factory=list)
    relations: list[_LRelation] = Field(default_factory=list)
    triples: list[_LTriple] = Field(default_factory=list)
    claims: list[dict[str, str]] = Field(default_factory=list)


def _chunk(text: str, size: int, overlap: int) -> list[str]:
    if len(text) <= size:
        return [text] if text.strip() else []
    chunks = []
    step = max(1, size - overlap)
    for start in range(0, len(text), step):
        piece = text[start : start + size]
        if piece.strip():
            chunks.append(piece)
        if start + size >= len(text):
            break
    return chunks


def _strip_meta(text: str) -> str:
    lines = text.splitlines()
    i = 0
    while i < len(lines) and (lines[i].strip().startswith("<!--") or not lines[i].strip()):
        i += 1
    return "\n".join(lines[i:])


def _build_prompt(config: ExtractorConfig, chunk: str) -> str:
    parts = ["请从下面文本中抽取信息。\n"]
    if "ner" in config.tasks:
        if config.entity_types:
            parts.append(f"实体类型限定为：{', '.join(config.entity_types)}。")
        else:
            parts.append("实体类型由你自动归纳（如 Model/Method/Person/Org/Concept 等）。")
        parts.append("抽取 entities: [{name, type, confidence}]。")
    if "re" in config.tasks:
        if config.relation_types:
            parts.append(f"关系类型限定为：{', '.join(config.relation_types)}。")
        parts.append("抽取 relations: [{subject, predicate, object, confidence}]。")
    if "triple" in config.tasks:
        parts.append("抽取 triples: [{head, relation, tail}]（用于知识图谱）。")
    parts.append("\n未涉及的字段返回空数组。\n\n--- 文本 ---\n")
    parts.insert(1, (
        "同时提取有实质内容的事实 claims:[{text,quote,topic}]；quote 必须逐字复制下面正文的"
        "完整证据句；topic 为简短子主题。禁止用URL或标题代替正文证据。\n"
    ))
    parts.append(chunk)
    return "".join(parts)


def _line_of(text_lines: list[str], needle: str) -> int:
    nl = needle.lower()
    for i, ln in enumerate(text_lines, 1):
        if nl in ln.lower():
            return i
    return 0


def _verified_claims(path: Path, claims: list[dict[str, str]]) -> list[dict]:
    """Bind extracted quotations to real body offsets before storing facts."""
    source = parse_source(path.read_text(encoding="utf-8"))
    verified = []
    for proposed in claims:
        quote = proposed.get("quote", "").strip()
        start = source["text"].find(quote) if quote else -1
        if start < 0 or not source["locator"]:
            raise StageError("extract", "模型引用不在来源正文中，停止抽取")
        claim_id = hashlib.sha256((source["source_id"] + quote).encode()).hexdigest()
        verified.append({
            "claim_id": claim_id,
            "text": quote,
            "topic": proposed.get("topic", "").strip() or source["title"] or "证据",
            "citations": [{
                "source_id": source["source_id"], "locator": source["locator"],
                "content_sha256": source["content_sha256"], "text": quote,
                "start_offset": start, "end_offset": start + len(quote),
            }],
        })
    return verified


class Extractor:
    def __init__(self, config: ExtractorConfig | None = None) -> None:
        self.config = config or ExtractorConfig()

    async def run(  # noqa: PLR0915 - 长函数：抽取管线，历史债
        self, input_dir: Path, llm: LLMClient, work_dir: Path | None = None
    ) -> ExtractResult:
        input_dir = Path(input_dir)
        if work_dir is not None:
            out_dir = Path(work_dir) / "extracted"
        else:
            out_dir = input_dir.parent / "extracted"
        ensure_dir(out_dir)

        sem = asyncio.Semaphore(self.config.concurrency)
        jobs: list[tuple[str, list[str], str]] = []  # (source_file, file_lines, chunk)
        for src in sorted(input_dir.glob("*.md")):
            full = _strip_meta(src.read_text(encoding="utf-8"))
            file_lines = full.splitlines()
            for ch in _chunk(full, self.config.chunk_size, self.config.overlap):
                jobs.append((src.name, file_lines, ch))

        async def _do(job):
            source_file, file_lines, ch = job
            async with sem:
                try:
                    res = await llm.chat_structured(
                        _build_prompt(self.config, ch), _ChunkResult, system=_SYSTEM
                    )
                except LLMAuthenticationError:
                    raise
                except LLMError as exc:
                    return source_file, file_lines, _ChunkResult(), exc
                except Exception as exc:  # noqa: BLE001 - aggregate chunk failures
                    return source_file, file_lines, _ChunkResult(), exc
                return source_file, file_lines, res, None

        results = await gather_fail_fast(_do(job) for job in jobs)
        failures = [error for *_rest, error in results if error is not None]
        failed_chunks = len(failures)
        if self.config.fail_on_chunk_error and failed_chunks:
            error = StageError(
                "extract",
                f"LLM 块抽取失败 {failed_chunks}/{len(results)}；未写入完成产物，可安全续跑",
            )
            raise error from failures[0]

        entities: list[Entity] = []
        claims: list[dict] = []
        relations: list[Relation] = []
        triples: list[Triple] = []
        e_seen, r_seen, t_seen = set(), set(), set()

        for source_file, file_lines, res, _failed in results:
            claims.extend(_verified_claims(input_dir / source_file, res.claims))
            for e in res.entities:
                key = (e.name.lower(), e.type.lower())
                if key in e_seen:
                    continue
                e_seen.add(key)
                entities.append(
                    Entity(
                        name=e.name,
                        type=e.type,
                        source_file=source_file,
                        source_line=_line_of(file_lines, e.name),
                        confidence=e.confidence,
                    )
                )
            for r in res.relations:
                key = (r.subject.lower(), r.predicate.lower(), r.object.lower())
                if key in r_seen:
                    continue
                r_seen.add(key)
                relations.append(
                    Relation(
                        subject=r.subject,
                        predicate=r.predicate,
                        object=r.object,
                        source_file=source_file,
                        confidence=r.confidence,
                    )
                )
            for t in res.triples:
                key = (t.head.lower(), t.relation.lower(), t.tail.lower())
                if key in t_seen:
                    continue
                t_seen.add(key)
                triples.append(
                    Triple(
                        head=t.head,
                        relation=t.relation,
                        tail=t.tail,
                        source_file=source_file,
                    )
                )

        schema = None
        if self.config.entity_types is None or self.config.relation_types is None:
            schema = {
                "entity_types": sorted({e.type for e in entities}),
                "relation_types": sorted({r.predicate for r in relations})
                or sorted({t.relation for t in triples}),
            }

        write_json(out_dir / "entities.json", [e.model_dump() for e in entities])
        claims = list({claim["claim_id"]: claim for claim in claims}.values())
        write_json(out_dir / "claims.json", claims)
        write_json(out_dir / "relations.json", [r.model_dump() for r in relations])
        write_json(out_dir / "triples.json", [t.model_dump() for t in triples])
        if schema is not None:
            write_json(out_dir / "schema.json", schema)

        return ExtractResult(
            entities=entities,
            relations=relations,
            triples=triples,
            claims=claims,
            schema=schema,
            output_dir=out_dir,
        )


async def extract(input_dir: Path, config: ExtractorConfig, llm: LLMClient) -> ExtractResult:
    """模块级函数（01 §4.2 签名）。"""
    return await Extractor(config).run(input_dir, llm)
