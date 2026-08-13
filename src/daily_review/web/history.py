"""历史报告服务：从 output/ 读回已生成的复盘/隔夜预案/开盘策略，供 Web 多日复盘。

背景（v0.16）：复盘报告每次生成已落盘 output/{date}_复盘.md，但 Web 只查进程内存
的 JobState.report_html，重启后旧报告"看不到"。本服务照搬数据看板的文件读回模式
（output/{date}_看板.html 复用），扫描 output/ 目录识别历史产物并按日渲染回前端。

- list_reports()：扫描 output/ 下 _复盘.md / _隔夜预案.md / _开盘策略.md / _看板.html，
  按日期倒序返回「哪天有哪些产物」
- load_report(date)：读回该日复盘 md → md_to_html 全文 + section_html 提取次日预案，
  返回结构与 JobState.to_dict() 兼容（report_html / plan_html），前端 showResult 零改动复用
- 只读 output/，不修改任何生成逻辑；日期正则 + resolve 防路径穿越
"""

from __future__ import annotations

import re
from pathlib import Path

from daily_review.config import get_settings
from daily_review.web.md import md_to_html, section_html

_DATE_RE = re.compile(r"^\d{8}$")
# output 历史产物文件命名：{YYYYMMDD}_{类型}.{ext}
_FILE_RE = re.compile(r"^(\d{8})_(复盘|隔夜预案|开盘策略|看板)\.(md|html)$")

_ARTIFACT_KEY = {
    "复盘": "has_review",
    "隔夜预案": "has_overnight",
    "开盘策略": "has_open",
    "看板": "has_dashboard",
}

# 复盘章节标题（与 prompts/modules/次日预案.md 的文档章节一致，jobs._run_review 同款提取）
_PLAN_SECTION_TITLE = "七、次日预案"


def _output_dir() -> Path:
    """output/ 目录（报告产物落盘处）。"""
    return get_settings().output_dir


def list_reports() -> list[dict]:
    """扫描 output/ 返回历史日期列表（倒序：最新在前）。

    每条：{date, has_review, has_overnight, has_open, has_dashboard}
    无任何产物的日期不出现；某类产物缺失对应字段 False。
    """
    d = _output_dir()
    if not d.exists():
        return []
    by_date: dict[str, dict] = {}
    for p in sorted(d.glob("*")):
        m = _FILE_RE.match(p.name)
        if not m:
            continue
        date, artifact, _ext = m.groups()
        row = by_date.setdefault(
            date,
            {"date": date, "has_review": False, "has_overnight": False,
             "has_open": False, "has_dashboard": False},
        )
        row[_ARTIFACT_KEY[artifact]] = True
    return [by_date[k] for k in sorted(by_date, reverse=True)]


def load_report(date: str) -> dict | None:
    """读回 {date} 的复盘报告并渲染为 HTML（与 JobState.to_dict 兼容）。

    返回 {trade_date, report_html, plan_html}；该日无复盘 md → None。
    """
    date = date.strip()
    if not _DATE_RE.fullmatch(date):
        raise ValueError(f"日期格式错误（需 YYYYMMDD）: {date}")
    d = _output_dir().resolve()
    path = (d / f"{date}_复盘.md").resolve()
    # 防路径穿越：解析后必须仍在 output/ 内
    if not path.is_relative_to(d) or not path.is_file():
        return None
    md_text = path.read_text(encoding="utf-8")
    plan_html, _ = section_html(md_text, _PLAN_SECTION_TITLE)
    return {
        "trade_date": date,
        "report_html": md_to_html(md_text),
        "plan_html": plan_html,
    }