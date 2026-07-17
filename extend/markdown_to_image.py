import asyncio
import base64
import re

import fitz
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Image, Reply
from markdown import markdown
from weasyprint import HTML


_PAGE_CSS = """
@page { size: A4; margin: 14mm; }
* { box-sizing: border-box; }
body {
    color: #1f2328;
    font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
    font-size: 14px;
    line-height: 1.65;
    overflow-wrap: anywhere;
}
h1, h2, h3, h4, h5, h6 { line-height: 1.3; margin: 1.1em 0 0.55em; }
h1 { font-size: 24px; border-bottom: 1px solid #d0d7de; padding-bottom: 0.3em; }
h2 { font-size: 20px; border-bottom: 1px solid #d0d7de; padding-bottom: 0.25em; }
h3 { font-size: 17px; }
p, ul, ol, blockquote, pre, table { margin: 0.75em 0; }
blockquote { border-left: 4px solid #d0d7de; color: #57606a; margin-left: 0; padding-left: 1em; }
pre, code { background: #f6f8fa; font-family: "SFMono-Regular", Menlo, monospace; }
pre { border-radius: 6px; padding: 12px; white-space: pre-wrap; }
code { border-radius: 4px; padding: 0.15em 0.3em; }
pre code { padding: 0; }
table { border-collapse: collapse; display: table; max-width: 100%; width: 100%; }
th, td { border: 1px solid #d0d7de; padding: 7px 10px; text-align: left; vertical-align: top; }
th { background: #f6f8fa; font-weight: 600; }
tr:nth-child(even) { background: #f6f8fa; }
a { color: #0969da; text-decoration: none; }
img { display: none; }
"""


def render_markdown_to_pngs(markdown_content: str) -> list[bytes]:
    """将 Markdown 渲染为适合聊天发送的 PNG 页面。"""
    content = markdown_content.strip()
    if not content:
        raise ValueError("Markdown 内容不能为空")

    safe_content = re.sub(
        r"<[^>\n]+>",
        lambda match: match.group(0).replace("<", "&lt;").replace(">", "&gt;"),
        content,
    )
    body = markdown(
        safe_content,
        extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
    )
    body = re.sub(r"<img\b[^>]*>", "", body, flags=re.IGNORECASE)
    document = f"<!doctype html><html><head><meta charset=\"utf-8\"><style>{_PAGE_CSS}</style></head><body>{body}</body></html>"
    pdf = HTML(string=document).write_pdf()

    with fitz.open(stream=pdf, filetype="pdf") as pdf_document:
        return [
            page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).tobytes("png")
            for page in pdf_document
        ]


async def handle_render_markdown(
    event: AstrMessageEvent,
    markdown_content: str,
) -> str:
    """渲染并发送 Markdown 图片，成功时阻止模型再发送原始 Markdown。"""
    try:
        images = await asyncio.to_thread(render_markdown_to_pngs, markdown_content)
        if not images:
            raise ValueError("Markdown 未生成图片")

        components: list[object] = [Reply(id=event.message_obj.message_id)]
        components.extend(
            Image.fromBase64(base64.b64encode(image).decode("ascii"))
            for image in images
        )
        await event.send(MessageChain(components))
        event.stop_event()
        return "Markdown 已转换为图片并发送。不要再输出原始 Markdown 或重复内容。"
    except Exception as exc:
        return f"Markdown 转图片失败：{exc}"
