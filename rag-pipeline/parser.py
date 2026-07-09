"""文档解析:支持 PDF/DOCX/PPTX/XLSX/HTML/MD/TXT,统一输出 Document 列表"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import List
from loguru import logger

try:
    from docling.document_converter import DocumentConverter
    DOCLING_AVAILABLE = True
except ImportError:
    DOCLING_AVAILABLE = False
    logger.warning("docling 未安装,降级使用 pypdf + 文本读取")


@dataclass
class Document:
    """统一文档结构"""
    content: str  # 纯文本或 Markdown
    metadata: dict = field(default_factory=dict)
    # metadata 标准字段:source(原始文件路径),filename,filetype,page(页码),title(标题)


def _read_plain_text(path: Path) -> str:
    """纯文本/Markdown/HTML 简单读取"""
    suffixes = {".html", ".htm"}
    if path.suffix.lower() in suffixes:
        # 简单 HTML 去除标签(避免引入 bs4)
        import re
        text = path.read_text(encoding="utf-8", errors="ignore")
        text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text
    return path.read_text(encoding="utf-8", errors="ignore")


def _read_with_docling(path: Path) -> List[Document]:
    """Docling 解析,按页/章节返回多个 Document"""
    converter = DocumentConverter()
    result = converter.convert(str(path))
    md = result.document.export_to_markdown()
    # Docling 整体输出一个 markdown,带换页符
    pages = md.split("\f") if "\f" in md else [md]
    docs = []
    for i, page_md in enumerate(pages, 1):
        if not page_md.strip():
            continue
        docs.append(Document(
            content=page_md,
            metadata={
                "source": str(path),
                "filename": path.name,
                "filetype": path.suffix.lower(),
                "page": i,
                "title": path.stem,
            }
        ))
    return docs


def _read_with_pypdf(path: Path) -> List[Document]:
    """pypdf 降级方案,按页返回"""
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    docs = []
    for i, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        if not text.strip():
            continue
        docs.append(Document(
            content=text,
            metadata={
                "source": str(path),
                "filename": path.name,
                "filetype": ".pdf",
                "page": i,
                "title": path.stem,
            }
        ))
    return docs


def parse_file(path: Path) -> List[Document]:
    """统一入口"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    plain_exts = {".txt", ".md", ".markdown", ".html", ".htm"}
    office_exts = {".pdf", ".docx", ".pptx", ".xlsx", ".xls"}

    if path.suffix.lower() in plain_exts:
        return [Document(
            content=_read_plain_text(path),
            metadata={"source": str(path), "filename": path.name, "filetype": path.suffix.lower(), "title": path.stem}
        )]

    if path.suffix.lower() in office_exts:
        if DOCLING_AVAILABLE:
            try:
                return _read_with_docling(path)
            except Exception as e:
                logger.warning(f"docling 解析 {path} 失败,降级 pypdf: {e}")
        if path.suffix.lower() == ".pdf":
            return _read_with_pypdf(path)
        else:
            logger.warning(f"无 docling 时不支持 {path.suffix},请安装 docling")

    raise ValueError(f"不支持的文件类型: {path.suffix}")


def parse_directory(dir_path: Path) -> List[Document]:
    """递归解析目录下所有支持的文档"""
    dir_path = Path(dir_path)
    supported = {".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".txt", ".md", ".markdown", ".html", ".htm"}
    files = [p for p in dir_path.rglob("*") if p.is_file() and p.suffix.lower() in supported]
    logger.info(f"发现 {len(files)} 个文档 in {dir_path}")

    all_docs = []
    for f in files:
        try:
            docs = parse_file(f)
            all_docs.extend(docs)
            logger.info(f"  ✓ {f.name} → {len(docs)} 段")
        except Exception as e:
            logger.error(f"  ✗ {f.name} 解析失败: {e}")
    return all_docs
