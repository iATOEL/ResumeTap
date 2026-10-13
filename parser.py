"""简历解析模块：从 .docx 文件中提取文本内容。"""

import io
from typing import Iterator, List, Union

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph


def _iter_block_items(parent) -> Iterator[Union[Paragraph, Table]]:
    """按文档顺序迭代段落和表格。"""
    for child in parent.element.body:
        if child.tag == qn("w:p"):
            yield Paragraph(child, parent)
        elif child.tag == qn("w:tbl"):
            yield Table(child, parent)


def _extract_table_text(table: Table) -> str:
    """从表格中提取文本内容。"""
    rows = []
    for row in table.rows:
        cells = [cell.text.strip() for cell in row.cells]
        rows.append(" | ".join(cells))
    return "\n".join(rows)


def _extract_text(doc: Document) -> str:
    """从 Document 对象中提取全部文本（段落、表格）。"""
    blocks: List[str] = []
    for block in _iter_block_items(doc):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if text:
                blocks.append(text)
        elif isinstance(block, Table):
            table_text = _extract_table_text(block)
            if table_text.strip():
                blocks.append(table_text)
    return "\n".join(blocks)


def parse_docx(file_path: str) -> str:
    """解析 .docx 文件路径，提取全部文本内容。"""
    return _extract_text(Document(file_path))


def parse_docx_bytes(file_bytes: bytes) -> str:
    """从字节流解析 .docx 文件。"""
    return _extract_text(Document(io.BytesIO(file_bytes)))
