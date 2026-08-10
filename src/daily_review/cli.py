"""命令行入口：python -m daily_review kline / realtime / review / dashboard / qa。

示例：
  python -m daily_review kline --code 600000 --lmt 30
  python -m daily_review realtime --codes 600601,002398,600789
  python -m daily_review review --date 20260806 --no-llm
  python -m daily_review review            # 缺省探测最近交易日，需 .env 配置 DEEPSEEK_API_KEY
  python -m daily_review dashboard --date 20260806 --no-llm --open
  python -m daily_review dashboard         # 近 10 个交易日，缺省探测最近交易日
  python -m daily_review qa                # 交互问答（RAG 知识库 + 数据工具），无 key 也可跑
  python -m daily_review qa --ask "什么是炸板率？" --no-embedding
  python -m daily_review qa --setup        # 安装向量检索依赖并下载 bge 模型
  python -m daily_review web               # Web 工作台（战法管理 + 跑复盘 + 问答 + 数据看板）
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from datetime import datetime

from daily_review.config import get_settings
from daily_review.data import eastmoney, eastmoney_pool, repo, sina


def _print(df) -> None:
    print(df.to_string(index=False))


def _console_safe(text: str) -> str:
    """把文本转成当前控制台可编码的版本（GBK 控制台对 emoji 等会崩，用 ? 替换）。

    问答的回答/出处可能含 emoji（如知识库示例里的图表符号、LLM 输出），print 前必须过一遍。
    """
    enc = sys.stdout.encoding or "utf-8"
    try:
        text.encode(enc)
        return text
    except UnicodeEncodeError:
        return text.encode(enc, "replace").decode(enc)


def _fmt_amount(v) -> str:
    """金额 → 亿/万 文本（GBK 安全）。"""
    if v is None:
        return "缺"
    v = float(v)
    if abs(v) >= 1e8:
        return f"{v / 1e8:+.2f}亿"
    if abs(v) >= 1e4:
        return f"{v / 1e4:+.0f}万"
    return f"{v:+.0f}"


def _cmd_kline(args) -> None:
    df = eastmoney.fetch_kline(args.code, klt=args.klt, fqt=args.fqt, lmt=args.lmt)
    _print(df)
    path = repo.save_csv(df, f"kline_{args.code}")
    print(f"\n已保存: {path}")


def _cmd_realtime(args) -> None:
    codes = [c for c in args.codes.split(",") if c.strip()]
    df = sina.fetch_realtime(codes)
    _print(df)
    name = f"realtime_{datetime.today().strftime('%Y%m%d')}"
    path = repo.save_csv(df, name)
    print(f"\n已保存: {path}")


def _print_summary(ind: dict) -> None:
    ladder = ind["ladder"]
    print("\n=== 指标 ===")
    print(
        f"涨停 {ladder['zt_count']} / 连板 {ladder['lianban_count']} / "
        f"空间板 {ladder['max_lb']} 板({ladder['max_lb_stock']})"
    )
    print(f"炸板 {ladder['break_count']} / 炸板率 {ladder['break_rate'] * 100:.1f}%")
    prom = ladder.get("promotion") or {}
    if prom:
        print("晋级率: " + "  ".join(f"{k}={v * 100:.1f}%" for k, v in prom.items()))
    print(f"题材 {len(ind['themes'])} 个")
    for t in ind["themes"][:5]:
        leader = (t.get("leader") or {}).get("name", "")
        print(
            f"  - {t['theme_name']} 家数{t['member_count']} 高度{t['max_lb']} "
            f"阶段{t['stage']} 龙头 {leader}"
        )
    print(f"炸板资金流表 {len(ind['break'].get('table', []))} 行")

    emo = ind.get("emotion") or {}
    if emo.get("available"):
        print(f"情绪温度 {emo.get('score')} 分 / 周期 {emo.get('stage')}")
        if emo.get("notes"):
            print(f"情绪备注: {'; '.join(emo['notes'])}")
    else:
        print("情绪温度 数据不足")

    lhb = ind.get("lhb") or {}
    ov = lhb.get("overview") or {}
    if ov.get("stock_count"):
        print(
            f"龙虎榜 {ov['stock_count']} 家 / 净买额 {_fmt_amount(ov.get('total_net_amt'))} / "
            f"机构上榜 {ov.get('inst_stock_count', 0)} 家"
        )
        for h in lhb.get("hotmoney", [])[:5]:
            tops = "、".join(
                f"{s.get('code')} {s.get('stock_name')}" for s in (h.get("stocks") or [])[:2]
            )
            print(
                f"  - {h.get('tag', '')}({h.get('style_cn', '')}) "
                f"净买{_fmt_amount(h.get('net_amt'))} 标的: {tops}"
            )
    else:
        print("龙虎榜 未更新或为空（需盘后 17:30 之后）")


def _probe_recent_date() -> str:
    """缺省交易日：探测最近有涨停数据的交易日（空则退化为今天）。"""
    dates = eastmoney_pool.resolve_recent_trade_dates(
        datetime.today().strftime("%Y%m%d"), n_days=1
    )
    return dates[0] if dates else datetime.today().strftime("%Y%m%d")


def _cmd_review(args) -> None:
    from daily_review.llm.client import LLMError
    from daily_review.llm.reporter import generate_report
    from daily_review.pipeline import collect, compute

    trade_date = args.date or _probe_recent_date()
    if not args.date:
        print(f"[review] 缺省交易日: {trade_date}")

    strategy = None
    if args.strategy:
        from daily_review.web.strategy import get_strategy

        strategy = get_strategy(args.strategy)
        if strategy is None:
            print(f"[review] 未找到战法 {args.strategy}，按通用预案处理")
        else:
            print(
                f"[review] 战法: {strategy.name}（v{strategy.version or '0.1.0'}，"
                f"适用 {strategy.applies_to or '见正文'}）→ 次日预案按该战法执行"
            )

    collected = collect(trade_date)
    indicators = compute(collected)
    _print_summary(indicators)

    if args.no_llm:
        print("\n已跳过 LLM 报告（--no-llm）；数据与指标已就绪")
        return

    print("\n[LLM] 生成复盘报告（DeepSeek）...")
    md = generate_report(indicators, trade_date, strategy=strategy)
    out = get_settings().output_dir / f"{trade_date}_复盘.md"
    print(f"\n已生成: {out}")
    print(f"（{len(md.splitlines())} 行）")


def _cmd_web(args) -> None:
    from daily_review.web.app import create_app

    app = create_app()
    url = f"http://{args.host}:{args.port}/"
    if args.open:
        webbrowser.open(url)
    print(f"Web 工作台启动: {url}  （Ctrl+C 退出；仅本机访问）")
    # 无认证服务：默认只绑 127.0.0.1，勿随意暴露到局域网
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


def _qa_repl(session) -> None:
    """交互问答 REPL：逐轮提问，展示回答与知识库出处。"""
    print("问答模式（RAG 短线知识库 + 数据工具）。输入 exit / quit / 退出 结束。")
    while True:
        try:
            q = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not q:
            continue
        if q.lower() in ("exit", "quit", "退出", "q"):
            return
        result = session.answer(q)
        print(_console_safe(result.answer))
        if result.tool_rounds:
            print(f"（调用了 {result.tool_rounds} 轮数据工具）")
        print("\n[出处]")
        print(_console_safe(render_sources(result.sources)))


def _cmd_qa(args) -> None:
    from daily_review.kb import embedding
    from daily_review.kb.index import KnowledgeIndex
    from daily_review.kb.qa import QASession, render_sources

    if args.setup:
        ok = embedding.install_embedding()
        print("向量检索环境已就绪" if ok else "向量检索环境安装失败（可继续用纯关键词检索）")
        return

    index = KnowledgeIndex(
        use_embedding=not args.no_embedding,
        include_output_reports=args.with_reports,
    )
    index.ensure_ready(force=args.rebuild)
    mode = "混合检索" if index.vector_available else "关键词检索"
    if not args.no_embedding and not index.vector_available:
        print("提示: 向量检索未启用（未装 sentence-transformers 或模型缺失），当前为纯关键词检索（可 --setup 安装）")
    print(f"知识库就绪: {len(index.chunks)} 个片段 / {mode}")

    trade_date = args.date or _probe_recent_date()
    if not args.date:
        print(f"[qa] 缺省交易日: {trade_date}")
    session = QASession(
        index,
        trade_date=trade_date,
        top_k=args.top_k,
        use_embedding=not args.no_embedding,
    )

    if args.ask:
        result = session.answer(args.ask)
        print(_console_safe(result.answer))
        if result.tool_rounds:
            print(f"（调用了 {result.tool_rounds} 轮数据工具）")
        print("\n[出处]")
        print(_console_safe(render_sources(result.sources)))
        return

    _qa_repl(session)


def _cmd_dashboard(args) -> None:
    from daily_review.dashboard import generate_dashboard

    trade_date = args.date or _probe_recent_date()
    if not args.date:
        print(f"[dashboard] 缺省交易日: {trade_date}")
    if args.days < 2:
        print("[dashboard] --days 至少为 2，已按 2 处理")
        args.days = 2
    generate_dashboard(trade_date, n_days=args.days, no_llm=args.no_llm)
    path = get_settings().output_dir / f"{trade_date}_看板.html"
    if args.open:
        webbrowser.open(path.resolve().as_uri())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="daily-review", description="每日复盘 · 数据采集 + 端到端复盘 CLI（v0.8）"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_kline = sub.add_parser("kline", help="东方财富日 K 线")
    p_kline.add_argument("--code", required=True, help="股票代码，如 600000")
    p_kline.add_argument("--lmt", type=int, default=120, help="返回条数（默认 120）")
    p_kline.add_argument("--klt", type=int, default=101, help="周期 101=日线 102=周线")
    p_kline.add_argument("--fqt", type=int, default=0, help="复权 0=不复权 1=前复权 2=后复权")
    p_kline.set_defaults(func=_cmd_kline)

    p_rt = sub.add_parser("realtime", help="新浪实时行情")
    p_rt.add_argument(
        "--codes", required=True, help="逗号分隔代码，如 600601,002398,600789"
    )
    p_rt.set_defaults(func=_cmd_realtime)

    p_rev = sub.add_parser(
        "review",
        help="端到端复盘：采集→指标→LLM 报告（涨停数据收盘 15:00 后、龙虎榜 17:30 后完整）",
    )
    p_rev.add_argument("--date", default="", help="交易日 YYYYMMDD，缺省探测最近交易日")
    p_rev.add_argument(
        "--no-llm", action="store_true", help="只采集+算指标，跳过 LLM 报告"
    )
    p_rev.add_argument(
        "--strategy", default="", help="战法 ID（如 strategy.user-xxx；缺省走通用预案）"
    )
    p_rev.set_defaults(func=_cmd_review)

    p_dash = sub.add_parser(
        "dashboard",
        help="数据看板：近 N 日趋势图表（单文件 HTML）+ LLM 多日解读",
    )
    p_dash.add_argument("--date", default="", help="交易日 YYYYMMDD，缺省探测最近交易日")
    p_dash.add_argument("--days", type=int, default=10, help="近 N 个交易日（默认 10）")
    p_dash.add_argument(
        "--no-llm", action="store_true", help="跳过 LLM 多日趋势解读（看板照常渲染）"
    )
    p_dash.add_argument(
        "--open", action="store_true", help="生成后用系统默认浏览器打开"
    )
    p_dash.set_defaults(func=_cmd_dashboard)

    p_qa = sub.add_parser(
        "qa",
        help="交互问答：RAG 短线知识库（持续更新）+ 数据工具（function-calling）",
    )
    p_qa.add_argument("--ask", default="", help="一次性提问并退出（缺省进入交互 REPL）")
    p_qa.add_argument("--date", default="", help="默认交易日 YYYYMMDD（数据工具用），缺省探测最近交易日")
    p_qa.add_argument("--rebuild", action="store_true", help="强制重建知识库索引")
    p_qa.add_argument("--setup", action="store_true", help="安装向量检索依赖并从 ModelScope 下载 bge 模型")
    p_qa.add_argument("--top-k", type=int, default=5, help="检索返回片段数（默认 5）")
    p_qa.add_argument("--no-embedding", action="store_true", help="禁用向量检索（纯关键词）")
    p_qa.add_argument("--with-reports", action="store_true", help="把 output/*_复盘.md 也纳入知识库")
    p_qa.set_defaults(func=_cmd_qa)

    p_web = sub.add_parser(
        "web",
        help="Web 工作台：战法管理 + 跑复盘看报告/次日预案 + 问答 + 数据看板（Flask）",
    )
    p_web.add_argument(
        "--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1，仅本机；无认证，勿暴露局域网）"
    )
    p_web.add_argument("--port", type=int, default=5000, help="监听端口（默认 5000）")
    p_web.add_argument(
        "--open", action="store_true", help="启动后用系统默认浏览器打开"
    )
    p_web.set_defaults(func=_cmd_web)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
