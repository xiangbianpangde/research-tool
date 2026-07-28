"""将 research-output 的 Markdown 产物受控发布到 Obsidian staging vault。

这个模块只做文件格式转换；不调用 LLM，也绝不写人工策展目录。
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path


class Form(str, Enum):
    COMPLETE = "complete"
    NO_EXTRACTED = "no-extracted"
    DEGRADED = "degraded"


@dataclass(frozen=True)
class PublishReport:
    slug: str
    form: Form
    written: tuple[Path, ...]
    warnings: tuple[str, ...]
    dry_run: bool


_NODE_LINK = re.compile(r"\[([^\]]+)\]\((N\d+-[^)]+)\.md\)")
_SOURCE_NUMBER = re.compile(r"来源\s*(\d+)")


def _safe_slug(slug: str) -> str:
    if not slug or Path(slug).name != slug or slug in {".", ".."}:
        raise ValueError("slug 必须是单级目录名，不能包含路径分隔符")
    return slug


def probe_form(source_dir: Path) -> Form:
    """探测 research-tool 的完整、无抽取和退化三种产物形态。"""
    tree = source_dir / "tree"
    if tree.is_dir():
        if not (tree / "00-主表.md").is_file():
            raise ValueError(f"知识树缺少 00-主表.md：{tree}")
        if not (source_dir / "report.md").is_file():
            raise ValueError(f"知识树产物缺少 report.md：{source_dir}")
        return Form.COMPLETE if (source_dir / "extracted").is_dir() else Form.NO_EXTRACTED
    if (source_dir / "report.md").is_file():
        return Form.DEGRADED
    raise ValueError(f"无法识别 research 产物：{source_dir}")


def _frontmatter(slug: str, form: Form, status: str) -> str:
    confidence = "low" if form is Form.DEGRADED else "medium"
    today = date.today().isoformat()
    return (
        "---\n"
        "type: source\n"
        f"status: {status}\n"
        f"created: {today}\n"
        f"updated: {today}\n"
        f"source: research-tool/{slug}\n"
        f"confidence: {confidence}\n"
        "owner: yhn\n"
        "tags: [research]\n"
        "links: []\n"
        "---\n\n"
    )


def _with_frontmatter(text: str, slug: str, form: Form, status: str) -> str:
    if text.startswith("---\n"):
        return text
    return _frontmatter(slug, form, status) + text.lstrip()


def _managed_update(path: Path, slug: str, content: str) -> None:
    start = f"<!-- publish-wiki:{slug}:start -->"
    end = f"<!-- publish-wiki:{slug}:end -->"
    block = f"{start}\n{content.rstrip()}\n{end}"
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    updated = (
        pattern.sub(block, original)
        if pattern.search(original)
        else original.rstrip() + "\n\n" + block + "\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated, encoding="utf-8")


def _source_warnings(node_files: list[Path], raw_dir: Path) -> list[str]:
    available = {
        int(match.group(1))
        for file in raw_dir.glob("*.md")
        if (match := re.match(r"(\d+)-", file.name))
    }
    referenced = {
        int(item)
        for node in node_files
        for item in _SOURCE_NUMBER.findall(node.read_text(encoding="utf-8"))
    }
    return [
        f"来源{number:02d} 未在 raw 中找到对应文件" for number in sorted(referenced - available)
    ]


def publish(
    slug: str,
    vault: Path,
    *,
    research_output: Path = Path("./research-output"),
    confirm: bool = False,
    dry_run: bool = False,
) -> PublishReport:
    """Legacy preview helper; active Obsidian Vault writes are forbidden.

    P1 imports must use ``wiki-stage`` and the governed atomic draft broker.
    """
    slug = _safe_slug(slug)
    source_dir = (research_output / slug).resolve()
    if not source_dir.is_dir():
        raise ValueError(f"未找到 research 产物：{source_dir}")
    form = probe_form(source_dir)
    vault = vault.resolve()
    if not dry_run and (vault / ".obsidian").is_dir():
        raise ValueError(
            "publish-wiki cannot write an active Vault; "
            "use wiki-stage and the governed draft importer"
        )
    target = vault / "05-wiki/research" / slug
    raw_target = vault / "raw" / slug
    status = "verified" if confirm else "draft"
    tree = source_dir / "tree"
    main = tree / "00-主表.md" if tree.is_dir() else source_dir / "00-主表.md"
    if not main.is_file():
        raise ValueError(f"退化产物也必须提供 00-主表.md：{source_dir}")
    node_files = sorted(tree.glob("N*.md")) if tree.is_dir() else []
    raw_dir = source_dir / "raw"
    warnings = _source_warnings(node_files, raw_dir) if raw_dir.is_dir() else []
    planned = [target / "index.md", target / "report.md", *[target / p.name for p in node_files]]
    if form is Form.COMPLETE:
        planned.append(target / "entities.md")
    if raw_dir.is_dir():
        planned.extend(raw_target / p.name for p in raw_dir.rglob("*") if p.is_file())
    planned.extend(
        [vault / "05-wiki/index.md", vault / "05-wiki/log.md", vault / "80-logs/ingest-log.md"]
    )
    if dry_run:
        return PublishReport(slug, form, tuple(planned), tuple(warnings), True)

    # publisher 的唯一可覆盖领地；其他 vault 路径不删除也不改。
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    if raw_dir.is_dir():
        if raw_target.exists():
            shutil.rmtree(raw_target)
        shutil.copytree(raw_dir, raw_target)

    index_text = _NODE_LINK.sub(r"[[\2|\1]]", main.read_text(encoding="utf-8"))
    (target / "index.md").write_text(
        _with_frontmatter(index_text, slug, form, status), encoding="utf-8"
    )
    (target / "report.md").write_text(
        _with_frontmatter(
            (source_dir / "report.md").read_text(encoding="utf-8"), slug, form, status
        ),
        encoding="utf-8",
    )
    for node in node_files:
        (target / node.name).write_text(
            _with_frontmatter(node.read_text(encoding="utf-8"), slug, form, status),
            encoding="utf-8",
        )
    if form is Form.COMPLETE:
        extracted = sorted(p.name for p in (source_dir / "extracted").glob("*.json"))
        (target / "entities.md").write_text(
            _with_frontmatter(
                "# 结构化抽取索引\n\n" + "\n".join(f"- `{name}`" for name in extracted),
                slug,
                form,
                status,
            ),
            encoding="utf-8",
        )

    source_count = len(list(raw_dir.glob("*.md"))) if raw_dir.is_dir() else 0
    _managed_update(
        vault / "05-wiki/index.md",
        slug,
        f"- [[research/{slug}/index|{slug}]] — {date.today().isoformat()}, "
        f"{len(node_files)} 节点, status: {status}",
    )
    _managed_update(
        vault / "05-wiki/log.md",
        slug,
        f"- {date.today().isoformat()} ingest: {slug} | 来源 {source_count} | "
        f"节点 {len(node_files)} | 形态: {form.value} | status: {status}",
    )
    warning_text = "；".join(warnings) if warnings else "通过"
    _managed_update(
        vault / "80-logs/ingest-log.md",
        slug,
        f"- slug: {slug}\n- form: {form.value}\n"
        f"- source validation: {warning_text}\n- status: {status}",
    )
    return PublishReport(slug, form, tuple(planned), tuple(warnings), False)
