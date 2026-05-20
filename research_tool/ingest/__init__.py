"""数据摄取适配器：把外部资料（PDF 等）转成 raw/ 文件，替代/补充 Collector。"""

from .pdf import PdfIngestor, ingest_pdfs

__all__ = ["PdfIngestor", "ingest_pdfs"]
