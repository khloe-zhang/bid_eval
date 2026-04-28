import base64
import os
from pathlib import Path

from crewai import LLM

from src.bid_eval.config import DEEPSEEK_MODEL, DEEPSEEK_API_KEY


# ── 图片转 base64 ─────────────────────────────────────────────────────────────

def _to_base64(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode("utf-8")


def _image_path_to_base64(image_path: str | Path) -> str:
    return _to_base64(Path(image_path).read_bytes())


# ── 图片描述 ──────────────────────────────────────────────────────────────────

def describe_image(
    image_input: bytes | str | Path,
    context: str = "",
) -> str:
    """
    调用多模态接口描述图片内容。
    适用于技术架构图、产品截图、流程图等。

    Args:
        image_input: 图片字节、文件路径均可
        context:     图片所在的上下文描述，提升理解准确率
                     如"招标文件第3章技术架构图"

    Returns:
        图片内容的文字描述，失败时返回占位文字
    """
    try:
        # 统一转为 base64
        if isinstance(image_input, bytes):
            b64 = _to_base64(image_input)
        else:
            b64 = _image_path_to_base64(image_input)

        # 构造多模态 prompt
        context_hint = f"这张图片来自：{context}\n" if context else ""
        prompt = (
            f"{context_hint}"
            "请描述这张图片的内容，重点提取其中的技术指标、"
            "架构组件、产品规格等与招投标相关的信息。"
            "如果图片包含表格或数据，请完整列出。"
            "如果图片与招投标无关，简要说明即可。"
        )

        # 直接用 litellm 调用，支持多模态
        import litellm
        response = litellm.completion(
            model=DEEPSEEK_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{b64}"
                            },
                        },
                        {
                            "type": "text",
                            "text": prompt,
                        },
                    ],
                }
            ],
            api_key=DEEPSEEK_API_KEY,
        )

        return response.choices[0].message.content or ""

    except Exception as e:
        # 图片解析失败不中断主流程，返回占位文字
        return f"[图片内容无法解析：{e}]"


def describe_image_file(
    image_path: str | Path,
    context: str = "",
) -> str:
    """describe_image 的文件路径快捷入口"""
    return describe_image(image_path, context)