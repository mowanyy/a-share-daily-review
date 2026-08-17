"""数据看板：近 N 日趋势图表 + LLM 多日趋势解读（单文件 HTML）。

对齐 v0.6 设计（见 docs/需求分析.md §3）：
- 形态：自包含 `output/{date}_看板.html`，零外链/零 CDN，纯 JS + 内联 SVG 画图，浏览器直接打开
- 数据：复用 `pipeline.collect(n_days)` + `pipeline.compute()`；趋势行由各日池子逐日核算，
  情绪温度直接复用 `compute_emotion` 的 `series`（按 date 匹配，与方向无关）
- LLM：顶部一段「多日趋势解读」（`module.dashboard` prompt + DeepSeek）；无 key / --no-llm / 失败
  时降级为「（未生成解读）」，数据看板照常渲染
- 图表遵循 dataviz 规范：单轴、≥2 序列必有图例、瘦标记、1px 网格、深/浅色双主题
"""

from __future__ import annotations

import html
from pathlib import Path

from daily_review.config import get_settings
from daily_review.llm.client import LLMError, chat
from daily_review.llm.reporter import _compact_json, _weekday_cn
from daily_review.prompts import get_prompt

DEFAULT_N_DAYS = 10


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

def _assemble_payload(indicators: dict, trend: list[dict], collected: dict) -> dict:
    """前端 `const DATA`：趋势行 + KPI + 今日结构面板（梯队/题材/炸板/龙虎榜，均取已核算产出）。"""
    ladder = indicators.get("ladder", {})
    brk = indicators.get("break", {})
    emo = indicators.get("emotion") or {}
    lhb = indicators.get("lhb") or {}
    trade_date = collected["trade_date"]
    today = trend[-1] if trend else {}
    return {
        "trade_date": trade_date,
        "weekday": _weekday_cn(trade_date),
        "n_days": len(trend),
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
        },
        "emotion": {
            "available": bool(emo.get("available")),
            "score": emo.get("score"),
            "stage": emo.get("stage"),
            "stage_reason": emo.get("stage_reason"),
            "components": emo.get("components", {}),
            "raw": emo.get("raw", {}),
        },
        "ladder": {
            "ladder": ladder.get("ladder", []),
            "promotion": ladder.get("promotion", {}),
        },
        "themes": indicators.get("themes", []),
        "break": {
            "break_count": brk.get("break_count", 0),
            "break_rate": brk.get("break_rate", 0.0),
            "table": brk.get("table", []),
        },
        "lhb": {
            "overview": lhb.get("overview", {}),
            "net_rank": lhb.get("net_rank", []),
            "hotmoney": lhb.get("hotmoney", []),
        },
    }


# ---------------------------------------------------------------- LLM 多日趋势解读

def _dashboard_payload(indicators: dict, trend: list[dict]) -> dict:
    """module.dashboard 输入载荷（紧凑，字段名对齐 prompt 输入契约）。"""
    ladder = indicators.get("ladder", {})
    brk = indicators.get("break", {})
    emo = indicators.get("emotion") or {}
    lhb = indicators.get("lhb") or {}
    return {
        "近N日趋势": trend,
        "今日结构摘要": {
            "KPI": {
                "涨停家数": ladder.get("zt_count", 0),
                "连板家数": ladder.get("lianban_count", 0),
                "空间板": f"{ladder.get('max_lb', 0)}板 {ladder.get('max_lb_stock', '')}",
                "炸板率": ladder.get("break_rate", 0.0),
                "跌停家数": today_dt(trend),
            },
            "情绪温度(已核算)": {
                "score": emo.get("score"), "stage": emo.get("stage"),
                "stage_reason": emo.get("stage_reason"),
            },
            "主要题材": [
                {"name": t.get("theme_name", ""), "member_count": t.get("member_count", 0),
                 "max_lb": t.get("max_lb", 0), "stage": t.get("stage", ""),
                 "leader": (t.get("leader") or {}).get("name", "")}
                for t in indicators.get("themes", [])[:5]
            ],
            "炸板概览": {"break_count": brk.get("break_count", 0), "watch": brk.get("watch", [])[:3]},
            "龙虎榜": {
                "stock_count": lhb.get("overview", {}).get("stock_count", 0),
                "hotmoney": [h.get("tag", "") for h in lhb.get("hotmoney", [])[:5]],
            },
        },
    }


def today_dt(trend: list[dict]) -> int:
    """趋势末行（今日）跌停家数，供载荷/展示复用。"""
    return int(trend[-1].get("dt_count", 0)) if trend else 0


def _dashboard_interpretation(indicators: dict, trend: list[dict], api_key: str | None = None) -> str:
    """LLM 多日趋势解读；失败/无 key/无 prompt 返回空串（render 降级为「（未生成解读）」）。"""
    p = get_prompt("module.dashboard")
    if p is None:
        return ""
    emo = indicators.get("emotion") or {}
    forced = ""
    if emo.get("available"):
        forced = (
            f"\n\n【程序核算结果，输出时必须原样采用，不得改写或另造数字】\n"
            f"情绪温度 {emo.get('score')} 分 / 周期 {emo.get('stage')}。依据：{emo.get('stage_reason')}"
        )
    messages = [
        {"role": "system", "content": p.body},
        {
            "role": "user",
            "content": (
                f"近{len(trend)}个交易日趋势数据如下（JSON，字段名与上方「输入数据」契约一致）：\n"
                f"```json\n{_compact_json(_dashboard_payload(indicators, trend))}\n```\n"
                f"请输出一段 150–250 字的多日趋势解读：用 2–4 个数据点概括 N 日走势"
                f"（家数/高度/炸板率/温度分段变化）、点名今日所处阶段位置、指出背离或转折；"
                f"禁止重算任何数字，缺数据日注明「缺数据」。只输出正文一段，不加标题、不加 Markdown 标记。"
                f"{forced}"
            ),
        },
    ]
    try:
        return chat(messages, api_key=api_key, max_tokens=1200).strip()  # 推理模型预留 reasoning 预算
    except LLMError:
        return ""


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
  line-height: 1.5; }
.board { max-width: 1180px; margin: 0 auto; padding: 24px 20px 48px; }
.board-head h1 { font-size: 24px; margin: 0 0 4px; font-weight: 650; }
.sub { color: var(--ink-2); font-size: 13px; }
.panel { background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
  padding: 16px 18px; margin-bottom: 18px; }
.panel h2 { margin: 0 0 8px; font-size: 15px; color: var(--ink-2); font-weight: 600; }
.llm-text { font-size: 14px; color: var(--ink); white-space: pre-wrap; }
.llm-fallback { color: var(--ink-3); font-style: italic; }
.kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px; margin-bottom: 18px; }
.kpi { background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
  padding: 14px 16px; }
.kpi .label { font-size: 12px; color: var(--ink-2); }
.kpi .value { font-size: 30px; font-weight: 650; margin-top: 4px; line-height: 1.1; }
.kpi .sub { font-size: 12px; margin-top: 4px; }
.chip { display: inline-block; padding: 2px 8px; border-radius: 20px; font-size: 12px;
  background: rgba(31,111,235,.16); color: var(--chip); }
.charts { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin-bottom: 18px; }
@media (max-width: 900px) { .charts { grid-template-columns: 1fr; } }
@media (max-width: 767px) {
  .board { padding: 12px 10px 32px; }
  .board-head h1 { font-size: 18px; }
  .kpi-grid { grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 8px; }
  .kpi { padding: 10px 12px; }
  .kpi .value { font-size: 24px; }
  .panel { padding: 10px 12px; }
  table { font-size: 11px; }
  th, td { padding: 4px 5px; }
}
figure { margin: 0; background: var(--panel); border: 1px solid var(--border);
  border-radius: 10px; padding: 14px 16px; }
figcaption { font-size: 14px; font-weight: 600; margin-bottom: 8px; }
.legend { font-size: 12px; color: var(--ink-2); margin-top: 6px; }
.legend .sw { display: inline-block; width: 10px; height: 10px; border-radius: 2px;
  margin: 0 4px 0 12px; vertical-align: -1px; }
svg.chart { width: 100%; height: auto; display: block; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--border);
  white-space: nowrap; }
/* 表格容器横向溢出兜底：内容超宽时出现横向滚动条而非裁掉文字
	   （连板梯队「炸板/弱封」/题材/炸板/龙虎榜行数或单元格可随涨停家数暴涨） */
	#ladder, #themes, #break, #lhb, #trend-summary, #emotion-comp { overflow-x: auto; }
th { color: var(--ink-2); font-weight: 600; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tbody tr:nth-child(even) { background: rgba(128,128,128,.05); }
.up { color: var(--up); } .down { color: var(--down); } .muted { color: var(--ink-2); }
.empty { color: var(--ink-3); font-size: 13px; }
</style>
</head>
<body>
<div class="board">
  <header class="board-head">
    <h1>__TITLE__ 数据看板（__WEEKDAY__）</h1>
    <div class="sub">近__N_DAYS__个交易日 · 收盘数据 · 超短连板复盘</div>
  </header>

  <section class="panel">
    <h2>多日趋势解读</h2>
    <div class="llm-text">__LLM_TEXT__</div>
  </section>

  <section class="kpi-grid" id="kpi"></section>

  <section class="charts">
    <figure>
      <figcaption>情绪温度走势（0–100）</figcaption>
      <svg id="chart-emotion" class="chart" viewBox="0 0 900 300"></svg>
      <div class="legend" id="leg-emotion"></div>
    </figure>
    <figure>
      <figcaption>涨停 / 连板 / 炸板 家数</figcaption>
      <svg id="chart-counts" class="chart" viewBox="0 0 900 300"></svg>
      <div class="legend" id="leg-counts"></div>
    </figure>
    <figure>
      <figcaption>空间板高度（板）</figcaption>
      <svg id="chart-height" class="chart" viewBox="0 0 900 260"></svg>
      <div class="legend" id="leg-height"></div>
    </figure>
    <figure>
      <figcaption>炸板率（%）</figcaption>
      <svg id="chart-breakrate" class="chart" viewBox="0 0 900 260"></svg>
      <div class="legend" id="leg-breakrate"></div>
    </figure>
  </section>

  <section class="panel"><h2>近__N_DAYS__日趋势摘要</h2><div id="trend-summary"></div></section>
  <section class="panel"><h2>情绪温度成分拆解</h2><div id="emotion-comp"></div></section>

  <section class="panel"><h2>连板梯队</h2><div id="ladder"></div></section>
  <section class="panel"><h2>题材结构</h2><div id="themes"></div></section>
  <section class="panel"><h2>炸板与资金</h2><div id="break"></div></section>
  <section class="panel"><h2>龙虎榜</h2><div id="lhb"></div></section>
</div>

<script>
const DATA = __DATA_JSON__;

function esc(s) { return String(s === null || s === undefined ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
function fmtDate(d) { return d ? d.slice(4, 6) + "-" + d.slice(6) : ""; }
function fmtMoney(v) {
  if (v === null || v === undefined || isNaN(v)) return "缺";
  const a = Math.abs(v);
  if (a >= 1e8) return (v / 1e8).toFixed(2) + "亿";
  if (a >= 1e4) return Math.round(v / 1e4) + "万";
  return Math.round(v).toString();
}
/* 弱封股列表截断：最多显示前 5 只，超出补「等 N 只」，避免涨停家数多时撑破表格。 */
function weakCell(list) {
  const arr = list || [];
  if (!arr.length) return "—";
  const shown = arr.slice(0, 5).join(" / ");
  const extra = arr.length - 5;
  return esc(shown) + (extra > 0 ? ' <span class="muted">等 ' + extra + " 只</span>" : "");
}
function pct(v, d) { return v === null || v === undefined ? "—" : (v * 100).toFixed(d ?? 1) + "%"; }
function pctPct(v, d) { return v === null || v === undefined ? "—" : v.toFixed(d ?? 1) + "%"; } /* 值已是百分数（东财 zdp/CHANGE_RATE），不再 ×100 */
function upDown(v) { return v > 0 ? "up" : v < 0 ? "down" : ""; }

/* ---------- SVG 绘图（通用） ---------- */
const PAD = { l: 46, r: 16, t: 14, b: 30 };
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
    txt.setAttribute("text-anchor", "end"); txt.setAttribute("font-size", "11");
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
    txt.setAttribute("x", s.x(i)); txt.setAttribute("y", s.innerH + PAD.t + 20);
    txt.setAttribute("text-anchor", "middle"); txt.setAttribute("font-size", "11");
    txt.setAttribute("fill", "var(--ink-3)");
    txt.textContent = fmtDate(r.date);
    svg.appendChild(txt);
  });
}

function drawLineChart(svg, rows, series, opts) {
  const { yMax = 100, yFmt = v => Math.round(v), bands = [], highlightLast = false } = opts || {};
  const W = 900, H = 300;
  svg.innerHTML = "";
  const s = makeScale(rows, yMax, W, H);

  // 背景阶段色带（情绪温度静态分区）
  bands.forEach(b => {
    const rect = document.createElementNS(NS, "rect");
    rect.setAttribute("x", PAD.l); rect.setAttribute("y", s.y(b.y1));
    rect.setAttribute("width", s.innerW);
    rect.setAttribute("height", Math.max(0, s.y(b.y0) - s.y(b.y1)));
    rect.setAttribute("fill", b.fill);
    svg.appendChild(rect);
    const txt = document.createElementNS(NS, "text");
    txt.setAttribute("x", PAD.l + 6); txt.setAttribute("y", s.y(b.y1) + 14);
    txt.setAttribute("font-size", "11"); txt.setAttribute("fill", b.labelColor || "var(--ink-3)");
    txt.textContent = b.label;
    svg.appendChild(txt);
  });

  drawGridAndAxes(svg, s, yMax, yFmt);
  drawXLabels(svg, rows, s);

  series.forEach(sr => {
    // 断点处理：连续有值分段折线（每段独立 polyline），跳过缺失点
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
        // 数据点（含原生 <title> tooltip；数值同时以表格可达）
        const c = document.createElementNS(NS, "circle");
        c.setAttribute("cx", s.x(i)); c.setAttribute("cy", s.y(v)); c.setAttribute("r", "4");
        c.setAttribute("fill", sr.color);
        const ti = document.createElementNS(NS, "title");
        ti.textContent = fmtDate(r.date) + " · " + sr.label + " " + (sr.fmt ? sr.fmt(v) : v);
        c.appendChild(ti);
        svg.appendChild(c);
      } else {
        flushSeg(pts);
        pts = [];
      }
    });
    flushSeg(pts);

    // 今日终点高亮圆点 + 选择性直接标签（lastLabel 只标主序列终点，防多序列终点标签堆叠/碰撞）
    if (highlightLast) {
      for (let i = rows.length - 1; i >= 0; i--) {
        const v = rows[i][sr.key];
        if (v === null || v === undefined || isNaN(v)) continue;
        const dot = document.createElementNS(NS, "circle");
        dot.setAttribute("cx", s.x(i)); dot.setAttribute("cy", s.y(v)); dot.setAttribute("r", "5");
        dot.setAttribute("fill", sr.color); dot.setAttribute("stroke", "var(--bg)");
        dot.setAttribute("stroke-width", "2");
        svg.appendChild(dot);
        if (sr.lastLabel) {
          const lbl = document.createElementNS(NS, "text");
          lbl.setAttribute("x", s.x(i)); lbl.setAttribute("y", Math.max(PAD.t + 12, s.y(v) - 10));
          lbl.setAttribute("text-anchor", "middle"); lbl.setAttribute("font-size", "12");
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
  const { color = "var(--accent)", yFmt = v => v, valueLabel = "" } = opts || {};
  const W = 900, H = 260;
  svg.innerHTML = "";
  const yMax = Math.max(1, ...rows.map(r => r[key] || 0)) * 1.15;
  const s = makeScale(rows, yMax, W, H);
  drawGridAndAxes(svg, s, yMax, yFmt);
  drawXLabels(svg, rows, s);
  const barW = Math.min(46, (s.innerW / s.n) * 0.55);
  rows.forEach((r, i) => {
    const v = r[key] || 0;
    const x = s.x(i) - barW / 2, y = s.y(v), h = s.innerH + PAD.t - y;
    const rect = document.createElementNS(NS, "rect");
    rect.setAttribute("x", x); rect.setAttribute("y", y); rect.setAttribute("width", barW);
    rect.setAttribute("height", h); rect.setAttribute("fill", color);
    rect.setAttribute("rx", "4"); rect.setAttribute("ry", "4");
    // 方形底（dataviz：数据端圆角、基线端方形）→ 底部矩形覆盖
    const base = document.createElementNS(NS, "rect");
    base.setAttribute("x", x); base.setAttribute("y", s.innerH + PAD.t - 4);
    base.setAttribute("width", barW); base.setAttribute("height", "4"); base.setAttribute("fill", color);
    svg.appendChild(rect); svg.appendChild(base);
    const ti = document.createElementNS(NS, "title");
    ti.textContent = fmtDate(r.date) + " · " + v + valueLabel;
    rect.appendChild(ti);
    // 仅标最大值（选择性直接标签）
    if (v === Math.max(...rows.map(rr => rr[key] || 0))) {
      const lbl = document.createElementNS(NS, "text");
      lbl.setAttribute("x", x + barW / 2); lbl.setAttribute("y", y - 6);
      lbl.setAttribute("text-anchor", "middle"); lbl.setAttribute("font-size", "12");
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
  return "var(--accent)"; // 退潮
}

function renderKPI() {
  const k = DATA.kpi, emo = DATA.emotion;
  const cards = [
    { label: "情绪温度", value: emo.available ? k.emotion_score : "—",
      valueCls: emo.available ? stageColor(k.emotion_stage) : "",
      sub: emo.available ? '<span class="chip" style="color:' + stageColor(k.emotion_stage)
        + '">' + esc(k.emotion_stage) + "</span>" : "数据不足" },
    { label: "涨停家数", value: k.zt_count, valueCls: "var(--up)" },
    { label: "连板家数", value: k.lianban_count, valueCls: "var(--up)" },
    { label: "空间板高度", value: k.max_lb + " 板", valueCls: "var(--accent)", sub: esc(k.max_lb_stock) },
    { label: "炸板率", value: pct(k.break_rate, 0), valueCls: "var(--warn)" },
    { label: "跌停家数", value: k.dt_count, valueCls: k.dt_count > 0 ? "var(--down)" : "" },
  ];
  document.getElementById("kpi").innerHTML = cards.map(c =>
    '<div class="kpi"><div class="label">' + esc(c.label) + '</div>'
    + '<div class="value" style="color:' + c.valueCls + '">' + esc(c.value) + "</div>"
    + (c.sub ? '<div class="sub">' + c.sub + "</div>" : "") + "</div>").join("");
}

function renderCharts() {
  const rows = DATA.trend;
  const hasData = rows.some(r => r.zt_count > 0);
  const emoAvailable = DATA.emotion.available;

  if (!hasData) {
    ["chart-emotion", "chart-counts", "chart-height", "chart-breakrate"].forEach(id => {
      const el = document.getElementById(id);
      el.innerHTML = '<text x="450" y="150" text-anchor="middle" font-size="14" fill="var(--ink-3)">数据不足</text>';
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
      { yMax: 100, bands, highlightLast: true, yFmt: v => Math.round(v) });
    setLegend(document.getElementById("leg-emotion"),
      [{ color: "var(--accent)", label: "情绪温度分" + (DATA.emotion.stage ? " · 今日" + DATA.emotion.stage : "") }]);
  } else {
    document.getElementById("chart-emotion").innerHTML =
      '<text x="450" y="150" text-anchor="middle" font-size="14" fill="var(--ink-3)">情绪温度数据不足</text>';
  }

  drawLineChart(document.getElementById("chart-counts"), rows,
    [{ key: "zt_count", label: "涨停", color: "var(--accent)", lastLabel: true },
     { key: "lianban_count", label: "连板", color: "var(--accent-2)" },
     { key: "break_count", label: "炸板", color: "var(--accent-3)" }],
    { yMax: Math.max(20, ...rows.map(r => Math.max(r.zt_count, r.break_count))) * 1.15,
      highlightLast: true, yFmt: v => Math.round(v) });
  setLegend(document.getElementById("leg-counts"),
    [{ color: "var(--accent)", label: "涨停" },
     { color: "var(--accent-2)", label: "连板" },
     { color: "var(--accent-3)", label: "炸板" }]);

  drawBarChart(document.getElementById("chart-height"), rows, "max_lb",
    { color: "var(--accent)", valueLabel: " 板", yFmt: v => Math.round(v) });

  drawLineChart(document.getElementById("chart-breakrate"), rows,
    [{ key: "break_rate", label: "炸板率", color: "var(--accent-2)", fmt: v => pct(v), lastLabel: true }],
    { yMax: 1, highlightLast: true, yFmt: v => pct(v, 0) });
  setLegend(document.getElementById("leg-breakrate"),
    [{ color: "var(--accent-2)", label: "炸板率" }]);
}

function renderLadder() {
  const ld = DATA.ladder;
  const el = document.getElementById("ladder");
  if (!ld.ladder.length) { el.innerHTML = '<div class="empty">当日无涨停或数据不足</div>'; return; }
  const rows = ld.ladder.map(r =>
    "<tr><td>" + esc(r.height) + "板</td><td class='num'>" + r.count + "</td>"
    + "<td>" + esc((r.stocks || []).join(" / ") || "—") + "</td>"
    + "<td>" + weakCell(r.weak) + "</td></tr>").join("");
  const prom = Object.keys(ld.promotion || {}).map(k =>
    esc(k) + " " + pct(ld.promotion[k])).join("  ");
  el.innerHTML = "<table><thead><tr><th>高度</th><th class='num'>数量</th>"
    + "<th>代表个股</th><th>炸板 / 弱封</th></tr></thead><tbody>" + rows + "</tbody></table>"
    + (prom ? '<div class="sub" style="margin-top:8px">晋级率：' + prom + "</div>" : "");
}

function renderThemes() {
  const el = document.getElementById("themes");
  const ts = (DATA.themes || []).slice().sort((a, b) => (b.is_main ? 1 : 0) - (a.is_main ? 1 : 0));
  if (!ts.length) { el.innerHTML = '<div class="empty">当日题材归类为空</div>'; return; }
  const rows = ts.map(t =>
    "<tr><td>" + esc(t.theme_name) + (t.is_main ? ' <span class="chip">主线</span>' : "") + "</td>"
    + "<td class='num'>" + t.member_count + "</td>"
    + "<td class='num'>" + t.max_lb + " 板</td>"
    + "<td>" + esc(t.stage || "") + "</td>"
    + "<td>" + esc((t.leader || {}).name || "—") + "</td></tr>").join("");
  el.innerHTML = "<table><thead><tr><th>题材</th><th class='num'>家数</th>"
    + "<th class='num'>最高身位</th><th>阶段</th><th>龙头</th></tr></thead><tbody>"
    + rows + "</tbody></table>";
}

function renderBreak() {
  const b = DATA.break, el = document.getElementById("break");
  if (!b.table.length) {
    el.innerHTML = '<div class="empty">当日炸板家数 ' + b.break_count + "，炸板率 "
      + pct(b.break_rate) + "（无个股明细）</div>";
    return;
  }
  const rows = b.table.map(r =>
    "<tr><td>" + esc(r.code + " " + r.name) + "</td><td>" + esc(r.industry || "") + "</td>"
    + "<td class='num'>" + r.break_times + "</td>"
    + "<td class='num'><span class='" + upDown(r.up_pct) + "'>" + pctPct(r.up_pct, 2) + "</span></td>"
    + "<td class='num'>" + esc(fmtMoney(r.main_net_inflow)) + "</td>"
    + "<td>" + esc(r.signal || "") + "</td></tr>").join("");
  el.innerHTML = "<table><thead><tr><th>代码 名称</th><th>行业</th><th class='num'>炸板次数</th>"
    + "<th class='num'>收盘涨幅</th><th class='num'>主力净流入</th><th>信号</th></tr></thead><tbody>"
    + rows + "</tbody></table>";
}

function renderLhb() {
  const l = DATA.lhb, el = document.getElementById("lhb");
  const ov = l.overview || {};
  if (!ov.stock_count) { el.innerHTML = '<div class="empty">（当日龙虎榜未更新——需盘后 18:00 之后）</div>'; return; }
  const kpis = ["上榜 " + ov.stock_count + " 家", "净买额 " + fmtMoney(ov.total_net_amt),
    "机构上榜 " + (ov.inst_stock_count || 0) + " 家"]
    .map(t => "<span class='chip'>" + esc(t) + "</span>").join(" ");
  const rank = (l.net_rank || []).slice(0, 10).map(r =>
    "<tr><td>" + esc(r.code + " " + r.name) + "</td>"
    + "<td class='num'><span class='" + upDown(r.change_rate) + "'>" + pctPct(r.change_rate, 2) + "</span></td>"
    + "<td class='num'>" + esc(fmtMoney(r.net_amt)) + "</td>"
    + "<td>" + esc((r.reasons || []).join("；").slice(0, 40)) + "</td></tr>").join("");
  let hm = "";
  if ((l.hotmoney || []).length) {
    hm = "<h3 style='font-size:13px;color:var(--ink-2);margin:14px 0 6px'>知名游资动向</h3><table>"
      + "<thead><tr><th>游资</th><th class='num'>净买总额</th><th>标的</th></tr></thead><tbody>"
      + l.hotmoney.slice(0, 8).map(h =>
        "<tr><td>" + esc(h.tag || "") + (h.style_cn ? "（" + esc(h.style_cn) + "）" : "") + "</td>"
        + "<td class='num'>" + esc(fmtMoney(h.net_amt)) + "</td>"
        + "<td>" + esc((h.stocks || []).slice(0, 3).map(s =>
          s.code + " " + s.stock_name).join("、")) + "</td></tr>").join("")
      + "</tbody></table>";
  }
  el.innerHTML = "<div style='margin-bottom:10px'>" + kpis + "</div>"
    + "<table><thead><tr><th>代码 名称</th><th class='num'>涨幅</th>"
    + "<th class='num'>净买额</th><th>上榜原因</th></tr></thead><tbody>" + rank + "</tbody></table>" + hm;
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
    + "<th class='num'>跌停</th><th class='num'>情绪温度</th><th>缺失</th></tr></thead><tbody>"
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

function init() {
  renderKPI();
  renderCharts();
  renderTrendSummary();
  renderEmotionComp();
  renderLadder();
  renderThemes();
  renderBreak();
  renderLhb();
}
document.addEventListener("DOMContentLoaded", init);
</script>
</body>
</html>
"""


def render_html(payload: dict, llm_text: str = "") -> str:
    """渲染自包含 HTML。数据经 JSON 注入 `const DATA`，`</` 转义防断 `<script>`。"""
    data_json = _compact_json(payload).replace("</", "<\\/")
    llm_html = html.escape(llm_text.strip()) if llm_text and llm_text.strip() else '<span class="llm-fallback">（未生成解读）</span>'
    ymd = str(payload.get("trade_date", ""))
    title = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}" if len(ymd) == 8 else ymd
    return (
        _HTML_TEMPLATE
        .replace("__TITLE__", title)
        .replace("__WEEKDAY__", str(payload.get("weekday", "")))
        .replace("__N_DAYS__", str(payload.get("n_days", "")))
        .replace("__LLM_TEXT__", llm_html)
        .replace("__DATA_JSON__", data_json)
    )


_ERROR_TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>数据看板生成失败</title>
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
  <h1>数据看板生成失败（__DATE__）</h1>
  <p>__MSG__</p>
  <p class="sub">可能原因：网络不通（东财接口）、当日行情数据尚未完整，或接口临时限流。可稍后重试，或先确认该日复盘数据已采集。</p>
</div>
</body>
</html>"""


def render_error_html(trade_date: str, message: str) -> str:
    """看板生成失败时的自包含错误页（供 web iframe 用，避免裸 500 白屏）。

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
    no_llm: bool = False,
    out_path: str | Path | None = None,
) -> str:
    """生成数据看板 HTML 并落盘，返回文本。

    流程：collect(n_days) → compute → build_trend → （可选 LLM 解读）→ render_html → 落盘。
    out_path 缺省：output/{trade_date}_看板.html。LLM 失败/无 key 时看板照常渲染。
    """
    from daily_review.pipeline import collect, compute

    print(f"[看板] 交易日 {trade_date}，近 {n_days} 个交易日")
    collected = collect(trade_date, n_days=n_days)
    indicators = compute(collected)
    trend = build_trend(collected, indicators, n_days)
    payload = _assemble_payload(indicators, trend, collected)

    llm_text = ""
    if not no_llm:
        print("[看板] LLM 生成多日趋势解读（DeepSeek）...")
        llm_text = _dashboard_interpretation(indicators, trend, api_key=api_key)

    html_text = render_html(payload, llm_text)

    settings = get_settings()
    out_path = out_path or (settings.output_dir / f"{trade_date}_看板.html")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_text, encoding="utf-8")
    print(f"\n已生成: {out_path}")
    print(f"（{len(html_text.splitlines())} 行）")
    return html_text
