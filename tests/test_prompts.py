"""Prompt 骨架测试：校验 front-matter 约定与 INDEX.md 索引一致性。"""

from pathlib import Path
import re

from daily_review.prompts import iter_prompts

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_all_prompt_files_have_required_front_matter():
    """每个 prompt 文件必须含 id/name/role/status。"""
    prompts = iter_prompts()
    assert len(prompts) >= 9, f"应至少加载 9 个 prompt，实际 {len(prompts)}"
    for p in prompts:
        assert p.id, f"[{p.path}] 缺 id"
        assert p.name, f"[{p.path}] 缺 name"
        assert p.role in {"report", "qa", "strategy", "tool", "example", "glossary"}, (
            f"[{p.path}] role 非法: {p.role}"
        )
        assert p.status in {"draft", "active"}, f"[{p.path}] status 非法: {p.status}"


def test_prompt_ids_unique():
    """prompt ID 全局唯一。"""
    ids = [p.id for p in iter_prompts()]
    assert len(ids) == len(set(ids)), f"存在重复 ID: {ids}"


def test_index_md_entries_match_files():
    """INDEX.md 中登记的 [文件](相对路径) 必须真实存在。"""
    index_path = PROJECT_ROOT / "prompts" / "INDEX.md"
    text = index_path.read_text(encoding="utf-8")
    # 匹配 markdown 链接（排除外部 http 链接）
    links = re.findall(r"\]\(([^)]+)\)", text)
    md_links = [l for l in links if l.endswith(".md")]
    assert md_links, "INDEX.md 未找到任何 .md 链接"
    for rel in md_links:
        target = (index_path.parent / rel).resolve()
        assert target.exists(), f"INDEX.md 链接失效: {rel}"
