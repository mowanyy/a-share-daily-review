"""测试夹具：迷你知识库 tmp 目录（front-matter + 标题 + 术语表 + knowledge/ 样例）。"""

from __future__ import annotations

import pytest


def _write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def kb_root(tmp_path):
    """构造一个小而全的知识库目录：prompts/（含 INDEX.md + 术语表 + 模块）+ knowledge/。"""
    root = tmp_path.resolve()
    _write(
        root,
        "prompts/INDEX.md",
        "# 索引\n\n| ID | 文件 |\n|---|---|\n| x | 术语表.md |\n",
    )
    _write(
        root,
        "prompts/glossary/术语表.md",
        """---
id: glossary.terms
name: 术语表
role: glossary
status: draft
---
# 术语表

## 连板相关

| 术语 | 定义 |
|---|---|
| 首板 | 当日第一次涨停（连板数为 1） |
| 连板 | 连续多日涨停 |
| 炸板率 | 炸板家数 /（涨停家数 + 炸板家数） |
""",
    )
    _write(
        root,
        "prompts/modules/炸板.md",
        """---
id: module.break
name: 炸板
role: report
status: draft
---
# 炸板分析

## 定义

炸板是指盘中触及涨停后又打开。

## 资金观察

炸板股若主力净流入为正，次日有反包预期。
""",
    )
    _write(
        root,
        "knowledge/战法笔记.md",
        """---
id: strategy.note
name: 战法笔记
role: strategy
status: draft
---
# 战法笔记

## 连板接力

首板放量分歧后二板缩量秒板，晋级概率提升。
""",
    )
    return root


@pytest.fixture
def index(kb_root):
    """已构建好、可检索的关键词索引（共用同一 tmp 目录，测试各自独立）。"""
    from daily_review.kb.index import KnowledgeIndex

    idx = KnowledgeIndex(kb_root, use_embedding=False)
    idx.ensure_ready(force=True)
    return idx
