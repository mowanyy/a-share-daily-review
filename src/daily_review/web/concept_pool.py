"""概念池服务：Agent/Web 对概念池的增删改查（gitignored data/stock_pool/concepts/）。

每个概念对应一个 CSV 文件：data/stock_pool/concepts/{概念名}.csv
格式：code,name,added_date,note
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from daily_review.config import get_settings

_COLUMNS = ["code", "name", "added_date", "note"]


def pool_dir() -> Path:
    """概念池存储目录（gitignored，data/*/ 已排除）。"""
    return get_settings().stock_pool_dir / "concepts"


def _sanitize_name(name: str) -> str:
    """文件名净化，防路径穿越。"""
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", name.strip())
    return s.strip("._ ") or "未命名"


def _path(name: str) -> Path:
    d = pool_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{_sanitize_name(name)}.csv"


def _validate_date(d: str) -> str:
    """校验日期格式 YYYYMMDD。"""
    d = d.strip()
    if not re.fullmatch(r"\d{8}", d):
        raise ValueError(f"日期格式错误（需 YYYYMMDD）: {d}")
    return d


# ---------------------------------------------------------------- 查询

def list_pools() -> list[dict]:
    """列出所有概念池：name, stock_count, created_at。"""
    d = pool_dir()
    if not d.exists():
        return []
    result = []
    for p in sorted(d.glob("*.csv")):
        try:
            df = pd.read_csv(p, dtype=str)
            result.append({
                "name": p.stem,
                "stock_count": len(df),
                "created_at": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
        except Exception:
            continue
    return result


def query_pool(name: str) -> list[dict] | None:
    """查询概念池中的股票列表。不存在返回 None。"""
    p = _path(name)
    if not p.exists():
        return None
    try:
        df = pd.read_csv(p, dtype=str)
        return df.to_dict("records")
    except Exception:
        return None


# ---------------------------------------------------------------- 写操作

def create_pool(name: str, *, description: str = "") -> dict:
    """新建概念池（空池）。同名已存在则不覆盖。"""
    name = name.strip()
    if not name:
        raise ValueError("概念池名称不能为空")
    p = _path(name)
    if p.exists():
        return {"name": name, "status": "exists", "stock_count": 0}
    pd.DataFrame(columns=_COLUMNS).to_csv(p, index=False, encoding="utf-8-sig")
    return {"name": name, "status": "created", "stock_count": 0}


def delete_pool(name: str) -> dict:
    """删除概念池。"""
    name = name.strip()
    if not name:
        raise ValueError("概念池名称不能为空")
    p = _path(name)
    if not p.exists():
        return {"name": name, "status": "not_found"}
    p.unlink(missing_ok=True)
    return {"name": name, "status": "deleted"}


def add_stocks(name: str, stocks: list[dict]) -> dict:
    """向概念池添加股票。

    stocks: [{code, name, note?}]
    返回 {added: int, skipped: int}。
    """
    name = name.strip()
    if not name:
        raise ValueError("概念池名称不能为空")
    p = _path(name)
    today = datetime.today().strftime("%Y%m%d")

    # 读现有
    existing = set()
    if p.exists():
        try:
            df = pd.read_csv(p, dtype=str)
            existing = set(df["code"].astype(str).str.zfill(6))
        except Exception:
            df = pd.DataFrame(columns=_COLUMNS)
    else:
        df = pd.DataFrame(columns=_COLUMNS)

    added = 0
    skipped = 0
    new_rows = []
    for s in stocks:
        code = str(s.get("code", "")).strip().zfill(6)
        if not code or code in existing:
            skipped += 1
            continue
        new_rows.append({
            "code": code,
            "name": str(s.get("name", "")).strip(),
            "added_date": today,
            "note": str(s.get("note", "")).strip(),
        })
        existing.add(code)
        added += 1

    if new_rows:
        df_new = pd.DataFrame(new_rows, columns=_COLUMNS)
        df = pd.concat([df, df_new], ignore_index=True)
        df.to_csv(p, index=False, encoding="utf-8-sig")

    return {"name": name, "added": added, "skipped": skipped, "total": len(df)}


def remove_stocks(name: str, codes: list[str]) -> dict:
    """从概念池移除股票。"""
    name = name.strip()
    if not name:
        raise ValueError("概念池名称不能为空")
    p = _path(name)
    if not p.exists():
        return {"name": name, "status": "not_found", "removed": 0}

    codes_set = {c.strip().zfill(6) for c in codes if c.strip()}
    df = pd.read_csv(p, dtype=str)
    before = len(df)
    df = df[~df["code"].astype(str).str.zfill(6).isin(codes_set)]
    removed = before - len(df)
    df.to_csv(p, index=False, encoding="utf-8-sig")
    return {"name": name, "removed": removed, "remaining": len(df)}


# ---------------------------------------------------------------- 知识库同步

def sync_to_knowledge() -> list[str]:
    """将概念池同步为 knowledge/概念池/*.md（供 KnowledgeIndex 自动收录）。

    返回生成的 .md 文件路径列表。
    """
    knowledge_dir = get_settings().project_root / "knowledge" / "概念池"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    generated = []

    for pool in list_pools():
        stocks = query_pool(pool["name"])
        if not stocks:
            continue
        lines = [
            f"# 概念池：{pool['name']}\n",
            f"> 创建时间：{pool.get('created_at', '')}  |  股票数量：{len(stocks)}\n",
            "\n## 成分股\n",
            "| 代码 | 名称 | 添加日期 | 备注 |",
            "|------|------|----------|------|",
        ]
        for s in stocks:
            lines.append(
                f"| {s.get('code', '')} | {s.get('name', '')} | "
                f"{s.get('added_date', '')} | {s.get('note', '')} |"
            )

        lines.append(f"\n共 {len(stocks)} 只股票。\n")
        out_path = knowledge_dir / f"{pool['name']}.md"
        out_path.write_text("\n".join(lines), encoding="utf-8")
        generated.append(str(out_path))

    return generated