"""题材运行周期与归类（对齐 docs/数据结构.md 的 Theme）。

把当日涨停股按行业归类，结合近 5 日各行业成员数与高度序列判定
运行阶段（启动/发酵/高潮/退潮）与龙头。
"""

from __future__ import annotations

import pandas as pd


def _pick_leader(members: pd.DataFrame) -> dict:
    """龙头：身位最高（lb_num 最大）→ 封板最早。"""
    if members.empty:
        return {}
    max_lb = members["lb_num"].max()
    top = members[members["lb_num"] == max_lb].sort_values("first_limit_time")
    r = top.iloc[0]
    return {
        "code": str(r["code"]),
        "name": str(r["name"]),
        "lb_num": int(r["lb_num"]),
        "first_limit_time": r["first_limit_time"] or "",
    }


def _judge_stage(name: str, counts: list[int], max_lbs: list[int]) -> tuple[str, str]:
    """阶段判定 + 依据句。

    counts / max_lbs 为近 N 日（含今日，旧→新）各行业成员数与最高身位。
    """
    today = counts[-1]
    prev_counts = counts[:-1]
    prev_max = max_lbs[:-1]
    series = "→".join(str(c) for c in counts)

    if today < 3:
        stage = "启动"
    elif prev_counts and today >= max(prev_counts) and (not prev_max or max_lbs[-1] >= max(prev_max)):
        stage = "高潮"
    elif prev_counts and today < prev_counts[-1]:
        stage = "退潮"
    elif prev_counts and today > prev_counts[-1]:
        stage = "发酵"
    else:
        stage = "发酵"

    reason = f"{name}：连续 {len(counts)} 日 {series} 家，高度 {max_lbs[-1]} 板，判定{stage}"
    return stage, reason


def build_themes(
    zt: pd.DataFrame,
    prev_pools: list[tuple[str, pd.DataFrame]],
    concept_map: dict[str, list[str]] | None = None,
) -> list[dict]:
    """题材归类。prev_pools: (date, 当日涨停池) 按旧→新排列（不含今日）。"""
    concept_map = concept_map or {}
    if zt.empty:
        return []

    # 今日按行业分组
    groups: dict[str, pd.DataFrame] = {k: g for k, g in zt.groupby("industry") if k}
    # 历史各行业成员数 / 最高身位序列（按旧→新，补今日）
    hist_counts: dict[str, list[int]] = {k: [] for k in groups}
    hist_max: dict[str, list[int]] = {k: [] for k in groups}
    for _, prev in prev_pools:
        for k in groups:
            sub = prev[prev["industry"] == k]
            hist_counts[k].append(len(sub))
            hist_max[k].append(int(sub["lb_num"].max()) if not sub.empty else 0)

    themes: list[dict] = []
    for name, g in groups.items():
        g = g.copy()
        g["concepts"] = g["code"].map(lambda c: concept_map.get(str(c), []))
        member_count = len(g)
        max_lb = int(g["lb_num"].max())
        counts = hist_counts[name] + [member_count]
        max_lbs = hist_max[name] + [max_lb]
        stage, reason = _judge_stage(name, counts, max_lbs)

        members = [
            {
                "code": str(r["code"]),
                "name": str(r["name"]),
                "lb_num": int(r["lb_num"]),
                "first_limit_time": r["first_limit_time"] or "",
                "concepts": r["concepts"],
            }
            for _, r in g.sort_values(["lb_num", "first_limit_time"], ascending=[False, True]).iterrows()
        ]
        leader = _pick_leader(g)
        assists = [
            {"code": m["code"], "name": m["name"], "lb_num": m["lb_num"]}
            for m in members if m["code"] != leader.get("code")
        ][:3]
        # 各高度分布
        heights: dict[str, int] = {}
        for h, cnt in g["lb_num"].value_counts().sort_index(ascending=False).items():
            heights[f"{int(h)}板"] = int(cnt)
        # 题材概念标签（成员概念并集，最多 5 个）
        theme_concepts: list[str] = []
        for m in members:
            for c in m["concepts"]:
                if c and c not in theme_concepts:
                    theme_concepts.append(c)
                    if len(theme_concepts) >= 5:
                        break
            if len(theme_concepts) >= 5:
                break

        themes.append({
            "theme_name": name,
            "member_count": member_count,
            "max_lb": max_lb,
            "stage": stage,
            "stage_reason": reason,
            "leader": leader,
            "assists": assists,
            "members": members,
            "heights": heights,
            "concepts": theme_concepts,
            "prev_member_counts": counts,
            "prev_max_lb_series": max_lbs,
        })

    # 主线：家数最多 或 高度最高的 1–2 个
    themes.sort(key=lambda t: (t["member_count"], t["max_lb"]), reverse=True)
    main_count = themes[0]["member_count"] if themes else 0
    main_height = themes[0]["max_lb"] if themes else 0
    for t in themes[:2]:
        t["is_main"] = True
    for t in themes[2:]:
        t["is_main"] = (t["member_count"] >= main_count or t["max_lb"] >= main_height) and t["member_count"] >= 4

    return themes
