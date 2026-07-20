import re

from markdown import markdown


MARKDOWN_T2I_TEMPLATE = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <style>
    * { box-sizing: border-box; }
    body {
      color: #1f2328;
      background: #ffffff;
      font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
      font-size: 16px;
      line-height: 1.7;
      margin: 0;
      padding: 32px;
      overflow-wrap: anywhere;
    }
    h1, h2, h3, h4, h5, h6 { line-height: 1.35; margin: 1.2em 0 0.55em; }
    h1 { border-bottom: 1px solid #d0d7de; font-size: 28px; padding-bottom: 0.3em; }
    h2 { border-bottom: 1px solid #d0d7de; font-size: 23px; padding-bottom: 0.25em; }
    h3 { font-size: 19px; }
    p, ul, ol, blockquote, pre, table { margin: 0.8em 0; }
    blockquote { border-left: 4px solid #d0d7de; color: #57606a; margin-left: 0; padding-left: 1em; }
    pre, code { background: #f6f8fa; font-family: "SFMono-Regular", Menlo, monospace; }
    pre { border-radius: 6px; padding: 14px; white-space: pre-wrap; }
    code { border-radius: 4px; padding: 0.15em 0.3em; }
    pre code { padding: 0; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border: 1px solid #d0d7de; padding: 8px 11px; text-align: left; vertical-align: top; }
    th { background: #f6f8fa; font-weight: 600; }
    tr:nth-child(even) { background: #f6f8fa; }
    a { color: #0969da; }
  </style>
</head>
<body>{{ content | safe }}</body>
</html>
"""


def markdown_to_html(markdown_content: str) -> str:
    content = markdown_content.strip()
    if not content:
        raise ValueError("Markdown 内容不能为空")

    safe_content = re.sub(
        r"<[^>\n]+>",
        lambda match: match.group(0).replace("<", "&lt;").replace(">", "&gt;"),
        content,
    )
    content_html = markdown(
        safe_content,
        extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
    )
    return re.sub(r"<img\b[^>]*>", "", content_html, flags=re.IGNORECASE)
