"""简历文件解析：支持 PDF / DOCX / TXT，统一输出纯文本。"""

import io
import re
import zipfile

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}
LEGACY_SUFFIXES = {".doc", ".wps", ".rtf"}

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TEXT_CHARS = 120_000


class ParseError(Exception):
    """可展示给用户的解析错误。"""


def _iter_block_items(parent):
    for child in parent.element.body:
        if child.tag == qn("w:p"):
            yield Paragraph(child, parent)
        elif child.tag == qn("w:tbl"):
            yield Table(child, parent)


def _table_text(table: Table) -> str:
    rows = []
    for row in table.rows:
        cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _docx_text(data: bytes) -> str:
    try:
        doc = Document(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError, KeyError) as exc:
        raise ParseError("DOCX 文件已损坏或不是有效的 Word 文档") from exc

    blocks: list[str] = []
    for block in _iter_block_items(doc):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if text:
                blocks.append(text)
        elif isinstance(block, Table):
            text = _table_text(block)
            if text:
                blocks.append(text)

    if not blocks:
        # 文本框/内容控件中的文字不在段落遍历范围内，兜底从 XML 取
        try:
            root = doc.element.body
            texts = [node.text for node in root.iter(qn("w:t")) if node.text]
            blocks = [t for t in texts if t.strip()]
        except Exception:  # noqa: BLE001 - 兜底失败按空文本处理
            blocks = []
    return "\n".join(blocks)


def _pdf_text(data: bytes) -> str:
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - 打包环境必然存在
        raise ParseError("缺少 PDF 解析组件，请重新安装应用") from exc

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - pymupdf 抛出的异常类型较多
        raise ParseError("PDF 文件已损坏或无法读取") from exc

    try:
        if doc.needs_pass:
            raise ParseError("PDF 已加密，请先解除密码后再导入")
        pages = []
        for page in doc:
            pages.append(page.get_text("text"))
        text = "\n".join(pages)
    finally:
        doc.close()

    if not text.strip():
        raise ParseError("未从 PDF 中提取到文字，可能是扫描件（仅图片）")
    return text


def _txt_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "gb18030", "big5"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:MAX_TEXT_CHARS]


def parse_bytes(data: bytes, suffix: str) -> str:
    suffix = suffix.lower()
    if not data:
        raise ParseError("文件内容为空")
    if len(data) > MAX_FILE_BYTES:
        raise ParseError("文件超过 25MB 限制")
    if suffix in LEGACY_SUFFIXES:
        raise ParseError("暂不支持旧版 .doc/.wps 格式，请另存为 .docx 或 PDF 后重试")
    if suffix not in SUPPORTED_SUFFIXES:
        raise ParseError(f"不支持的文件格式 {suffix or '（无扩展名）'}，请使用 PDF / DOCX / TXT")

    if suffix == ".pdf":
        text = _pdf_text(data)
    elif suffix == ".docx":
        text = _docx_text(data)
    else:
        text = _txt_text(data)

    text = clean_text(text)
    if not text:
        raise ParseError("未提取到任何文字内容，请检查文件是否为空或受保护")
    return text
