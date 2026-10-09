"""服务端 Markdown → HTML 渲染（零 JS/CDN 依赖，XSS 安全）。

先按行做**结构解析**（代码块/标题/分隔线/表格/列表/引用），再对各块的**内容**做
html.escape → 行内加工。这样 `>` 引用不会被转义破坏，同时任意用户/LLM 文本都不会
成为可执行 HTML。支持：fenced 代码块 / 1-6 级标题 / 表格 / 无序/有序列表 / 引用 /
分隔线 / 粗斜体 / 行内代码；`[text](url)` 链接渲染为纯文本 text（去掉 href 防外链）。

section_html(md, title)：提取某 `##` 章节正文渲染为 HTML，返回 (html, found)。
"""

from __future__ import annotations

import html
import re

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_HR_RE = re.compile(r"^\s*([-*_])\1{2,}\s*$")          # --- / *** / ___
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:\-|]+\|?\s*$")
_ORDERED_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_CODE_FENCE_RE = re.compile(r"^```\s*(\w*)\s*$")
_SECTION_RE = re.compile(r"^(#{1,3})\s+(.+)$")

_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\s][^*]*?)\*(?!\*)")
_LIST_ITEM_RE = re.compile(r"^[-*+]\s+(.*)$")


def _inline(text: str) -> str:
    """行内加工（入参已转义）：行内代码（优先）→ 粗体 → 斜体 → 链接去 href。"""
    text = _INLINE_CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", text)
    text = _BOLD_RE.sub(r"<strong>\1</strong>", text)
    text = _ITALIC_RE.sub(r"<em>\1</em>", text)
    text = _LINK_RE.sub(r"\1", text)
    return text


def _esc(text: str) -> str:
    return _inline(html.escape(text))


def _scan_blocks(text: str) -> list[tuple[str, tuple]]:
    """按行扫描（原始文本）：fenced 代码块、标题、分隔线各成原子块，其余按空行分段。"""
    blocks: list[tuple[str, tuple]] = []
    lines = text.split("\n")
    i, n = 0, len(lines)
    buf: list[str] = []

    def flush() -> None:
        if buf:
            blocks.append(("text", ("\n".join(buf),)))
            buf.clear()

    while i < n:
        line = lines[i]
        stripped = line.strip()
        m = _CODE_FENCE_RE.match(stripped)
        if m:
            flush()
            lang = m.group(1)
            code: list[str] = []
            i += 1
            while i < n and not _CODE_FENCE_RE.match(lines[i].strip()):
                code.append(lines[i])
                i += 1
            i += 1  # 跳过闭合 fence（或文件尾）
            blocks.append(("code", (lang, "\n".join(code))))
            continue
        if not stripped:
            flush()
            i += 1
            continue
        # 标题 / 分隔线：单独成块（避免与正文粘连成段落）
        if _HEADING_RE.match(stripped) or _HR_RE.match(stripped):
            flush()
            blocks.append(("text", (line,)))
            i += 1
            continue
        buf.append(line)
        i += 1
    flush()
    return blocks


def _split_row(line: str) -> list[str]:
    s = line.strip().strip("|")
    return [c.strip() for c in s.split("|")]


def _is_table(lines: list[str]) -> bool:
    if len(lines) < 2:
        return False
    return lines[0].strip().startswith("|") and bool(_TABLE_SEP_RE.match(lines[1].strip()))


def _render_table(lines: list[str]) -> str:
    header = _split_row(lines[0])
    n_cols = len(header)
    parts = [
        "<table><thead><tr>"
        + "".join(f"<th>{_esc(c)}</th>" for c in header)
        + "</tr></thead><tbody>"
    ]
    for raw in lines[2:]:
        row = _split_row(raw)
        if not any(row):
            continue
        cells = row[:n_cols]
        cells += [""] * (n_cols - len(cells))
        parts.append("<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in cells) + "</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def _is_list_item(line: str) -> bool:
    s = line.lstrip()
    return bool(_LIST_ITEM_RE.match(s)) or bool(_ORDERED_RE.match(s))


def _render_list(lines: list[str]) -> str:
    ordered = bool(_ORDERED_RE.match(lines[0].lstrip()))
    tag = "ol" if ordered else "ul"
    parts = [f"<{tag}>"]
    for raw in lines:
        s = raw.lstrip()
        m = _ORDERED_RE.match(s)
        item = m.group(1) if m else ((_LIST_ITEM_RE.match(s).group(1)) if _LIST_ITEM_RE.match(s) else s)
        parts.append(f"<li>{_esc(item)}</li>")
    parts.append(f"</{tag}>")
    return "\n".join(parts)


def _render_quote(lines: list[str]) -> str:
    inner = "\n".join(ln.lstrip().lstrip(">").strip() for ln in lines if ln.strip())
    return f"<blockquote>{_esc(inner)}</blockquote>"


def _render_heading(line: str) -> str:
    m = _HEADING_RE.match(line.strip())
    level = min(len(m.group(1)), 6)
    return f"<h{level}>{_esc(m.group(2))}</h{level}>"


def _render_text_block(content: str) -> str:
    lines = content.split("\n")
    if _is_table(lines):
        return _render_table(lines)
    nonempty = [ln for ln in lines if ln.strip()]
    if nonempty and all(_is_list_item(ln) for ln in nonempty):
        return _render_list(nonempty)
    if nonempty and all(ln.lstrip().startswith(">") for ln in nonempty):
        return _render_quote(nonempty)
    if len(lines) == 1 and _HR_RE.match(lines[0].strip()):
        return "<hr>"
    if len(lines) == 1 and _HEADING_RE.match(lines[0].strip()):
        return _render_heading(lines[0])
    body = " ".join(_esc(ln) for ln in lines if ln.strip())
    return f"<p>{body}</p>"


def md_to_html(text: str) -> str:
    """Markdown 文本 → HTML。结构优先解析，内容渲染时转义（XSS 安全）。"""
    blocks = _scan_blocks(text or "")
    parts: list[str] = []
    for kind, content in blocks:
        if kind == "code":
            lang, code = content
            cls = f' class="language-{lang}"' if lang else ""
            parts.append(f"<pre><code{cls}>{html.escape(code)}</code></pre>")
        else:
            parts.append(_render_text_block(content[0]))
    return "\n".join(parts)


def section_html(md: str, title: str) -> tuple[str, bool]:
    """提取标题为 title 的章节正文并渲染 HTML；未找到返回 ("", False)。

    章节结束判定：下一个同级或更高级（更浅）标题。
    """
    lines = (md or "").split("\n")
    start: int | None = None
    for i, ln in enumerate(lines):
        m = _SECTION_RE.match(ln)
        if m and m.group(2).strip() == title.strip():
            start = i
            break
    if start is None:
        return "", False
    level = len(_SECTION_RE.match(lines[start]).group(1))  # type: ignore[union-attr]
    end = len(lines)
    for j in range(start + 1, len(lines)):
        m = _SECTION_RE.match(lines[j])
        if m and len(m.group(1)) <= level:
            end = j
            break
    body = "\n".join(lines[start + 1 : end]).strip()
    return md_to_html(body), True
