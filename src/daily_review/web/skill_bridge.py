"""战法 ↔ SKILL.md 双向桥（v0.17）：让个人战法与 ZCode skill 互相转换。

- `import_skill`: SKILL.md（front-matter name/description + 规则正文）→ 个人战法，
  复用 web/strategy.py 的 create()/make_id() 链路（id 确定性生成、同名覆盖、净化防穿越）。
  正文原样保留；skill 正文没有战法 8 节 → 走现有 validate() 缺节告警（仅提示，不拒绝执行）。
- `export_strategy`: 战法（tracked 或 user）→ SKILL.md 文本（name + description + 正文原样），
  供分享 / 在其他会话按固定规则执行。

约定：本桥仅限本项目内使用——skill 档案放在项目 `skills/` 目录，
不写入用户级 skill 目录、不注册为全局 /命令；消费走「本项目会话引用 + 问答知识库检索」。
"""

from __future__ import annotations

from daily_review.prompts import Prompt, _FRONT_MATTER_RE, _parse_front_matter
from daily_review.web.strategy import (
    StrategyError,
    create as strategy_create,
    get_strategy,
    source_of,
    to_dict,
)

# 导入时插入战法正文前的说明块（提醒这是 skill 转来的、待按 8 节补全。
# 注意：措辞刻意不出现「选股/买入规则/……」等节名关键词，避免误触 missing_sections 关键字检查）
_IMPORT_NOTE = (
    "> **由 ZCode skill 导入（v0.17 桥接）**\n"
    "> 原 skill 说明：{description}\n"
    "> 本正文按 skill 原样保留，未经战法 8 节规范改写；\n"
    "> 建议在 Web 战法管理页编辑补全规则章节后再用于驱动次日预案。\n"
)


def _applies_from_description(desc: str) -> str:
    """从 skill description 提炼一句话放到战法 applies_to（超长/无则不标）。"""
    if not desc:
        return "（未指定，见正文）"
    first = desc.splitlines()[0].strip()
    if len(first) > 40:
        return "（未指定，见正文）"
    return first


def import_skill(markdown: str, *, status: str = "draft") -> dict:
    """把 SKILL.md 导入为个人战法。

    返回 to_dict(战法)（含 id/name/status/source/missing_sections）；
    非法输入抛 ValueError；落盘失败抛 StrategyError。
    """
    text = (markdown or "").strip()
    if not text:
        raise ValueError("缺少 SKILL.md 内容")
    meta = _parse_front_matter(text)
    name = ""
    description = ""
    if meta is not None:
        name = str(meta.get("name", "")).strip()
        description = str(meta.get("description", "")).strip()
    # 有 front-matter 却无 name → 明确报错（不拿 `---`/description 当名字）
    if not name and meta is not None:
        raise ValueError("SKILL.md front-matter 缺少 name 字段")
    # 无 front-matter → 兜底取首个标题行
    if not name:
        head = next((ln.strip().lstrip("#").strip() for ln in text.splitlines() if ln.strip()), "")
        name = head
    if not name:
        raise ValueError("SKILL.md 缺少 name（front-matter 或标题）")
    body = _FRONT_MATTER_RE.sub("", text).strip()
    if not body:
        raise ValueError("SKILL.md 正文为空")
    st = status if status in ("draft", "active") else "draft"
    doc = (
        "---\n"
        f"name: {name}\n"
        f"status: {st}\n"
        f"applies_to: {_applies_from_description(description)}\n"
        "author: 从 skill 导入\n"
        "---\n\n"
        f"{_IMPORT_NOTE.format(description=description or '（未提供）')}\n\n"
        f"{body}\n"
    )
    pr = strategy_create(doc)
    return to_dict(pr)


def _description_for(pr: Prompt) -> str:
    """从战法元信息生成 SKILL.md 的 description（skill 触发关键词；限长防御）。"""
    applies = pr.applies_to or "适用于短线复盘决策"
    head = ""
    for line in (pr.body or "").splitlines():
        ln = line.strip()
        # 跳过引用块/HTML 注释等非正文首行（如导入注记）
        if not ln or ln.startswith((">", "<!--", "#")):
            continue
        head = ln.lstrip("#").strip()
        break
    text = f"以「{pr.name}」为固定规则分析 A 股个股/板块/复盘预案；{applies}"
    if head and head not in text:
        text += f"。核心要点：{head}"
    return text[:120]


def export_strategy(strategy_id: str) -> str:
    """战法 → SKILL.md 文本。tracked（模板/示例）与 user 战法均支持；未找到抛 StrategyError(404)。"""
    pr = get_strategy(strategy_id)
    if pr is None:
        raise StrategyError(f"未找到战法 {strategy_id}", 404)
    src = source_of(pr.path) if pr.path else "user"
    body = (pr.body or "").strip()
    return (
        "---\n"
        f"name: {pr.name}\n"
        f"description: {_description_for(pr)}\n"
        "---\n\n"
        f"<!-- 来源于每日复盘战法 {pr.id}（{src} 目录）。改动本文件不影响原战法。 -->\n\n"
        f"{body}\n"
    )