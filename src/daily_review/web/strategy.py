"""战法服务：用户上传战法（gitignored data/strategies/）+ 模板示例（prompts/strategies/ 只读）。

- 复用 daily_review.prompts 的解析器（load_prompt_file / _parse_front_matter / _FRONT_MATTER_RE），
  存储文件本身就是一个合法 prompt 文件（YAML front-matter + 正文）
- 用户战法 id 统一生成 `strategy.user-<sha256(name)[:10]>`：确定性、不与 tracked 冲突
- tracked（prompts/strategies/ 的模板示例）只读：update/delete/set_status 抛 StrategyError(403)
- validate 对 8 节做关键字告警：missing_sections 仅提示不拒绝
- 路径防护：文件名净化 + resolve().is_relative_to 拦截穿越
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from daily_review.config import get_settings
from daily_review.prompts import (
    Prompt,
    _FRONT_MATTER_RE,
    _parse_front_matter,
    load_prompt_file,
)

_USER_ID_PREFIX = "strategy.user-"

# 8 节契约（对应 prompts/strategies/战法模板.md，缺节仅告警）
_REQUIRED_SECTIONS = [
    "概述",
    "适用情绪阶段",
    "选股",
    "买入规则",
    "卖出与止损",
    "仓位管理",
    "规避与风险",
    "复盘记录",
]


class StrategyError(Exception):
    """战法操作错误。code 映射 HTTP 状态：400 参数/校验、403 只读、404 未找到。"""

    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------- 目录


def user_dir() -> Path:
    """用户上传战法目录（gitignored，data/*/ 已排除，不入项目）。"""
    return get_settings().data_dir / "strategies"


def tracked_dir() -> Path:
    """模板示例目录（prompts/strategies/，只读，入库）。"""
    return get_settings().prompts_dir / "strategies"


def source_of(path: Path) -> str:
    p = path.resolve()
    if tracked_dir().exists() and p.is_relative_to(tracked_dir().resolve()):
        return "tracked"
    return "user"


# ---------------------------------------------------------------- id / 文件名


def make_id(name: str) -> str:
    digest = hashlib.sha256(name.strip().encode("utf-8")).hexdigest()[:10]
    return f"{_USER_ID_PREFIX}{digest}"


def _sanitize_filename(name: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", name.strip())
    s = s.strip("._ ")
    return s or "未命名"


def _reserve_path(name: str) -> Path:
    """文件名：战法-<name>.md；同名冲突追加短 id。路径穿越由净化 + is_relative_to 双保险。"""
    root = user_dir().resolve()
    root.mkdir(parents=True, exist_ok=True)
    base = _sanitize_filename(name)
    candidate = root / f"战法-{base}.md"
    if not candidate.exists():
        return candidate
    return root / f"战法-{base}-{make_id(name).split('-')[-1]}.md"


# ---------------------------------------------------------------- 读取


def _find_in(d: Path, strategy_id: str) -> Path | None:
    if not d.exists():
        return None
    for p in sorted(d.rglob("*.md")):
        pr = load_prompt_file(p)
        if pr is not None and pr.id == strategy_id:
            return p
    return None


def _path_for_id(strategy_id: str) -> Path | None:
    if strategy_id.startswith(_USER_ID_PREFIX):
        return _find_in(user_dir(), strategy_id)
    return _find_in(tracked_dir(), strategy_id)


def iter_all() -> list[Prompt]:
    """合并 tracked（只读）+ user，按 name 排序；id 去重（tracked 优先）。"""
    result: list[Prompt] = []
    for d in (tracked_dir(), user_dir()):
        if not d.exists():
            continue
        for p in sorted(d.rglob("*.md")):
            pr = load_prompt_file(p)
            if pr is not None:
                result.append(pr)
    seen: set[str] = set()
    out: list[Prompt] = []
    for pr in sorted(result, key=lambda x: x.name):
        if pr.id in seen:
            continue
        seen.add(pr.id)
        out.append(pr)
    return out


def get_strategy(strategy_id: str) -> Prompt | None:
    for pr in iter_all():
        if pr.id == strategy_id:
            return pr
    return None


get = get_strategy


# ---------------------------------------------------------------- 校验


def missing_sections(p: Prompt) -> list[str]:
    return [s for s in _REQUIRED_SECTIONS if s not in (p.body or "")]


def validate(p: Prompt) -> list[str]:
    """返回缺节列表（仅告警，不拒绝）。"""
    return missing_sections(p)


def to_dict(p: Prompt) -> dict:
    src = source_of(p.path) if p.path else "user"
    return {
        "id": p.id,
        "name": p.name,
        "status": p.status,
        "version": p.version or "0.1.0",
        "author": p.author,
        "applies_to": p.applies_to,
        "source": src,
        "missing_sections": missing_sections(p),
    }


# ---------------------------------------------------------------- 写操作


def _rewrite(
    path: Path,
    *,
    body: str | None = None,
    name: str | None = None,
    author: str | None = None,
    applies_to: str | None = None,
    status: str | None = None,
) -> Prompt:
    pr = load_prompt_file(path)
    if pr is None:
        raise StrategyError("战法解析失败")
    new_status = status if status in ("draft", "active") else pr.status
    doc = (
        "---\n"
        f"id: {pr.id}\n"
        f"name: {name if name is not None else pr.name}\n"
        "role: strategy\n"
        f"status: {new_status}\n"
        f"version: {pr.version or '0.1.0'}\n"
        f"author: {author if author is not None else pr.author}\n"
        f"applies_to: {applies_to if applies_to is not None else pr.applies_to}\n"
        "---\n\n"
        f"{(body if body is not None else pr.body).strip()}\n"
    )
    path.write_text(doc, encoding="utf-8")
    loaded = load_prompt_file(path)
    if loaded is None:
        raise StrategyError("战法写入后解析失败")
    return loaded


def create(
    markdown: str,
    *,
    name: str = "",
    author: str = "",
    applies_to: str = "",
    status: str = "draft",
) -> Prompt:
    """新建用户战法。markdown 可带 YAML front-matter（其元信息优先于入参）。

    落盘 data/strategies/战法-<name>.md（gitignored，不入项目）；id 不信任用户提供。
    """
    body_text = markdown.strip()
    meta = _parse_front_matter(markdown)
    if meta is not None:
        name = str(meta.get("name", name) or name or "")
        author = str(meta.get("author", author) or author or "")
        applies_to = str(meta.get("applies_to", applies_to) or applies_to or "")
        status = str(meta.get("status", status) or status or "")
        body_text = _FRONT_MATTER_RE.sub("", markdown).strip()
    name = name.strip()
    if not name:
        raise StrategyError("缺少战法名称（name 或 front-matter 的 name 字段）")
    st = status if status in ("draft", "active") else "draft"
    strat_id = make_id(name)
    doc = (
        "---\n"
        f"id: {strat_id}\n"
        f"name: {name}\n"
        "role: strategy\n"
        f"status: {st}\n"
        "version: 0.1.0\n"
        f"author: {author}\n"
        f"applies_to: {applies_to}\n"
        "---\n\n"
        f"{body_text}\n"
    )
    # 同名（同 id）已存在 → 覆盖该文件，保持 name↔id↔file 1:1（避免重复 id）
    existing = _find_in(user_dir(), strat_id)
    path = existing if existing is not None else _reserve_path(name)
    path.write_text(doc, encoding="utf-8")
    pr = load_prompt_file(path)
    if pr is None:
        raise StrategyError("战法写入失败：解析异常")
    return pr


def update(
    strategy_id: str,
    markdown: str,
    *,
    name: str = "",
    author: str = "",
    applies_to: str = "",
    status: str = "",
) -> Prompt:
    """整体替换用户战法正文/元信息（保留原 id 与文件路径）。tracked → StrategyError(403)。"""
    path = _path_for_id(strategy_id)
    if path is None:
        raise StrategyError(f"未找到战法 {strategy_id}", 404)
    if source_of(path) == "tracked":
        raise StrategyError("模板示例战法只读，不能修改", 403)
    body_text = markdown.strip()
    meta = _parse_front_matter(markdown)
    if meta is not None:
        body_text = _FRONT_MATTER_RE.sub("", markdown).strip()
        name = str(meta.get("name", name) or name or "")
        author = str(meta.get("author", author) or author or "")
        applies_to = str(meta.get("applies_to", applies_to) or applies_to or "")
        status = str(meta.get("status", status) or status or "")
    return _rewrite(
        path,
        body=body_text or None,
        name=name or None,
        author=author or None,
        applies_to=applies_to or None,
        status=status or None,
    )


def delete(strategy_id: str) -> None:
    path = _path_for_id(strategy_id)
    if path is None:
        raise StrategyError(f"未找到战法 {strategy_id}", 404)
    if source_of(path) == "tracked":
        raise StrategyError("模板示例战法只读，不能删除", 403)
    path.unlink(missing_ok=True)


def set_status(strategy_id: str, status: str) -> Prompt:
    if status not in ("draft", "active"):
        raise StrategyError("status 只能为 draft 或 active")
    path = _path_for_id(strategy_id)
    if path is None:
        raise StrategyError(f"未找到战法 {strategy_id}", 404)
    if source_of(path) == "tracked":
        raise StrategyError("模板示例战法只读，不能切换状态", 403)
    return _rewrite(path, status=status)
