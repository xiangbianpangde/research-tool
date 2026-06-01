"""PDF 摄取测试：用假的 mineru（monkeypatch subprocess）验证编排，不需真实 MinerU。"""

import pytest

from src.infrastructure.ingest.pdf import PdfIngestor
from src.infrastructure.llm import MockLLMClient
from src.domain.models import PdfIngestConfig


def _make_pdf(tmp_path, name="paper.pdf"):
    p = tmp_path / name
    p.write_bytes(b"%PDF-1.4 fake")
    return p


@pytest.fixture
def fake_mineru(monkeypatch, tmp_path):
    """伪造 mineru：把 PATH 查找通过，并让 subprocess 写出一个 MD。"""
    monkeypatch.setattr("src.infrastructure.ingest.pdf.shutil.which", lambda c: "/usr/bin/mineru")

    def fake_run(cmd, **kwargs):
        # cmd: [mineru, -p, pdf, -o, parse_root, -b, ..., -l, ...]
        pdf = __import__("pathlib").Path(cmd[2])
        parse_root = __import__("pathlib").Path(cmd[4])
        out = parse_root / pdf.stem / "auto"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{pdf.stem}.md").write_text(
            "# Title\n\nThe Transformer uses self-attention.", encoding="utf-8"
        )

        class R:
            returncode = 0
            stderr = ""

        return R()

    monkeypatch.setattr("src.infrastructure.ingest.pdf.subprocess.run", fake_run)


@pytest.mark.asyncio
async def test_ingest_no_translate(tmp_path, fake_mineru):
    _make_pdf(tmp_path)
    res = await PdfIngestor(PdfIngestConfig()).run(tmp_path, tmp_path / "out")
    assert len(res.files) == 1
    content = res.files[0].read_text(encoding="utf-8")
    assert "self-attention" in content  # 英文原文保留
    assert content.startswith("<!-- source: file://")  # 元数据头
    assert res.sources[0].source_engine == "pdf"
    assert (res.raw_dir / "sources.json").exists()


@pytest.mark.asyncio
async def test_ingest_with_translate(tmp_path, fake_mineru):
    _make_pdf(tmp_path)
    llm = MockLLMClient(chat_response="标题\n\nTransformer 使用自注意力。")
    cfg = PdfIngestConfig(translate=True)
    res = await PdfIngestor(cfg, llm).run(tmp_path, tmp_path / "out")
    content = res.files[0].read_text(encoding="utf-8")
    assert "自注意力" in content  # 已翻译
    assert "+ translate" in content  # 元数据标注翻译


@pytest.mark.asyncio
async def test_translate_requires_llm(tmp_path):
    with pytest.raises(Exception):
        PdfIngestor(PdfIngestConfig(translate=True), llm=None)


@pytest.mark.asyncio
async def test_missing_mineru_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr("src.infrastructure.ingest.pdf.shutil.which", lambda c: None)
    _make_pdf(tmp_path)
    with pytest.raises(Exception, match="mineru"):
        await PdfIngestor(PdfIngestConfig()).run(tmp_path, tmp_path / "out")
