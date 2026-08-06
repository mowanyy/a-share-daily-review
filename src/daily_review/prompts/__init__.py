"""Prompt 加载与渲染引擎（v0.1 骨架）。

- 读取 prompts/ 下所有 prompt 文件（含 YAML front-matter 与正文）
- front-matter 用标准库解析，不依赖第三方包
- 通过 prompts/INDEX.md 校验索引一致性（见 tests/test_prompts.py）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

from daily_review.config import get_settings

_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


@dataclass
class Prompt:
    """一个 prompt 文件：front-matter 元信息 + 正文。"""

    id: str
    name: str
    role: str
    status: str
    version: str = ""
    depends: list[str] = field(default_factory=list)
    output: str = ""
    path: Path | None = None
    body: str = ""

    @property
    def is_active(self) -> bool:
        return self.status == "active"


def _parse_front_matter(text: str) -> dict | None:
    """解析简单 YAML front-matter（键值对，支持标量与 [] 列表）。"""
    m = _FRONT_MATTER_RE.match(text)
    if not m:
        return None
    data: dict = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        # 列表解析： [a, b]
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            data[key] = [v.strip().strip("'\"") for v in inner.split(",") if v.strip()]
        else:
            data[key] = value.strip("'\"")
    return data or None


def load_prompt_file(path: Path) -> Prompt | None:
    """从单个 .md 文件加载 Prompt；无合法 front-matter 返回 None。"""
    text = path.read_text(encoding="utf-8")
    meta = _parse_front_matter(text)
    if meta is None:
        return None
    required = {"id", "name", "role", "status"}
    missing = required - meta.keys()
    if missing:
        raise ValueError(f"[{path.name}] front-matter 缺少字段: {sorted(missing)}")
    body = _FRONT_MATTER_RE.sub("", text).strip()
    return Prompt(
        id=str(meta["id"]),
        name=str(meta["name"]),
        role=str(meta["role"]),
        status=str(meta["status"]),
        version=str(meta.get("version", "")),
        depends=list(meta.get("depends", [])),
        output=str(meta.get("output", "")),
        path=path,
        body=body,
    )


def iter_prompts() -> list[Prompt]:
    """扫描 prompts/ 目录，返回全部含合法 front-matter 的 Prompt。"""
    prompts_dir = get_settings().prompts_dir
    prompts: list[Prompt] = []
    for path in sorted(prompts_dir.rglob("*.md")):
        if path.name == "INDEX.md":
            continue
        prompt = load_prompt_file(path)
        if prompt is not None:
            prompts.append(prompt)
    return prompts


def get_prompt(prompt_id: str) -> Prompt | None:
    """按 ID 取 Prompt。"""
    for p in iter_prompts():
        if p.id == prompt_id:
            return p
    return None
