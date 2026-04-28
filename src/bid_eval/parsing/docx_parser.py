import re
from pathlib import Path
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph


# ── 公共工具函数 ─────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    """去除多余空白，保留换行结构"""
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def _is_heading(paragraph: Paragraph) -> bool:
    """判断段落是否为标题样式"""
    style_name = paragraph.style.name.lower()
    return "heading" in style_name or "标题" in style_name


def _iter_block_items(document: Document):
    """
    按文档顺序逐个 yield 段落和表格，保留原始位置关系。
    python-docx 默认的 .paragraphs 和 .tables 是分开的，
    这个函数统一遍历 document.element.body 的直接子元素。
    """
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    body = document.element.body
    for child in body.iterchildren():
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag == "p":
            yield Paragraph(child, document)
        elif tag == "tbl":
            yield Table(child, document)


def _parse_table(table: Table) -> str:
    """
    将 Word 表格转换为纯文本，处理水平合并和垂直合并单元格。

    水平合并（gridSpan）：同行多列指向同一 cell 对象，用 id() 去重。
    垂直合并（vMerge） ：跨行单元格每行是新对象，用 vMerge XML 属性去重。
    """
    W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    lines = []

    for row in table.rows:
        seen_ids: set[int] = set()
        row_cells = []

        for cell in row.cells:
            # 水平合并去重：同一行内同一对象只处理一次
            cell_id = id(cell)
            if cell_id in seen_ids:
                continue
            seen_ids.add(cell_id)

            # 垂直合并去重：跳过延续行（非 restart 的 vMerge）
            tc = cell._tc
            vmerge = tc.find(f"{{{W_NS}}}tcPr/{{{W_NS}}}vMerge")
            if vmerge is not None:
                val = vmerge.get(f"{{{W_NS}}}val", "")
                if val != "restart":
                    # 延续行，内容已在 restart 行输出过，跳过
                    continue

            cell_text = _clean(cell.text)
            if cell_text:
                row_cells.append(cell_text)

        if row_cells:
            lines.append(" | ".join(row_cells))

    return "\n".join(lines)


def _parse_paragraph(paragraph: Paragraph) -> str:
    """
    提取段落文本，标题加前缀标记方便后续按章节切分。
    """
    text = _clean(paragraph.text)
    if not text:
        return ""
    if _is_heading(paragraph):
        return f"\n## {text}\n"
    return text


# ── 主解析函数 ───────────────────────────────────────────────────────────────

def parse_docx(file_path: str | Path) -> str:
    """
    解析 Word 文档，按文档顺序提取段落和表格内容，
    返回适合 LLM 处理的纯文本字符串。

    Args:
        file_path: Word 文件路径（.docx）

    Returns:
        结构化纯文本，标题用 ## 标记，表格用 | 分隔

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: 文件格式不支持
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"文件不存在：{path}")
    if path.suffix.lower() != ".docx":
        raise ValueError(f"不支持的文件格式：{path.suffix}，当前仅支持 .docx")

    document = Document(str(path))
    blocks: list[str] = []

    for block in _iter_block_items(document):
        if isinstance(block, Paragraph):
            text = _parse_paragraph(block)
            if text:
                blocks.append(text)
        elif isinstance(block, Table):
            table_text = _parse_table(block)
            if table_text:
                blocks.append(f"\n[表格开始]\n{table_text}\n[表格结束]\n")

    return "\n".join(blocks)


def parse_docx_files(file_paths: list[str | Path]) -> str:
    """
    解析多个 Word 文件并合并输出，文件之间用分隔符隔开。
    用于招标文件或投标文件由多个 Word 文件组成的情况。

    Args:
        file_paths: Word 文件路径列表

    Returns:
        所有文件内容合并后的纯文本
    """
    parts: list[str] = []

    for path in file_paths:
        file_name = Path(path).name
        try:
            content = parse_docx(path)
            parts.append(f"{'='*60}\n文件：{file_name}\n{'='*60}\n{content}")
        except (FileNotFoundError, ValueError) as e:
            parts.append(f"{'='*60}\n文件：{file_name}\n{'='*60}\n[解析失败：{e}]")

    return "\n\n".join(parts)