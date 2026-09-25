"""知识库语料：源发现 + Markdown 切块。

源：prompts/**/*.md（跳过 INDEX.md）+ docs/{需求分析,数据结构,战法规范}.md +
    knowledge/**/*.md（用户持续更新的知识目录）+ data/strategies/**/*.md（用户上传的个人战法）+
    skills/**/*.md（项目内 skill 档案，基金风格等）+ 可选 output/*_复盘.md（带日期标签）。

切块规则：
- 按 `##`/`###` 标题切分，章节路径作为 section（可展示出处）
- 术语表等「术语 | 定义」表格按行切（术语名=行首列），检索「什么是炸板率」命中单条定义
- 长段落按空行/约 700 字二次切
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
from pathlib import Path

from daily_review.config import PROJECT_ROOT
from daily_review.prompts import _FRONT_MATTER_RE

# 收入知识库的 docs 白名单（含短线知识语义；开发/环境类文档不入库）
SEED_DOCS = ("需求分析.md", "数据结构.md", "战法规范.md")

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")
_REPORT_RE = re.compile(r"^(\d{8})_复盘\.md$")
_TERM_TABLE_HEADER_RE = re.compile(r"^\s*\|?\s*术语")
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:\-|]+\|?\s*$")


@dataclass
class Chunk:
    """知识库检索的最小单元。"""

    chunk_id: str                 # f"{source_rel}#{idx:03d}"
    source_rel: str               # 相对项目根，如 prompts/glossary/术语表.md
    section: str                  # 章节路径，如「晋级博弈」
    idx: int                      # 文件内第几个 chunk
    text: str
    date: str | None = None       # 仅复盘报告（YYYYMMDD）
    tags: list[str] = field(default_factory=list)
    source_hash: str = ""         # 所属文件 sha256（增量重建用）


def file_sha256(path: Path) -> str:
    """文件内容 sha256（增量重建的变更指纹）。"""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def strip_frontmatter(text: str) -> str:
    """去掉 YAML front-matter（复用 prompts 的正则），返回正文。"""
    return _FRONT_MATTER_RE.sub("", text).strip()


def discover_sources(root: Path | None = None, *, include_output_reports: bool = False) -> list[Path]:
    """返回入库源文件路径（相对 root 排序去重）。

    root 缺省为项目根；测试可传 tmp 目录。
    """
    root = (root or PROJECT_ROOT).resolve()
    paths: list[Path] = []

    prompts_dir = root / "prompts"
    if prompts_dir.exists():
        for p in sorted(prompts_dir.rglob("*.md")):
            if p.name == "INDEX.md":
                continue
            paths.append(p)

    docs_dir = root / "docs"
    for name in SEED_DOCS:
        p = docs_dir / name
        if p.exists():
            paths.append(p)

    kb_dir = root / "knowledge"
    if kb_dir.exists():
        paths.extend(sorted(kb_dir.rglob("*.md")))

    # 用户上传的个人战法（data/strategies/，gitignored）——并入知识库，QA 可检索
    strat_dir = root / "data" / "strategies"
    if strat_dir.exists():
        paths.extend(sorted(strat_dir.rglob("*.md")))

    # 项目内 skill 档案（skills/，v0.17，git 入库）——并入知识库，QA 可检索风格档案
    skills_dir = root / "skills"
    if skills_dir.exists():
        paths.extend(sorted(skills_dir.rglob("*.md")))

    if include_output_reports:
        out_dir = root / "output"
        if out_dir.exists():
            paths.extend(sorted(out_dir.rglob("*_复盘.md")))

    # 去重（按相对路径）并保持稳定顺序
    seen: set[str] = set()
    result: list[Path] = []
    for p in sorted(paths, key=lambda x: x.relative_to(root).as_posix()):
        rel = p.relative_to(root).as_posix()
        if rel not in seen:
            seen.add(rel)
            result.append(p)
    return result


def _split_headings(text: str) -> list[tuple[str, str]]:
    """按 1-3 级标题切分，返回 [(section_path, body)]。无标题时 section='（开头）'。"""
    sections: list[tuple[str, str]] = []
    cur_path: list[str] = []
    cur_lines: list[str] = []
    cur_heading: str = "（开头）"
    have_any = False

    def flush() -> None:
        nonlocal cur_lines, cur_heading
        body = "\n".join(cur_lines).strip()
        if body or cur_heading != "（开头）":
            sections.append((cur_heading, body))
        cur_lines = []

    for line in text.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            flush()
            level = len(m.group(1))
            title = m.group(2).strip()
            cur_path = cur_path[: level - 1] + [title]
            cur_heading = " / ".join(cur_path)
            have_any = True
        else:
            cur_lines.append(line)
    flush()
    if not have_any and len(sections) == 1 and sections[0][0] == "（开头）":
        return sections
    return sections


def _is_table_body(body: str) -> bool:
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    if not lines:
        return False
    return all(ln.startswith("|") for ln in lines)


def _table_rows_to_chunks(body: str) -> list[str]:
    """表格正文 → 每数据行一条文本（跳过表头/分隔行）。"""
    rows: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or _TABLE_SEP_RE.match(line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        cells = [c for c in cells if c]
        if not cells:
            continue
        if _TERM_TABLE_HEADER_RE.match(cells[0]) or (len(cells) >= 2 and cells[0] == "术语"):
            continue
        if len(cells) == 1:
            rows.append(cells[0])
        elif len(cells) == 2:
            rows.append(f"{cells[0]}：{cells[1]}")
        else:
            rows.append("，".join(cells))
    return [r for r in rows if r]


def _split_long_text(text: str, cap: int = 700) -> list[str]:
    """按空行分段；单段超 cap 再按句子边界切窗。"""
    if not text:
        return []
    parts: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= cap:
            parts.append(para)
            continue
        # 长段：按中文句号/分号/换行切窗
        sentences = re.split(r"(?<=[。！？；;\n])", para)
        buf = ""
        for s in sentences:
            s = s.strip()
            if not s:
                continue
            if buf and len(buf) + len(s) > cap:
                parts.append(buf.strip())
                buf = s
            else:
                buf += s
        if buf.strip():
            parts.append(buf.strip())
    return parts


def chunk_file(path: Path, *, root: Path | None = None) -> list[Chunk]:
    """单文件切块。root 用于计算相对路径；缺省取 path.parent 之上自动推断（见 build_corpus）。"""
    root = (root or PROJECT_ROOT).resolve()
    rel = path.resolve().relative_to(root).as_posix()
    text = path.read_text(encoding="utf-8")
    body_text = strip_frontmatter(text)
    sha = file_sha256(path)

    date: str | None = None
    tags: list[str] = []
    m = _REPORT_RE.match(path.name)
    if m:
        date = m.group(1)
        tags.append("复盘")

    chunks: list[Chunk] = []
    idx = 0
    for section, body in _split_headings(body_text):
        if body and _is_table_body(body):
            for row_text in _table_rows_to_chunks(body):
                if not row_text:
                    continue
                chunks.append(
                    Chunk(
                        chunk_id=f"{rel}#{idx:03d}",
                        source_rel=rel,
                        section=section,
                        idx=idx,
                        text=row_text,
                        date=date,
                        tags=tags,
                        source_hash=sha,
                    )
                )
                idx += 1
            continue
        for para in _split_long_text(body):
            if not para:
                continue
            chunks.append(
                Chunk(
                    chunk_id=f"{rel}#{idx:03d}",
                    source_rel=rel,
                    section=section,
                    idx=idx,
                    text=para,
                    date=date,
                    tags=tags,
                    source_hash=sha,
                )
            )
            idx += 1
    return chunks


def build_corpus(root: Path | None = None, *, include_output_reports: bool = False) -> list[Chunk]:
    """全量建语料：发现源 → 逐文件切块。"""
    root = (root or PROJECT_ROOT).resolve()
    chunks: list[Chunk] = []
    for path in discover_sources(root, include_output_reports=include_output_reports):
        chunks.extend(chunk_file(path, root=root))
    return chunks
