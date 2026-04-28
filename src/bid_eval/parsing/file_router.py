from pathlib import Path

from src.bid_eval.parsing.docx_parser import parse_docx_files
from src.bid_eval.parsing.pdf_parser import parse_pdf_files


def parse_files(
    file_paths: list[str | Path],
    use_mineru: bool = False,
    parse_images: bool = False,
) -> str:
    """
    统一文件解析入口，按扩展名自动分发到对应解析器。
    支持 .docx 和 .pdf 混合上传。

    Args:
        file_paths:   文件路径列表，可混合 docx 和 pdf
        use_mineru:   PDF 解析是否启用 MinerU
        parse_images: 是否解析图片内容（调用多模态 LLM，有额外成本）

    Returns:
        所有文件内容合并后的纯文本
    """
    docx_files = [p for p in file_paths if Path(p).suffix.lower() == ".docx"]
    pdf_files  = [p for p in file_paths if Path(p).suffix.lower() == ".pdf"]
    unknown    = [
        p for p in file_paths
        if Path(p).suffix.lower() not in (".docx", ".pdf")
    ]

    parts: list[str] = []

    if docx_files:
        parts.append(parse_docx_files(docx_files))

    if pdf_files:
        image_parser = None
        if parse_images:
            from bid_eval.parsing import image_parser as _image_parser
            image_parser = _image_parser

        parts.append(
            parse_pdf_files(
                pdf_files,
                use_mineru=use_mineru,
                parse_images=parse_images,
                image_parser=image_parser,
            )
        )

    for p in unknown:
        parts.append(f"[不支持的文件格式：{Path(p).name}，已跳过]")

    return "\n\n".join(parts)