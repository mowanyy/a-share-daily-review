"""web/md.py 服务端 Markdown 渲染测试：XSS 安全 / 结构 / 章节提取 / 零外链。"""

from __future__ import annotations

from daily_review.web.md import md_to_html, section_html


def _sample_md() -> str:
    return """# 标题

## 一、总览
涨停 **50** 家，炸板率 `25%`，[外链](https://evil.example.com/a)。

| 指标 | 数值 |
|---|---|
| 涨停 | 50 |
| 炸板 | 12 |

- 甲
- 乙

> 引用内容

---

单独段落 *斜体* 与 **粗体**。
"""


def test_script_escaped():
    out = md_to_html("<script>alert(1)</script>")
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out


def test_table_cell_script_escaped():
    out = md_to_html("| 列 |\n|---|---|\n| <script>x</script> |")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_link_href_removed():
    out = md_to_html("[点我](https://evil.example.com/x)")
    assert "href=" not in out
    assert "https://evil.example.com/x" not in out
    assert "点我" in out


def test_no_external_urls():
    out = md_to_html(_sample_md())
    for bad in ("http://", "https://", "<script src", "<link"):
        assert bad not in out


def test_headings_table_list_quote_hr_bold_italic_code():
    out = md_to_html(_sample_md())
    assert "<h1>" in out and "<h2>" in out
    assert "<table><thead>" in out and "<td>50</td>" in out
    assert "<li>甲</li>" in out and "<ul>" in out
    assert "<blockquote>引用内容</blockquote>" in out
    assert "<hr>" in out
    assert "<strong>50</strong>" in out and "<em>斜体</em>" in out
    assert "<code>25%</code>" in out


def test_fenced_code_block_escaped():
    out = md_to_html("```python\nprint('<script>')\n```")
    assert "<pre><code" in out
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_ordered_list():
    out = md_to_html("1. 一\n2. 二")
    assert "<ol>" in out and "<li>一</li>" in out


def test_section_html_found_and_stops_at_next():
    md = """## 七、次日预案
关注方向：**低吸**。

## 六、其他
不该出现
"""
    html, found = section_html(md, "七、次日预案")
    assert found
    assert "<strong>低吸</strong>" in html
    assert "不该出现" not in html


def test_section_html_not_found():
    html, found = section_html("# 只有一\n\n无此章节", "七、次日预案")
    assert not found and html == ""


def test_heading_followed_by_text_no_blank_line():
    out = md_to_html("## 标题\n紧跟着的正文")
    assert "<h2>标题</h2>" in out
    assert "紧跟着的正文" in out
