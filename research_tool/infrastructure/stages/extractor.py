"""Stage 3: Extractor —— LLM 抽取实体/关系/三元组（可选阶段）。

依据 01 §4、04 extractor 段、07 §4（OneKE 三任务 + AutoSchemaKG 自动 Schema）。
- 按 chunk_size/overlap 分块送 LLM
- tasks 决定抽取 ner/re/triple
- entity_types/relation_types 为 null 时自动归纳 Schema → schema.json
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

from ..llm.base import LLMClient, gather_fail_fast
from ..llm.openai_client import OpenAILLMClient
from ...domain.errors import LLMAuthenticationError, StageError
from ...domain.models import Entity, ExtractorConfig, ExtractResult, Relation, Triple
from .base import ensure_dir, write_json
from ...nine_loop.evidence import parse_source

_CACHE_VERSION = 1
_CACHE_CONTRACT = "extract-evidence-v1"
_COMPLETED_FILES = ("entities.json", "claims.json", "relations.json", "triples.json", "schema.json")

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


def _chunk_spans(text: str, size: int, overlap: int) -> list[tuple[int, str]]:
    chunks = []
    step = max(1, size - overlap)
    for start in range(0, len(text), step):
        piece = text[start:start + size]
        if piece.strip():
            chunks.append((start, piece))
        if start + size >= len(text):
            break
    return chunks


def _chunk(text: str, size: int, overlap: int) -> list[str]:
    return [piece for _start, piece in _chunk_spans(text, size, overlap)]


def _strip_meta(text: str) -> str:
    """Backward-compatible helper backed by the canonical source parser."""
    return parse_source(text)["text"]


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


def _verified_claims(
    source: dict[str, str],
    chunk: str,
    claims: list[dict[str, str]],
    *,
    chunk_offset: int = 0,
) -> list[dict]:
    """Bind quotes to both the prompt chunk and canonical source body."""
    verified = []
    for proposed in claims:
        quote = proposed.get("quote", "").strip()
        chunk_start = chunk.find(quote) if quote else -1
        start = chunk_offset + chunk_start
        if (
            chunk_start < 0
            or source["text"][chunk_offset:chunk_offset + len(chunk)] != chunk
            or source["text"][start:start + len(quote)] != quote
            or not source["locator"]
        ):
            raise StageError("extract", "模型引用不在当前正文块或规范化正文中，停止抽取")
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


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _cache_identity(config: ExtractorConfig, llm: LLMClient) -> dict[str, Any] | None:
    llm_config = getattr(llm, "config", None)
    if llm_config is None or not hasattr(llm_config, "model_dump"):
        return None  # Unknown client identity: do not reuse another client's results.
    return {
        "contract": _CACHE_CONTRACT,
        "extractor": config.model_dump(mode="json"),
        "llm": llm_config.model_dump(mode="json", exclude={"api_key", "session_id"}),
        "client": f"{type(llm).__module__}.{type(llm).__qualname__}",
        "system": _SYSTEM,
    }


def _load_chunk_cache(path: Path) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != _CACHE_VERSION:
            return {}
        entries = payload.get("entries")
        if not isinstance(entries, list):
            return {}
        return {
            item["key"]: item
            for item in entries
            if isinstance(item, dict) and isinstance(item.get("key"), str)
            and isinstance(item.get("result"), dict)
            and item.get("result_sha256") == _digest(item["result"])
        }
    except (OSError, ValueError, TypeError):
        return {}  # Incomplete/corrupt caches are not completion artifacts.


def _save_chunk_cache(path: Path, entries: dict[str, dict[str, Any]]) -> None:
    temporary = path.with_suffix(".tmp")
    write_json(temporary, {"version": _CACHE_VERSION,
                           "entries": [entries[key] for key in sorted(entries)]})
    temporary.replace(path)


def _is_structured_failure(exc: BaseException) -> bool:
    """Recognize parser/schema errors without trusting provider message text."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (ValidationError, json.JSONDecodeError)):
            return True
        current = current.__cause__ or current.__context__
    return False


def _isolate_previous_outputs(out_dir: Path) -> None:
    """Retain earlier stage JSON without exposing it as this attempt's completion."""
    previous = [out_dir / name for name in _COMPLETED_FILES if (out_dir / name).is_file()]
    if previous:
        history = ensure_dir(out_dir / ".previous" / uuid4().hex)
        for path in previous:
            path.replace(history / path.name)


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
        _isolate_previous_outputs(out_dir)

        sem = asyncio.Semaphore(self.config.concurrency)
        jobs: list[tuple[str, dict[str, str], list[str], int, str, str]] = []
        for src in sorted(input_dir.glob("*.md")):
            source = parse_source(src.read_text(encoding="utf-8"))
            full = source["text"]
            file_lines = full.splitlines()
            for index, (offset, ch) in enumerate(
                _chunk_spans(full, self.config.chunk_size, self.config.overlap)
            ):
                jobs.append((src.name, source, file_lines, offset, ch, str(index)))

        cache_path = out_dir / ".chunk-cache.json"
        identity = _cache_identity(self.config, llm)
        cache = _load_chunk_cache(cache_path) if identity is not None else {}
        transient_retry = (
            isinstance(llm, OpenAILLMClient)
            or getattr(getattr(llm, "config", None), "provider", None)
            == "openai_compatible"
        )
        invalid_evidence = False

        async def _do(job):
            nonlocal invalid_evidence
            source_file, source, file_lines, offset, ch, index = job
            key = _digest({"identity": identity, "source": source,
                           "file": source_file, "chunk_index": index,
                           "chunk_offset": offset, "chunk": ch})
            async with sem:
                if invalid_evidence:
                    raise StageError("extract", "先前正文块引用校验失败，拒绝排队抽取")
                cached = cache.get(key)
                res = None
                if cached is not None:
                    try:
                        res = _ChunkResult.model_validate(cached["result"])
                        if transient_retry and not (
                            res.claims or res.entities or res.relations or res.triples
                        ):
                            raise ValueError("empty extraction cache")
                        _verified_claims(source, ch, res.claims, chunk_offset=offset)
                    except (ValueError, TypeError, StageError):
                        cache.pop(key, None)
                        res = None
                if res is None:
                    prompt = _build_prompt(self.config, ch)
                    structured_attempts = 2 if transient_retry else 1
                    for structured_attempt in range(structured_attempts):
                        try:
                            res = await llm.chat_structured(
                                prompt, _ChunkResult, system=_SYSTEM
                            )
                            if (
                                structured_attempts > 1
                                and not res.claims
                                and not res.entities
                                and not res.relations
                                and not res.triples
                                and structured_attempt == 0
                            ):
                                prompt += (
                                    "\n\n上一次返回了空结果。正文包含可抽取证据时，"
                                    "至少返回一个 claims 条目；quote 必须逐字复制正文中的完整"
                                    "证据句，不能编造或使用 URL。"
                                )
                                continue
                            break
                        except LLMAuthenticationError:
                            invalid_evidence = True
                            raise
                        except Exception as exc:  # noqa: BLE001
                            if (
                                structured_attempts > 1
                                and _is_structured_failure(exc)
                                and structured_attempt == 0
                            ):
                                prompt += (
                                    "\n\n上一次结构化输出无法解析。请重新回答：只输出一个完整、"
                                    "单一、合法的 JSON 对象，不要 markdown、解释、截断或多个对象。"
                                )
                                continue
                            if _is_structured_failure(exc):
                                invalid_evidence = True
                                # Preserve the LLMError cause so the pipeline can
                                # apply its bounded stage retry budget to transient
                                # malformed structured responses.
                                raise StageError(
                                    "extract", "模型结构化证据无效，停止抽取"
                                ) from exc
                            return source_file, file_lines, _ChunkResult(), [], exc
                try:
                    res = _ChunkResult.model_validate(res)
                    verified = _verified_claims(source, ch, res.claims, chunk_offset=offset)
                except (StageError, ValidationError, TypeError, AttributeError):
                    invalid_evidence = True
                    raise StageError("extract", "模型结构化证据或正文引用无效，停止抽取") from None
                if identity is not None:
                    dumped = res.model_dump(mode="json")
                    cache[key] = {"key": key, "result": dumped, "result_sha256": _digest(dumped)}
                    _save_chunk_cache(cache_path, cache)
                return source_file, file_lines, res, verified, None

        results = await gather_fail_fast(_do(job) for job in jobs)
        failures = [item[-1] for item in results if item[-1] is not None]
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

        for source_file, file_lines, res, verified, _failed in results:
            claims.extend(verified)
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
