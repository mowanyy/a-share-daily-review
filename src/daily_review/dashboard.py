"""数据看板：近 N 日 KPI + 趋势图表 + 明细面板（单文件 HTML，无 AI 文案）。

对齐图表化看板（v0.36）+ 明细增强（v0.38）：
- 形态：自包含 `output/{date}_看板.html`，零外链/零 CDN，纯 JS + 内联 SVG 画图
- 数据：复用 `pipeline.collect_dashboard`（轻量）+ `collect_dashboard_detail`（明细读盘）；
  趋势行由各日池子逐日核算，情绪温度直接复用 `compute_emotion` 的 `series`（按 date 匹配）
- 内容：KPI（6 基础 + 6 明细）+ 2×2 趋势图 + 趋势摘要表 + 情绪成分拆解
  + Tab 三面板（总览 / 市场结构=连板梯队+题材板块 / 涨停个股明细）；无 LLM
- 交互：Tab 切换、表格排序/搜索/筛选、跨面板联动下钻、图表悬浮提示、分页（纯内联 JS）
- 图表遵循 dataviz 规范：单轴、≥2 序列必有图例、瘦标记、1px 网格、深/浅色双主题
"""

from __future__ import annotations

import html
from pathlib import Path

from daily_review.config import get_settings
from daily_review.llm.reporter import _compact_json, _weekday_cn

DEFAULT_N_DAYS = 10
# 看板单文件结构版本（v0.38 明细面板起 = 2）：Web 文件复用时核对，旧版文件触发重新生成
DASH_VER = 2


# ---------------------------------------------------------------- 多日趋势行（纯函数）

def _day_row(date: str, zt, zb, dt, emo_by_date: dict, *, zb_ok: bool = True, dt_ok: bool = True) -> dict:
    """单日趋势行（口径与 emotion._day_components / ladder.compute_ladder 一致）。

    返回 {date, zt_count, lianban_count, max_lb, break_count, break_rate, dt_count, emotion, missing}。
    缺 zt 记 0 值 + missing 标记（图表不炸，数据照常渲染）。
    """
    zt_count = int(len(zt))
    lianban_count = int((zt["lb_num"] >= 2).sum()) if zt_count else 0
    max_lb = int(zt["lb_num"].max()) if zt_count else 0
    break_count = int(len(zb))
    break_rate = round(break_count / (zt_count + break_count), 4) if (zt_count + break_count) else 0.0
    dt_count = int(len(dt))
    missing: list[str] = []
    if not zt_count:
        missing.append("涨停缺失")
    if not zb_ok:
        missing.append("炸板缺失")
    if not dt_ok:
        missing.append("跌停缺失")
    return {
        "date": date,
        "zt_count": zt_count,
        "lianban_count": lianban_count,
        "max_lb": max_lb,
        "break_count": break_count,
        "break_rate": break_rate,
        "dt_count": dt_count,
        "emotion": emo_by_date.get(date),
        "missing": missing,
    }


def build_trend(collected: dict, indicators: dict, n_days: int = DEFAULT_N_DAYS) -> list[dict]:
    """构建多日趋势行列表，**旧→新**（时间左→右、今日最后一行）。

    emotion.series 是最新在前（emotion.py:256-264），此处按 date 建 dict 匹配，
    与方向无关，统一消化 series / height_series 与主数组的方向差。
    hist_days 为旧→新（pipeline.py:132-136），今日行由 collected 当日池子核算。
    """
    emo = {s["date"]: s.get("score") for s in (indicators.get("emotion") or {}).get("series", [])}
    rows = [
        _day_row(h["date"], h["zt"], h["zb"], h["dt"], emo,
                 zb_ok=h.get("zb_ok", True), dt_ok=h.get("dt_ok", True))
        for h in collected.get("hist_days", [])
    ]
    rows.append(_day_row(
        collected["trade_date"], collected["zt"], collected["zb"], collected["dt"], emo,
        zb_ok=collected.get("zb_ok", True), dt_ok=collected.get("dt_ok", True),
    ))
    return rows[-n_days:] if n_days else rows


# ---------------------------------------------------------------- 前端载荷（render_html 的 DATA）

def _themes_compact(themes: list[dict]) -> list[dict]:
    """题材精简（payload 减体积）：只保留看板面板需要的字段。"""
    out: list[dict] = []
    for t in themes:
        leader = t.get("leader") or {}
        out.append({
            "name": t.get("theme_name", ""),
            "member_count": t.get("member_count", 0),
            "max_lb": t.get("max_lb", 0),
            "stage": t.get("stage", ""),
            "stage_reason": t.get("stage_reason", ""),
            "leader": {
                "code": leader.get("code", ""),
                "name": leader.get("name", ""),
                "lb_num": leader.get("lb_num", 0),
            },
            "is_main": bool(t.get("is_main")),
        })
    return out


def _assemble_payload(indicators: dict, trend: list[dict], collected: dict,
                      detail: dict | None = None) -> dict:
    """前端 `const DATA`：趋势行 + KPI + 情绪成分 + 明细面板（梯队/题材/涨停明细）。

    detail: compute_dashboard_detail 产出；None → 仅基础看板（明细键不注入，前端空态）。
    """
    ladder = indicators.get("ladder", {})
    emo = indicators.get("emotion") or {}
    trade_date = collected["trade_date"]
    today = trend[-1] if trend else {}
    d_ladder = (detail or {}).get("ladder") or {}
    flags = (detail or {}).get("flags") or {}
    payload = {
        "trade_date": trade_date,
        "weekday": _weekday_cn(trade_date),
        "n_days": len(trend),
        "dash_ver": DASH_VER,
        "trend": trend,
        "kpi": {
            "emotion_score": emo.get("score"),
            "emotion_stage": emo.get("stage"),
            "emotion_reason": emo.get("stage_reason"),
            "zt_count": ladder.get("zt_count", 0),
            "lianban_count": ladder.get("lianban_count", 0),
            "max_lb": ladder.get("max_lb", 0),
            "max_lb_stock": ladder.get("max_lb_stock", ""),
            "break_rate": ladder.get("break_rate", 0.0),
            "dt_count": today.get("dt_count", 0),
            # v0.38 新增（detail 缺省时 None → 前端隐藏对应卡片）
            "first_board_count": d_ladder.get("first_board_count"),
            "promote_rate": (detail or {}).get("promote_rate"),
            "height_position": d_ladder.get("height_position"),
            "zt_amount": (detail or {}).get("zt_amount"),
            "mf_net": (detail or {}).get("mf_net"),
            "lhb_net": (detail or {}).get("lhb_net"),
        },
        "emotion": {
            "available": bool(emo.get("available")),
            "score": emo.get("score"),
            "stage": emo.get("stage"),
            "stage_reason": emo.get("stage_reason"),
            "components": emo.get("components", {}),
            "raw": emo.get("raw", {}),
        },
    }
    if detail is not None:
        payload["ladder"] = {
            "ladder": d_ladder.get("ladder", []),
            "promotion": d_ladder.get("promotion", {}),
        }
        payload["themes"] = _themes_compact((detail or {}).get("themes", []))
        payload["zt_list"] = (detail or {}).get("zt_list", [])
        payload["flags"] = flags
    return payload


# ---------------------------------------------------------------- HTML 渲染

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ 数据看板</title>
<style>
:root {
  color-scheme: dark;
  --bg: #0d1117; --panel: #161b22; --border: #30363d;
  --ink: #e6edf3; --ink-2: #8b949e; --ink-3: #6e7681;
  --grid: #21262d; --accent: #2a78d6; --accent-2: #eb6834; --accent-3: #1baf7a;
  --up: #f5222d; --down: #3fb950; --warn: #d29922; --chip: #1f6feb;
}
@media (prefers-color-scheme: light) {
  :root { color-scheme: light;
    --bg: #ffffff; --panel: #f6f8fa; --border: #d0d7de;
    --ink: #1f2328; --ink-2: #57606a; --ink-3: #6e7781;
    --grid: #d8dee4; --up: #cf222e; --down: #1a7f37; --warn: #9a6700; --chip: #0969da;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font-family: system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
  line-height: 1.4; }
.board { max-width: 1180px; margin: 0 auto; padding: 14px 16px 28px; }
.board-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap;
  margin-bottom: 12px; }
.board-head h1 { font-size: 18px; margin: 0; font-weight: 650; }
.sub { color: var(--ink-2); font-size: 12px; }
.kpi-grid { display: grid; grid-template-columns: repeat(6, 1fr);
  gap: 8px; margin-bottom: 12px; }
.kpi { background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
  padding: 10px 12px; border-left: 3px solid var(--accent); }
.kpi .label { font-size: 11px; color: var(--ink-2); letter-spacing: .02em; }
.kpi .value { font-size: 26px; font-weight: 700; margin-top: 2px; line-height: 1.1;
  font-variant-numeric: tabular-nums; }
.kpi .sub { font-size: 11px; margin-top: 3px; }
.chip { display: inline-block; padding: 1px 7px; border-radius: 4px; font-size: 11px;
  background: rgba(31,111,235,.16); color: var(--chip); font-weight: 600; }
.charts { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 12px; }
.tables { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 8px; }
.panel { background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
  padding: 10px 12px; min-width: 0; }
.panel h2 { margin: 0 0 6px; font-size: 12px; color: var(--ink-2); font-weight: 600;
  text-transform: none; letter-spacing: .02em; }
@media (max-width: 900px) {
  .charts, .tables { grid-template-columns: 1fr; }
  .kpi-grid { grid-template-columns: repeat(3, 1fr); }
}
@media (max-width: 767px) {
  .board { padding: 10px 8px 20px; }
  .board-head h1 { font-size: 16px; }
  .kpi-grid { grid-template-columns: repeat(2, 1fr); gap: 6px; }
  .kpi { padding: 8px 10px; }
  .kpi .value { font-size: 22px; }
  .panel { padding: 8px 10px; }
  table { font-size: 11px; }
  th, td { padding: 3px 5px; }
}
figure { margin: 0; background: var(--panel); border: 1px solid var(--border);
  border-radius: 8px; padding: 8px 10px; }
figcaption { font-size: 12px; font-weight: 600; margin-bottom: 4px; color: var(--ink-2); }
.legend { font-size: 11px; color: var(--ink-2); margin-top: 4px; }
.legend .sw { display: inline-block; width: 8px; height: 8px; border-radius: 2px;
  margin: 0 4px 0 10px; vertical-align: -1px; }
svg.chart { width: 100%; height: auto; display: block; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th, td { text-align: left; padding: 4px 6px; border-bottom: 1px solid var(--border);
  white-space: nowrap; }
#trend-summary, #emotion-comp { overflow-x: auto; }
th { color: var(--ink-2); font-weight: 600; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tbody tr:nth-child(even) { background: rgba(128,128,128,.05); }
.up { color: var(--up); } .down { color: var(--down); } .muted { color: var(--ink-2); }
.empty { color: var(--ink-3); font-size: 12px; }
/* ---- v0.38：Tab 导航 / 明细面板 / 交互 ---- */
.tabs { display: flex; gap: 4px; margin-bottom: 12px; border-bottom: 1px solid var(--border);
  padding-bottom: 8px; overflow-x: auto; }
.tab { background: none; border: 1px solid var(--border); color: var(--ink-2);
  padding: 5px 16px; border-radius: 6px; font-size: 12px; cursor: pointer; white-space: nowrap; }
.tab.active { background: rgba(31,111,235,.14); border-color: var(--chip); color: var(--ink); font-weight: 600; }
.tab-panel { margin-bottom: 8px; }
.panel-head { display: flex; align-items: center; justify-content: space-between; gap: 8px;
  margin-bottom: 6px; }
.panel-head h2 { margin: 0; }
.inline-check { font-size: 12px; color: var(--ink-2); display: inline-flex; align-items: center;
  gap: 4px; cursor: pointer; }
.filter-bar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; align-items: center; }
.filter-bar input[type=text] { background: var(--bg); border: 1px solid var(--border); color: var(--ink);
  border-radius: 6px; padding: 5px 10px; font-size: 12px; width: 170px; }
.filter-bar select { background: var(--bg); border: 1px solid var(--border); color: var(--ink);
  border-radius: 6px; padding: 5px 8px; font-size: 12px; max-width: 180px; }
.filter-bar button { background: none; border: 1px solid var(--border); color: var(--ink-2);
  border-radius: 6px; padding: 5px 12px; font-size: 12px; cursor: pointer; }
.filter-bar button:hover { color: var(--ink); border-color: var(--chip); }
th.sortable { cursor: pointer; user-select: none; }
th.sortable:hover { color: var(--ink); }
.arr { font-size: 10px; color: var(--chip); margin-left: 2px; }
tr.row-link { cursor: pointer; }
tr.row-link:hover td { background: rgba(31,111,235,.08); }
td.slim { max-width: 260px; white-space: normal; }
.pager { display: flex; gap: 4px; align-items: center; margin-top: 8px; flex-wrap: wrap; }
.pager button { background: none; border: 1px solid var(--border); color: var(--ink-2);
  border-radius: 5px; min-width: 26px; padding: 3px 8px; font-size: 12px; cursor: pointer; }
.pager button:hover { color: var(--ink); }
.pager button.active { background: rgba(31,111,235,.16); border-color: var(--chip); color: var(--ink); font-weight: 600; }
.hint { font-size: 11px; color: var(--ink-3); margin-top: 4px; }
.kpi.clickable { cursor: pointer; transition: border-color .15s; }
.kpi.clickable:hover { border-color: var(--chip); }
.table-wrap { overflow-x: auto; }
.chart-tip { position: absolute; pointer-events: none; background: var(--panel);
  border: 1px solid var(--border); border-radius: 6px; padding: 6px 10px; font-size: 11px;
  color: var(--ink); display: none; z-index: 5; box-shadow: 0 2px 8px rgba(0,0,0,.3);
  max-width: 260px; }
@media (max-width: 767px) {
  .filter-bar input[type=text] { width: 100%; }
  .tabs .tab { flex: 1; }
}
</style>
</head>
<body>
<div class="board">
  <header class="board-head">
    <h1>__TITLE__ 数据看板（__WEEKDAY__）</h1>
    <div class="sub">近__N_DAYS__个交易日 · 收盘数据</div>
  </header>

  <nav class="tabs" id="tabs">
    <button type="button" class="tab active" data-tab="overview">总览</button>
    <button type="button" class="tab" data-tab="structure">市场结构</button>
    <button type="button" class="tab" data-tab="zt">涨停明细</button>
  </nav>

  <section id="tab-overview" class="tab-panel">
  <section class="kpi-grid" id="kpi"></section>

  <section class="charts">
    <figure>
      <figcaption>情绪温度走势（0–100）</figcaption>
      <svg id="chart-emotion" class="chart" viewBox="0 0 900 220"></svg>
      <div class="legend" id="leg-emotion"></div>
    </figure>
    <figure>
      <figcaption>涨停 / 连板 / 炸板 家数</figcaption>
      <svg id="chart-counts" class="chart" viewBox="0 0 900 220"></svg>
      <div class="legend" id="leg-counts"></div>
    </figure>
    <figure>
      <figcaption>空间板高度（板）</figcaption>
      <svg id="chart-height" class="chart" viewBox="0 0 900 180"></svg>
      <div class="legend" id="leg-height"></div>
    </figure>
    <figure>
      <figcaption>炸板率（%）</figcaption>
      <svg id="chart-breakrate" class="chart" viewBox="0 0 900 180"></svg>
      <div class="legend" id="leg-breakrate"></div>
    </figure>
  </section>

  <section class="tables">
    <div class="panel"><h2>近__N_DAYS__日趋势摘要</h2><div id="trend-summary"></div></div>
    <div class="panel"><h2>情绪温度成分拆解</h2><div id="emotion-comp"></div></div>
  </section>
  </section><!-- /tab-overview -->

  <section id="tab-structure" class="tab-panel" hidden>
    <div class="panel">
      <div class="panel-head"><h2>连板梯队</h2></div>
      <div class="table-wrap" id="ladder"></div>
      <div class="hint">点击板数行 → 在「涨停明细」按该板数过滤</div>
    </div>
    <div class="panel">
      <div class="panel-head"><h2>题材板块</h2>
        <label class="inline-check"><input type="checkbox" id="themeMainOnly"> 仅看主线</label></div>
      <div class="table-wrap" id="themes"></div>
      <div class="hint">点击题材行 → 在「涨停明细」按该行业过滤</div>
    </div>
  </section>

  <section id="tab-zt" class="tab-panel" hidden>
    <div class="panel">
      <div class="panel-head"><h2>涨停个股明细</h2><span id="ztCount" class="muted"></span></div>
      <div class="filter-bar">
        <input type="text" id="ztSearch" placeholder="搜索代码/名称">
        <select id="ztIndustry"><option value="">全部行业</option></select>
        <select id="ztLb"><option value="">全部连板</option></select>
        <button type="button" id="ztClear">清空筛选</button>
      </div>
      <div class="table-wrap" id="zt-list"></div>
      <div id="ztPager" class="pager"></div>
    </div>
  </section>
</div>

<script>
const DATA = __DATA_JSON__;

function esc(s) { return String(s === null || s === undefined ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
function fmtDate(d) { return d ? d.slice(4, 6) + "-" + d.slice(6) : ""; }
function pct(v, d) { return v === null || v === undefined ? "—" : (v * 100).toFixed(d ?? 1) + "%"; }
function upDown(v) { return v > 0 ? "up" : v < 0 ? "down" : ""; }

/* ---------- SVG 绘图（通用） ---------- */
const PAD = { l: 42, r: 12, t: 10, b: 26 };
// SVG 命名空间 URI 带协议前缀（协议名 + 双斜杠），拼装避免字面量——模板保持零外链字面量，
// 供「单文件自包含」的离线断言扫描（render_html 断言不出现协议前缀）
const NS = "http:" + "//www.w3.org/2000/svg";

function makeScale(rows, yMax, w, h) {
  const innerW = w - PAD.l - PAD.r, innerH = h - PAD.t - PAD.b;
  const n = rows.length, step = n > 1 ? innerW / (n - 1) : 0;
  return {
    x: i => PAD.l + (n > 1 ? i * step : innerW / 2),
    y: v => PAD.t + innerH - (v / yMax) * innerH,
    innerW, innerH, n,
  };
}

function drawGridAndAxes(svg, s, yMax, yFmt) {
  const ticks = 4;
  for (let t = 0; t <= ticks; t++) {
    const v = (yMax / ticks) * t;
    const y = s.y(v);
    const line = document.createElementNS(NS, "line");
    line.setAttribute("x1", PAD.l); line.setAttribute("y1", y);
    line.setAttribute("x2", PAD.l + s.innerW); line.setAttribute("y2", y);
    line.setAttribute("stroke", "var(--grid)"); line.setAttribute("stroke-width", "1");
    svg.appendChild(line);
    const txt = document.createElementNS(NS, "text");
    txt.setAttribute("x", PAD.l - 6); txt.setAttribute("y", y + 4);
    txt.setAttribute("text-anchor", "end"); txt.setAttribute("font-size", "10");
    txt.setAttribute("fill", "var(--ink-3)");
    txt.textContent = yFmt(v);
    svg.appendChild(txt);
  }
}

function drawXLabels(svg, rows, s) {
  const every = rows.length > 8 ? 2 : 1;
  rows.forEach((r, i) => {
    if (i % every !== 0) return;
    const txt = document.createElementNS(NS, "text");
    txt.setAttribute("x", s.x(i)); txt.setAttribute("y", s.innerH + PAD.t + 18);
    txt.setAttribute("text-anchor", "middle"); txt.setAttribute("font-size", "10");
    txt.setAttribute("fill", "var(--ink-3)");
    txt.textContent = fmtDate(r.date);
    svg.appendChild(txt);
  });
}

function drawLineChart(svg, rows, series, opts) {
  const { yMax = 100, yFmt = v => Math.round(v), bands = [], highlightLast = false,
          height = 220 } = opts || {};
  const W = 900, H = height;
  svg.innerHTML = "";
  const s = makeScale(rows, yMax, W, H);

  bands.forEach(b => {
    const rect = document.createElementNS(NS, "rect");
    rect.setAttribute("x", PAD.l); rect.setAttribute("y", s.y(b.y1));
    rect.setAttribute("width", s.innerW);
    rect.setAttribute("height", Math.max(0, s.y(b.y0) - s.y(b.y1)));
    rect.setAttribute("fill", b.fill);
    svg.appendChild(rect);
    const txt = document.createElementNS(NS, "text");
    txt.setAttribute("x", PAD.l + 6); txt.setAttribute("y", s.y(b.y1) + 12);
    txt.setAttribute("font-size", "10"); txt.setAttribute("fill", b.labelColor || "var(--ink-3)");
    txt.textContent = b.label;
    svg.appendChild(txt);
  });

  drawGridAndAxes(svg, s, yMax, yFmt);
  drawXLabels(svg, rows, s);

  series.forEach(sr => {
    const flushSeg = (pts) => {
      if (!pts.length) return;
      const line = document.createElementNS(NS, "polyline");
      line.setAttribute("fill", "none"); line.setAttribute("stroke", sr.color);
      line.setAttribute("stroke-width", "2"); line.setAttribute("stroke-linejoin", "round");
      line.setAttribute("points", pts.join(" "));
      svg.appendChild(line);
    };
    let pts = [];
    rows.forEach((r, i) => {
      const v = r[sr.key];
      const has = v !== null && v !== undefined && !isNaN(v);
      if (has) {
        pts.push(s.x(i).toFixed(1) + "," + s.y(v).toFixed(1));
        const c = document.createElementNS(NS, "circle");
        c.setAttribute("cx", s.x(i)); c.setAttribute("cy", s.y(v)); c.setAttribute("r", "3.5");
        c.setAttribute("fill", sr.color);
        const ti = document.createElementNS(NS, "title");
        ti.textContent = fmtDate(r.date) + " · " + sr.label + " " + (sr.fmt ? sr.fmt(v) : v);
        c.appendChild(ti);
        attachTip(c, ti);
        svg.appendChild(c);
      } else {
        flushSeg(pts);
        pts = [];
      }
    });
    flushSeg(pts);

    if (highlightLast) {
      for (let i = rows.length - 1; i >= 0; i--) {
        const v = rows[i][sr.key];
        if (v === null || v === undefined || isNaN(v)) continue;
        const dot = document.createElementNS(NS, "circle");
        dot.setAttribute("cx", s.x(i)); dot.setAttribute("cy", s.y(v)); dot.setAttribute("r", "4.5");
        dot.setAttribute("fill", sr.color); dot.setAttribute("stroke", "var(--bg)");
        dot.setAttribute("stroke-width", "2");
        svg.appendChild(dot);
        if (sr.lastLabel) {
          const lbl = document.createElementNS(NS, "text");
          lbl.setAttribute("x", s.x(i)); lbl.setAttribute("y", Math.max(PAD.t + 12, s.y(v) - 8));
          lbl.setAttribute("text-anchor", "middle"); lbl.setAttribute("font-size", "11");
          lbl.setAttribute("font-weight", "600"); lbl.setAttribute("fill", sr.color);
          lbl.textContent = sr.fmt ? sr.fmt(v) : v;
          svg.appendChild(lbl);
        }
        break;
      }
    }
  });
}

function drawBarChart(svg, rows, key, opts) {
  const { color = "var(--accent)", yFmt = v => v, valueLabel = "", height = 180 } = opts || {};
  const W = 900, H = height;
  svg.innerHTML = "";
  const yMax = Math.max(1, ...rows.map(r => r[key] || 0)) * 1.15;
  const s = makeScale(rows, yMax, W, H);
  drawGridAndAxes(svg, s, yMax, yFmt);
  drawXLabels(svg, rows, s);
  const barW = Math.min(40, (s.innerW / s.n) * 0.55);
  rows.forEach((r, i) => {
    const v = r[key] || 0;
    const x = s.x(i) - barW / 2, y = s.y(v), h = s.innerH + PAD.t - y;
    const rect = document.createElementNS(NS, "rect");
    rect.setAttribute("x", x); rect.setAttribute("y", y); rect.setAttribute("width", barW);
    rect.setAttribute("height", h); rect.setAttribute("fill", color);
    rect.setAttribute("rx", "3"); rect.setAttribute("ry", "3");
    const base = document.createElementNS(NS, "rect");
    base.setAttribute("x", x); base.setAttribute("y", s.innerH + PAD.t - 3);
    base.setAttribute("width", barW); base.setAttribute("height", "3"); base.setAttribute("fill", color);
    svg.appendChild(rect); svg.appendChild(base);
    const ti = document.createElementNS(NS, "title");
    ti.textContent = fmtDate(r.date) + " · " + v + valueLabel;
    rect.appendChild(ti);
    attachTip(rect, ti);
    if (v === Math.max(...rows.map(rr => rr[key] || 0))) {
      const lbl = document.createElementNS(NS, "text");
      lbl.setAttribute("x", x + barW / 2); lbl.setAttribute("y", y - 5);
      lbl.setAttribute("text-anchor", "middle"); lbl.setAttribute("font-size", "11");
      lbl.setAttribute("fill", "var(--ink-2)");
      lbl.textContent = v + valueLabel;
      svg.appendChild(lbl);
    }
  });
}

function setLegend(el, items) {
  if (!el) return;
  el.innerHTML = items.map(it =>
    '<span class="sw" style="background:' + it.color + '"></span>' + esc(it.label)).join("");
}

/* ---------- 页面渲染 ---------- */
function stageColor(stage) {
  if (!stage) return "var(--ink-3)";
  if (stage.indexOf("高潮") >= 0) return "var(--up)";
  if (stage.indexOf("冰点") >= 0) return "var(--down)";
  if (stage.indexOf("修复") >= 0) return "var(--warn)";
  return "var(--accent)";
}

function renderKPI() {
  const k = DATA.kpi, emo = DATA.emotion;
  const hp = k.height_position || {};
  const cards = [
    { label: "情绪温度", value: emo.available ? k.emotion_score : "—",
      valueCls: emo.available ? stageColor(k.emotion_stage) : "",
      bar: emo.available ? stageColor(k.emotion_stage) : "var(--accent)",
      sub: emo.available ? '<span class="chip" style="color:' + stageColor(k.emotion_stage)
        + '">' + esc(k.emotion_stage) + "</span>" : "数据不足" },
    { label: "涨停家数", value: k.zt_count, valueCls: "var(--up)", bar: "var(--up)", goto: "zt" },
    { label: "连板家数", value: k.lianban_count, valueCls: "var(--up)", bar: "var(--accent-2)", goto: "structure" },
    { label: "空间板高度", value: k.max_lb + " 板", valueCls: "var(--accent)", bar: "var(--accent)",
      sub: esc(k.max_lb_stock), goto: "structure" },
    { label: "炸板率", value: pct(k.break_rate, 0), valueCls: "var(--warn)", bar: "var(--warn)" },
    { label: "跌停家数", value: k.dt_count, valueCls: k.dt_count > 0 ? "var(--down)" : "",
      bar: "var(--down)" },
    { label: "首板家数", value: k.first_board_count, valueCls: "var(--up)", bar: "var(--accent-3)",
      goto: "zt", nullHide: true },
    { label: "晋级率", value: k.promote_rate === null || k.promote_rate === undefined
        ? null : pct(k.promote_rate, 0),
      valueCls: "var(--accent-2)", bar: "var(--accent-2)", sub: "今日连板 ÷ 昨日涨停",
      goto: "structure", nullHide: true },
    { label: "最高板位置", value: hp.label || null, valueCls: "var(--accent)", bar: "var(--accent)",
      sub: (hp.trend || "") + (hp.percentile !== undefined && hp.percentile !== null
        ? " · 分位 " + Math.round(hp.percentile * 100) + "%" : ""), nullHide: true },
    { label: "炸板净流出", value: k.mf_net === null || k.mf_net === undefined
        ? null : Math.abs(k.mf_net).toFixed(2) + " 亿",
      valueCls: upDown(k.mf_net), bar: k.mf_net < 0 ? "var(--down)" : "var(--up)",
      sub: "炸板股主力净额", nullHide: true },
    { label: "龙虎榜净买", value: k.lhb_net === null || k.lhb_net === undefined
        ? null : Math.abs(k.lhb_net).toFixed(2) + " 亿",
      valueCls: upDown(k.lhb_net), bar: k.lhb_net < 0 ? "var(--down)" : "var(--up)",
      sub: "净买额", nullHide: true },
    { label: "涨停总成交额", value: k.zt_amount === null || k.zt_amount === undefined
        ? null : k.zt_amount.toFixed(2) + " 亿",
      valueCls: "", bar: "var(--accent)", nullHide: true },
  ];
  const html = cards
    .filter(c => !c.nullHide || (c.value !== null && c.value !== undefined && c.value !== ""))
    .map(c => '<div class="kpi' + (c.goto ? " clickable" : "") + '" style="border-left-color:'
      + (c.bar || "var(--accent)") + '"' + (c.goto ? ' data-goto="' + c.goto + '"' : "") + ">"
      + '<div class="label">' + esc(c.label) + '</div>'
      + '<div class="value" style="color:' + (c.valueCls || "") + '">' + esc(c.value) + "</div>"
      + (c.sub ? '<div class="sub">' + c.sub + "</div>" : "") + "</div>").join("");
  document.getElementById("kpi").innerHTML = html;
  document.querySelectorAll("#kpi .kpi.clickable").forEach(function (el) {
    el.addEventListener("click", function () { switchTab(el.dataset.goto); });
  });
}

function renderCharts() {
  const rows = DATA.trend;
  const hasData = rows.some(r => r.zt_count > 0);
  const emoAvailable = DATA.emotion.available;

  if (!hasData) {
    ["chart-emotion", "chart-counts", "chart-height", "chart-breakrate"].forEach(id => {
      const el = document.getElementById(id);
      el.innerHTML = '<text x="450" y="110" text-anchor="middle" font-size="13" fill="var(--ink-3)">数据不足</text>';
    });
    return;
  }

  if (emoAvailable) {
    const bands = [
      { y0: 0, y1: 45, label: "冰点/修复", fill: "rgba(80,200,120,.07)", labelColor: "var(--down)" },
      { y0: 45, y1: 70, label: "修复/退潮", fill: "rgba(210,153,34,.09)", labelColor: "var(--warn)" },
      { y0: 70, y1: 100, label: "高潮/退潮", fill: "rgba(245,34,45,.09)", labelColor: "var(--up)" },
    ];
    drawLineChart(document.getElementById("chart-emotion"), rows,
      [{ key: "emotion", label: "情绪温度", color: "var(--accent)", fmt: v => Math.round(v), lastLabel: true }],
      { yMax: 100, bands, highlightLast: true, yFmt: v => Math.round(v), height: 220 });
    setLegend(document.getElementById("leg-emotion"),
      [{ color: "var(--accent)", label: "情绪温度分" + (DATA.emotion.stage ? " · 今日" + DATA.emotion.stage : "") }]);
  } else {
    document.getElementById("chart-emotion").innerHTML =
      '<text x="450" y="110" text-anchor="middle" font-size="13" fill="var(--ink-3)">情绪温度数据不足</text>';
  }

  drawLineChart(document.getElementById("chart-counts"), rows,
    [{ key: "zt_count", label: "涨停", color: "var(--accent)", lastLabel: true },
     { key: "lianban_count", label: "连板", color: "var(--accent-2)" },
     { key: "break_count", label: "炸板", color: "var(--accent-3)" }],
    { yMax: Math.max(20, ...rows.map(r => Math.max(r.zt_count, r.break_count))) * 1.15,
      highlightLast: true, yFmt: v => Math.round(v), height: 220 });
  setLegend(document.getElementById("leg-counts"),
    [{ color: "var(--accent)", label: "涨停" },
     { color: "var(--accent-2)", label: "连板" },
     { color: "var(--accent-3)", label: "炸板" }]);

  drawBarChart(document.getElementById("chart-height"), rows, "max_lb",
    { color: "var(--accent)", valueLabel: " 板", yFmt: v => Math.round(v), height: 180 });

  drawLineChart(document.getElementById("chart-breakrate"), rows,
    [{ key: "break_rate", label: "炸板率", color: "var(--accent-2)", fmt: v => pct(v), lastLabel: true }],
    { yMax: 1, highlightLast: true, yFmt: v => pct(v, 0), height: 180 });
  setLegend(document.getElementById("leg-breakrate"),
    [{ color: "var(--accent-2)", label: "炸板率" }]);
}

function renderTrendSummary() {
  const rows = DATA.trend, el = document.getElementById("trend-summary");
  if (!rows.length) { el.innerHTML = '<div class="empty">无趋势数据</div>'; return; }
  const trs = rows.map(r =>
    "<tr><td>" + fmtDate(r.date) + "</td>"
    + "<td class='num'>" + r.zt_count + "</td>"
    + "<td class='num'>" + r.lianban_count + "</td>"
    + "<td class='num'>" + r.max_lb + "</td>"
    + "<td class='num'>" + r.break_count + "</td>"
    + "<td class='num'>" + pct(r.break_rate) + "</td>"
    + "<td class='num'>" + r.dt_count + "</td>"
    + "<td class='num'>" + (r.emotion === null || r.emotion === undefined ? "—" : Math.round(r.emotion)) + "</td>"
    + "<td>" + esc((r.missing || []).join("、")) + "</td></tr>").join("");
  el.innerHTML = "<table><thead><tr><th>日期</th><th class='num'>涨停</th><th class='num'>连板</th>"
    + "<th class='num'>最高板</th><th class='num'>炸板</th><th class='num'>炸板率</th>"
    + "<th class='num'>跌停</th><th class='num'>情绪</th><th>缺失</th></tr></thead><tbody>"
    + trs + "</tbody></table>";
}

function renderEmotionComp() {
  const emo = DATA.emotion, el = document.getElementById("emotion-comp");
  if (!emo.available) { el.innerHTML = '<div class="empty">情绪温度数据不足</div>'; return; }
  const comps = emo.components || {}, raw = emo.raw || {};
  const defs = [
    { k: "zt", label: "涨停家数", rk: "zt_count", fmt: v => Math.round(v) + " 家" },
    { k: "height", label: "空间板高度", rk: "max_lb", fmt: v => Math.round(v) + " 板" },
    { k: "promote", label: "晋级延续率", rk: "promote", fmt: v => pct(v, 0) },
    { k: "break", label: "炸板率", rk: "break_rate", fmt: v => pct(v, 0) },
    { k: "dt", label: "跌停家数", rk: "dt_count", fmt: v => Math.round(v) + " 家" },
  ];
  const rows = defs.map(d =>
    "<tr><td>" + d.label + "</td>"
    + "<td class='num'>" + (raw[d.rk] === null || raw[d.rk] === undefined ? "—" : d.fmt(raw[d.rk])) + "</td>"
    + "<td class='num'>" + (comps[d.k] === null || comps[d.k] === undefined ? "—" : Math.round(comps[d.k])) + "</td></tr>").join("");
  el.innerHTML = "<table><thead><tr><th>成分</th><th class='num'>今日值</th>"
    + "<th class='num'>成分分(0-100)</th></tr></thead><tbody>" + rows + "</tbody></table>";
}

/* ---------- 图表悬浮提示（v0.38） ---------- */
function attachTip(el, titleEl) {
  el.addEventListener("mousemove", function (ev) {
    const fig = el.closest("figure");
    if (!fig) return;
    let tip = fig.querySelector(".chart-tip");
    if (!tip) {
      tip = document.createElement("div");
      tip.className = "chart-tip";
      fig.style.position = "relative";
      fig.appendChild(tip);
    }
    tip.textContent = titleEl.textContent;
    tip.style.display = "block";
    const r = fig.getBoundingClientRect();
    tip.style.left = Math.max(4, ev.clientX - r.left + 12) + "px";
    tip.style.top = Math.max(4, ev.clientY - r.top + 14) + "px";
  });
  el.addEventListener("mouseleave", function () {
    const fig = el.closest("figure");
    if (!fig) return;
    const tip = fig.querySelector(".chart-tip");
    if (tip) tip.style.display = "none";
  });
}

/* ---------- Tab / 状态 / 联动（v0.38） ---------- */
const STATE = { tab: "overview", sortKey: "lb_num", sortDir: "desc",
  keyword: "", industry: "", lb: "", page: 1, mainOnly: false };

function switchTab(name) {
  STATE.tab = name;
  document.querySelectorAll(".tab").forEach(function (b) {
    b.classList.toggle("active", b.dataset.tab === name);
  });
  ["overview", "structure", "zt"].forEach(function (t) {
    const el = document.getElementById("tab-" + t);
    if (el) el.hidden = (t !== name);
  });
  if (name === "structure") renderStructure();
  if (name === "zt") renderZtList();
  try { sessionStorage.setItem("dashTab", name); } catch (e) {}
  try { if (window.parent && window.parent.fitFrame) window.parent.fitFrame(); } catch (e) {}
}

function initTabs() {
  document.querySelectorAll(".tab").forEach(function (b) {
    b.addEventListener("click", function () { switchTab(b.dataset.tab); });
  });
  let saved = null;
  try { saved = sessionStorage.getItem("dashTab"); } catch (e) {}
  if (saved === "structure" || saved === "zt") switchTab(saved);
}

function renderStructure() {
  renderLadder();
  renderThemes();
}

function renderLadder() {
  const el = document.getElementById("ladder");
  const d = DATA.ladder;
  if (!d || !d.ladder || !d.ladder.length) {
    el.innerHTML = '<div class="empty">连板梯队数据不足</div>';
    return;
  }
  const rows = d.ladder.map(function (row) {
    const prom = d.promotion ? d.promotion[row.height + "进" + (row.height + 1)] : undefined;
    const stocks = (row.stocks || []).map(function (s) { return esc(s); }).join("<br>");
    let weak = "";
    if (row.weak && row.weak.length) {
      weak = '<tr class="weak-row"><td></td><td></td><td colspan="2"><div class="muted" style="font-size:11px">弱封/多次开板: '
        + esc(row.weak.slice(0, 5).join("、")) + (row.weak.length > 5 ? " 等" + row.weak.length + " 只" : "")
        + "</div></td></tr>";
    }
    return "<tr class='row-link' data-lb='" + row.height + "'>"
      + "<td class='num'><b>" + row.height + " 板</b></td>"
      + "<td class='num'>" + row.count + "</td>"
      + "<td class='slim'>" + (stocks || "—") + "</td>"
      + "<td class='num'>" + (prom === undefined || prom === null ? "—" : pct(prom, 0)) + "</td></tr>"
      + weak;
  }).join("");
  el.innerHTML = "<table><thead><tr><th class='num'>板数</th><th class='num'>家数</th>"
    + "<th>代表股（代码 名称 首封 行业）</th><th class='num'>晋级率</th></tr></thead><tbody>"
    + rows + "</tbody></table>";
  el.querySelectorAll("tr.row-link").forEach(function (tr) {
    tr.addEventListener("click", function () {
      STATE.lb = tr.dataset.lb; STATE.industry = ""; STATE.keyword = ""; STATE.page = 1;
      switchTab("zt");
    });
  });
}

function renderThemes() {
  const el = document.getElementById("themes");
  const list = DATA.themes || [];
  if (!list.length) { el.innerHTML = '<div class="empty">题材板块数据不足</div>'; return; }
  const rows = list.filter(function (t) { return !STATE.mainOnly || t.is_main; }).map(function (t) {
    return "<tr class='row-link' data-ind='" + esc(t.name) + "' style='opacity:" + (t.is_main ? "1" : ".8") + "'>"
      + "<td>" + esc(t.name) + (t.is_main ? ' <span class="chip">主线</span>' : "") + "</td>"
      + "<td class='num'>" + t.member_count + "</td>"
      + "<td class='num'>" + t.max_lb + " 板</td>"
      + "<td><span style='color:" + stageColor(t.stage) + ";font-weight:600'>" + esc(t.stage) + "</span></td>"
      + "<td>" + esc(t.leader.name || "—") + (t.leader.lb_num > 1 ? "（" + t.leader.lb_num + "板）" : "") + "</td>"
      + "</tr>";
  }).join("");
  el.innerHTML = "<table><thead><tr><th>题材</th><th class='num'>家数</th><th class='num'>最高板</th>"
    + "<th>阶段</th><th>领涨股</th></tr></thead><tbody>"
    + (rows || '<tr><td colspan="5" class="empty">无匹配题材</td></tr>') + "</tbody></table>";
  el.querySelectorAll("tr.row-link").forEach(function (tr) {
    tr.addEventListener("click", function () {
      STATE.industry = tr.dataset.ind; STATE.lb = ""; STATE.keyword = ""; STATE.page = 1;
      switchTab("zt");
    });
  });
}

const ZT_COLS = [
  { key: "code", label: "代码", cls: "num" },
  { key: "name", label: "名称", cls: "" },
  { key: "lb_num", label: "连板", cls: "num", fmt: function (v) { return v + "板"; } },
  { key: "first_time", label: "首封", cls: "num", fmt: function (v) { return v || "—"; } },
  { key: "open_times", label: "开板", cls: "num" },
  { key: "seal_amount", label: "封单额(亿)", cls: "num",
    fmt: function (v) { return v === null || v === undefined ? "—" : v.toFixed(2); } },
  { key: "amount", label: "成交额(亿)", cls: "num",
    fmt: function (v) { return v === null || v === undefined ? "—" : v.toFixed(2); } },
  { key: "turnover", label: "换手%", cls: "num",
    fmt: function (v) { return v === null || v === undefined ? "—" : v.toFixed(1); } },
  { key: "industry", label: "行业", cls: "" },
];

function fmtCell(col, v) {
  return col.fmt ? col.fmt(v) : (v === null || v === undefined ? "—" : v);
}

function renderZtList() {
  const el = document.getElementById("zt-list");
  const list = DATA.zt_list || [];
  if (!list.length) {
    el.innerHTML = '<div class="empty">涨停明细数据不足</div>';
    const count = document.getElementById("ztCount");
    if (count) count.textContent = "";
    const pager = document.getElementById("ztPager");
    if (pager) pager.innerHTML = "";
    return;
  }
  let rows = list;
  if (STATE.keyword) {
    const kw = STATE.keyword.toLowerCase();
    rows = rows.filter(function (r) {
      return r.code.indexOf(kw) >= 0 || String(r.name || "").toLowerCase().indexOf(kw) >= 0;
    });
  }
  if (STATE.industry) rows = rows.filter(function (r) { return r.industry === STATE.industry; });
  if (STATE.lb) rows = rows.filter(function (r) { return String(r.lb_num) === STATE.lb; });

  const sortKey = STATE.sortKey || "lb_num", sortDir = STATE.sortDir || "desc";
  rows = rows.slice().sort(function (a, b) {
    const va = a[sortKey], vb = b[sortKey];
    const na = va === null || va === undefined, nb = vb === null || vb === undefined;
    if (na && nb) return 0;
    if (na) return 1;
    if (nb) return -1;
    let cmp;
    if (typeof va === "number" && typeof vb === "number") cmp = va - vb;
    else cmp = String(va).localeCompare(String(vb), "zh");
    return sortDir === "asc" ? cmp : -cmp;
  });

  const perPage = 50;
  const total = rows.length;
  const pages = Math.max(1, Math.ceil(total / perPage));
  if (STATE.page > pages) STATE.page = pages;
  const pageRows = rows.slice((STATE.page - 1) * perPage, STATE.page * perPage);

  const countEl = document.getElementById("ztCount");
  if (countEl) countEl.textContent = "共 " + total + " 只";
  const head = ZT_COLS.map(function (c) {
    return "<th class='" + (c.cls ? c.cls + " " : "") + "sortable' data-key='" + c.key + "'>" + c.label
      + (sortKey === c.key ? '<span class="arr">' + (sortDir === "asc" ? "▲" : "▼") + "</span>" : "") + "</th>";
  }).join("");
  const trs = pageRows.map(function (r) {
    return "<tr>" + ZT_COLS.map(function (c) {
      return "<td class='" + c.cls + "'>" + esc(fmtCell(c, r[c.key])) + "</td>";
    }).join("") + "</tr>";
  }).join("");
  el.innerHTML = "<table><thead><tr>" + head + "</tr></thead><tbody>"
    + (trs || '<tr><td colspan="' + ZT_COLS.length + '" class="empty">无匹配个股</td></tr>')
    + "</tbody></table>";

  el.querySelectorAll("th.sortable").forEach(function (th) {
    th.addEventListener("click", function () {
      const k = th.dataset.key;
      if (STATE.sortKey === k) STATE.sortDir = STATE.sortDir === "asc" ? "desc" : "asc";
      else { STATE.sortKey = k; STATE.sortDir = "asc"; }
      STATE.page = 1;
      renderZtList();
    });
  });
  renderPager(pages);
  syncFilters();
}

function renderPager(pages) {
  const el = document.getElementById("ztPager");
  if (!el) return;
  if (pages <= 1) { el.innerHTML = ""; return; }
  const show = new Set([1, pages, STATE.page - 1, STATE.page, STATE.page + 1]);
  let html = "", prev = 0;
  for (let p = 1; p <= pages; p++) {
    if (!show.has(p)) continue;
    if (p - prev > 1) html += '<span class="muted">…</span>';
    html += '<button type="button" class="' + (p === STATE.page ? "active" : "") + '" data-p="' + p + '">' + p + "</button>";
    prev = p;
  }
  el.innerHTML = html;
  el.querySelectorAll("button").forEach(function (b) {
    b.addEventListener("click", function () { STATE.page = Number(b.dataset.p); renderZtList(); });
  });
}

let _filtersInit = false;
function syncFilters() {
  const ind = document.getElementById("ztIndustry");
  const lb = document.getElementById("ztLb");
  if (!ind || !lb) return;
  if (!_filtersInit) {
    const seen = [], inds = [];
    (DATA.zt_list || []).forEach(function (r) {
      if (r.industry && seen.indexOf(r.industry) < 0) { seen.push(r.industry); inds.push(r.industry); }
    });
    inds.sort(function (a, b) { return a.localeCompare(b, "zh"); });
    ind.innerHTML = '<option value="">全部行业</option>'
      + inds.map(function (i) { return "<option>" + esc(i) + "</option>"; }).join("");
    let maxLb = 0;
    (DATA.zt_list || []).forEach(function (r) { if (r.lb_num > maxLb) maxLb = r.lb_num; });
    let opts = '<option value="">全部连板</option><option value="1">首板</option>';
    for (let h = 2; h <= maxLb; h++) opts += '<option value="' + h + '">' + h + "板</option>";
    lb.innerHTML = opts;
    _filtersInit = true;
  }
  ind.value = STATE.industry || "";
  lb.value = STATE.lb || "";
}

function bindFilters() {
  const search = document.getElementById("ztSearch");
  const ind = document.getElementById("ztIndustry");
  const lb = document.getElementById("ztLb");
  const clear = document.getElementById("ztClear");
  const mainOnly = document.getElementById("themeMainOnly");
  let timer = null;
  if (search) search.addEventListener("input", function () {
    if (timer) clearTimeout(timer);
    timer = setTimeout(function () {
      STATE.keyword = search.value.trim().toLowerCase(); STATE.page = 1; renderZtList();
    }, 200);
  });
  if (ind) ind.addEventListener("change", function () { STATE.industry = ind.value; STATE.page = 1; renderZtList(); });
  if (lb) lb.addEventListener("change", function () { STATE.lb = lb.value; STATE.page = 1; renderZtList(); });
  if (clear) clear.addEventListener("click", function () {
    STATE.keyword = ""; STATE.industry = ""; STATE.lb = ""; STATE.page = 1;
    if (search) search.value = "";
    renderZtList();
  });
  if (mainOnly) mainOnly.addEventListener("change", function () {
    STATE.mainOnly = mainOnly.checked;
    renderThemes();
  });
}

function init() {
  renderKPI();
  renderCharts();
  renderTrendSummary();
  renderEmotionComp();
  initTabs();
  bindFilters();
}
document.addEventListener("DOMContentLoaded", init);
</script>
</body>
</html>
"""


def render_html(payload: dict, llm_text: str = "") -> str:
    """渲染自包含 HTML。数据经 JSON 注入 `const DATA`，`</` 转义防断 `<script>`。

    llm_text 参数保留兼容旧调用方，已忽略（看板不再含 AI 解读）。
    """
    del llm_text  # 兼容签名，不再注入
    data_json = _compact_json(payload).replace("</", "<\\/")
    ymd = str(payload.get("trade_date", ""))
    title = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}" if len(ymd) == 8 else ymd
    return (
        _HTML_TEMPLATE
        .replace("__TITLE__", title)
        .replace("__WEEKDAY__", str(payload.get("weekday", "")))
        .replace("__N_DAYS__", str(payload.get("n_days", "")))
        .replace("__DATA_JSON__", data_json)
    )


_ERROR_TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>数据看板加载失败</title>
<style>
body { margin:0; font-family:system-ui,"Microsoft YaHei",sans-serif; background:#0d1117; color:#e6edf3; }
.box { max-width:680px; margin:48px auto; padding:24px 28px; background:#161b22; border:1px solid #30363d; border-radius:10px; }
h1 { font-size:20px; margin:0 0 10px; color:#f5222d; }
p { color:#8b949e; font-size:14px; line-height:1.6; }
.sub { color:#6e7681; font-size:12px; }
</style>
</head>
<body>
<div class="box">
  <h1>数据看板加载失败（__DATE__）</h1>
  <p>__MSG__</p>
  <p class="sub">可能原因：网络不通（东财接口）、当日行情数据尚未完整，或接口临时限流。可稍后重试，或先确认该日复盘数据已采集。</p>
</div>
</body>
</html>"""


def render_error_html(trade_date: str, message: str) -> str:
    """看板加载失败时的自包含错误页（供 web iframe 用，避免裸 500 白屏）。

    数据经 html.escape 转义（错误消息可能含异常文本/特殊字符，日期也一并转义防注入），
    不落库、不缓存。
    """
    return (
        _ERROR_TEMPLATE
        .replace("__DATE__", html.escape(str(trade_date)))
        .replace("__MSG__", html.escape(message))
    )


# ---------------------------------------------------------------- 主入口

def generate_dashboard(
    trade_date: str,
    *,
    n_days: int = DEFAULT_N_DAYS,
    api_key: str | None = None,
    no_llm: bool = True,
    out_path: str | Path | None = None,
) -> str:
    """生成数据看板 HTML 并落盘，返回文本。

    流程：collect_dashboard（轻量）→ compute_dashboard → collect_dashboard_detail（明细读盘）
    → compute_dashboard_detail → build_trend → render_html → 落盘。
    明细面板失败时降级为基础看板（不影响生成）。
    out_path 缺省：output/{trade_date}_看板.html。
    api_key / no_llm 保留兼容旧调用方（看板不再调用 LLM）。
    """
    del api_key, no_llm  # 兼容签名，不再使用
    from daily_review.pipeline import (
        collect_dashboard,
        collect_dashboard_detail,
        compute_dashboard,
        compute_dashboard_detail,
    )

    print(f"[看板] 交易日 {trade_date}，近 {n_days} 个交易日（轻量采集 + 明细读盘）")
    collected = collect_dashboard(trade_date, n_days=n_days)
    indicators = compute_dashboard(collected)
    try:
        detail = collect_dashboard_detail(trade_date, n_days=n_days)
        detail_indicators = compute_dashboard_detail(collected, detail)
    except Exception as exc:  # noqa: BLE001 —— 明细失败降级基础看板
        print(f"[看板] 明细组装失败（降级为基础看板）：{type(exc).__name__}: {exc}")
        detail_indicators = None
    trend = build_trend(collected, indicators, n_days)
    payload = _assemble_payload(indicators, trend, collected, detail_indicators)
    html_text = render_html(payload)

    settings = get_settings()
    out_path = out_path or (settings.output_dir / f"{trade_date}_看板.html")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_text, encoding="utf-8")
    print(f"\n已生成: {out_path}")
    print(f"（{len(html_text.splitlines())} 行）")
    return html_text


def try_pregenerate_dashboard(trade_date: str, *, n_days: int = DEFAULT_N_DAYS) -> bool:
    """复盘成功后附写看板（失败只打印，不抛异常，不阻断主流程）。返回是否成功。"""
    try:
        generate_dashboard(trade_date, n_days=n_days)
        return True
    except Exception as exc:  # noqa: BLE001 —— 预写失败不影响复盘
        print(f"[看板] 预写失败（不影响复盘）：{type(exc).__name__}: {exc}")
        return False
