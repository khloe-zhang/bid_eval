import io
from pathlib import Path
from typing import Optional

import pdfplumber


# ── 工具函数 ─────────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def _parse_table_pdfplumber(table: list[list]) -> str:
    """
    将 pdfplumber 提取的表格（list of list）转换为纯文本。
    过滤全空行，合并单元格内容用 | 分隔。
    """
    lines = []
    for row in table:
        cells = []
        for cell in row:
            cell_text = _clean(str(cell)) if cell is not None else ""
            cells.append(cell_text)
        # 过滤全空行
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def _extract_images_from_page(page) -> list[bytes]:
    """
    从 pdfplumber page 对象提取图片原始字节。
    返回图片字节列表，供 image_parser 处理。
    """
    images = []
    for img in page.images:
        try:
            # pdfplumber 图片对象包含 stream 数据
            raw = img.get("stream", None)
            if raw:
                images.append(raw.get_data())
        except Exception:
            pass
    return images


# ── MinerU 解析（可选，需安装 magic-pdf）────────────────────────────────────

def _parse_with_mineru(file_path: Path) -> Optional[str]:
    """
    使用 MinerU 解析 PDF，适合复杂表格和扫描件。
    未安装时返回 None，由调用方降级到 pdfplumber。
    """
    try:
        from magic_pdf.data.data_reader_writer import FileBasedDataWriter
        from magic_pdf.data.dataset import PymuDocDataset
        from magic_pdf.model.doc_analyze_by_custom_model import doc_analyze
        from magic_pdf.config.enums import SupportedPdfParseMethod
    except ImportError:
        return None

    try:
        pdf_bytes = file_path.read_bytes()
        ds = PymuDocDataset(pdf_bytes)

        # 自动判断是否为扫描件
        if ds.classify() == SupportedPdfParseMethod.OCR:
            infer_result = ds.apply(doc_analyze, ocr=True)
        else:
            infer_result = ds.apply(doc_analyze, ocr=False)

        # 提取 Markdown 格式内容（含表格）
        md_writer = FileBasedDataWriter("")
        pipe_result = infer_result.pipe_txt_mode(md_writer)
        return pipe_result.get_markdown(file_path.stem)

    except Exception as e:
        return None


# ── 主解析函数 ───────────────────────────────────────────────────────────────

def parse_pdf(
    file_path: str | Path,
    use_mineru: bool = False,
    parse_images: bool = False,
    image_parser=None,
) -> str:
    """
    解析 PDF 文件，返回适合 LLM 处理的纯文本。

    Args:
        file_path:    PDF 文件路径
        use_mineru:   是否使用 MinerU 解析（复杂表格场景）
        parse_images: 是否解析页面中的图片
        image_parser: image_parser 模块，parse_images=True 时必须传入

    Returns:
        结构化纯文本，表格用 | 分隔，图片用 [图片描述] 占位

    Raises:
        FileNotFoundError: 文件不存在
        ValueError:        文件格式不支持
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"文件不存在：{path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"不支持的文件格式：{path.suffix}，当前仅支持 .pdf")

    # 优先尝试 MinerU（如果启用）
    if use_mineru:
        mineru_result = _parse_with_mineru(path)
        if mineru_result:
            return mineru_result
        # MinerU 失败则降级到 pdfplumber

    # pdfplumber 解析
    blocks: list[str] = []

    with pdfplumber.open(str(path)) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            page_blocks: list[str] = []

            # 提取正文文字
            text = page.extract_text()
            if text:
                cleaned = _clean(text)
                if cleaned:
                    page_blocks.append(cleaned)

            # 提取表格
            tables = page.extract_tables()
            for table in tables:
                if table:
                    table_text = _parse_table_pdfplumber(table)
                    if table_text:
                        page_blocks.append(
                            f"\n[表格开始]\n{table_text}\n[表格结束]\n"
                        )

            # 提取图片（可选）
            if parse_images and image_parser:
                image_bytes_list = _extract_images_from_page(page)
                for img_bytes in image_bytes_list:
                    description = image_parser.describe_image(img_bytes)
                    if description:
                        page_blocks.append(
                            f"\n[图片描述开始]\n{description}\n[图片描述结束]\n"
                        )

            if page_blocks:
                blocks.append(
                    f"\n--- 第 {page_num} 页 ---\n" + "\n".join(page_blocks)
                )

    return "\n".join(blocks)


def parse_pdf_files(
    file_paths: list[str | Path],
    use_mineru: bool = False,
    parse_images: bool = False,
    image_parser=None,
) -> str:
    """解析多个 PDF 文件并合并输出"""
    parts: list[str] = []

    for path in file_paths:
        file_name = Path(path).name
        try:
            content = parse_pdf(
                path,
                use_mineru=use_mineru,
                parse_images=parse_images,
                image_parser=image_parser,
            )
            parts.append(
                f"{'='*60}\n文件：{file_name}\n{'='*60}\n{content}"
            )
        except (FileNotFoundError, ValueError) as e:
            parts.append(
                f"{'='*60}\n文件：{file_name}\n{'='*60}\n[解析失败：{e}]"
            )

    return "\n\n".join(parts)