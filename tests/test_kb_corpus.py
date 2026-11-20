"""知识库语料测试：源发现 / 切块 / frontmatter 剔除 / 术语表按行切。"""

from __future__ import annotations

from daily_review.kb.corpus import (
    _split_long_text,
    build_corpus,
    chunk_file,
    discover_sources,
)


def test_discover_sources_skips_index_and_sorted(kb_root):
    srcs = discover_sources(kb_root)
    rels = [p.relative_to(kb_root).as_posix() for p in srcs]
    assert "prompts/INDEX.md" not in rels
    assert "prompts/glossary/术语表.md" in rels
    assert "knowledge/战法笔记.md" in rels
    assert rels == sorted(rels)


def test_discover_sources_excludes_reports_by_default(kb_root):
    p = kb_root / "output" / "20260806_复盘.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# 2026-08-06 复盘\n\n## 总览\n\n内容。", encoding="utf-8")
    rels_default = [x.relative_to(kb_root).as_posix() for x in discover_sources(kb_root)]
    assert not any(r.endswith("_复盘.md") for r in rels_default)
    rels_with = [x.relative_to(kb_root).as_posix() for x in discover_sources(kb_root, include_output_reports=True)]
    assert any(r.endswith("_复盘.md") for r in rels_with)


def test_chunk_file_strips_frontmatter_and_splits_headings(kb_root):
    p = kb_root / "prompts" / "modules" / "炸板.md"
    chunks = chunk_file(p, root=kb_root)
    assert chunks
    assert all("module.break" not in c.text for c in chunks)  # frontmatter 不进正文
    sections = {c.section for c in chunks}
    assert "炸板分析 / 定义" in sections and "炸板分析 / 资金观察" in sections


def test_glossary_table_rows_as_chunks(kb_root):
    p = kb_root / "prompts" / "glossary" / "术语表.md"
    texts = [c.text for c in chunk_file(p, root=kb_root)]
    assert any("首板：当日第一次涨停" in t for t in texts)
    assert any("炸板率：炸板家数" in t for t in texts)


def test_report_chunk_has_date_tag(kb_root):
    p = kb_root / "output" / "20260806_复盘.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# 复盘\n\n## 总览\n\n内容。", encoding="utf-8")
    chunks = chunk_file(p, root=kb_root)
    assert chunks and chunks[0].date == "20260806"
    assert "复盘" in chunks[0].tags


def test_chunk_ids_unique_and_stable(kb_root):
    chunks = build_corpus(kb_root)
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids))
    assert all("#" in cid for cid in ids)
    rels = {c.source_rel for c in chunks}
    assert "prompts/glossary/术语表.md" in rels


def test_discover_sources_includes_user_strategies(kb_root):
    """用户上传的个人战法（data/strategies/）并入知识库源。"""
    d = kb_root / "data" / "strategies"
    d.mkdir(parents=True, exist_ok=True)
    (d / "战法-测试.md").write_text(
        "---\nid: strategy.user-test\nname: 测试\nrole: strategy\nstatus: draft\n---\n\n"
        "## 1. 概述\n赚晋级预期差",
        encoding="utf-8",
    )
    rels = [p.relative_to(kb_root).as_posix() for p in discover_sources(kb_root)]
    assert "data/strategies/战法-测试.md" in rels


def test_strategy_chunks_enter_corpus(kb_root):
    d = kb_root / "data" / "strategies"
    d.mkdir(parents=True, exist_ok=True)
    (d / "战法-测试.md").write_text(
        "---\nid: strategy.user-test\nname: 测试\nrole: strategy\nstatus: draft\n---\n\n"
        "## 1. 概述\n赚晋级预期差",
        encoding="utf-8",
    )
    chunks = build_corpus(kb_root)
    texts = [c.text for c in chunks]
    assert any("赚晋级预期差" in t for t in texts)


def test_long_text_splits_by_sentence_window():
    para = "。".join("这是第%d个句子内容用于测试长段拆分" % i for i in range(120)) + "。"
    parts = _split_long_text(para, cap=100)
    assert len(parts) > 5
    assert all(parts)
    assert all(len(p) <= 150 for p in parts)
