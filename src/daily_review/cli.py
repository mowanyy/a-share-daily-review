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
  python -m daily_review launch            # 图形启动器（tkinter 窗口；双击 启动.bat 同效）
  python -m daily_review launch --dry-run  # 自检：打印运行环境与各子命令，不弹窗
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

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
        print("龙虎榜 未更新或为空（需盘后 18:00 之后）")


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

    try:
        collected = collect(trade_date)
        indicators = compute(collected)
    except Exception as exc:  # noqa: BLE001 —— 网络/解析失败映射为一行可读错误，不抛原始 traceback
        print(f"[review] 采集或指标计算失败：{type(exc).__name__}: {exc}")
        print("（请检查网络是否可达东方财富接口；已有本地数据时可加 --no-llm 仅跑指标）")
        return 1
    _print_summary(indicators)

    if args.no_llm:
        print("\n已跳过 LLM 报告（--no-llm）；数据与指标已就绪")
        return 0

    print("\n[LLM] 生成复盘报告（DeepSeek）...")
    try:
        md = generate_report(indicators, trade_date, strategy=strategy)
    except LLMError as exc:
        print(f"[review] LLM 报告生成失败：{exc}")
        print("（请确认 .env 已配置 DEEPSEEK_API_KEY；或加 --no-llm 跳过 LLM）")
        return 1
    out = get_settings().output_dir / f"{trade_date}_复盘.md"
    print(f"\n已生成: {out}")
    print(f"（{len(md.splitlines())} 行）")
    return 0


def _find_prev_trade_date(trade_date: str) -> str:
    """获取 trade_date 的前一个交易日。"""
    dates = eastmoney_pool.resolve_recent_trade_dates(trade_date, n_days=2)
    if not dates:
        return trade_date
    # dates[0] 是 trade_date 或最近交易日
    if dates[0] == trade_date and len(dates) > 1:
        return dates[1]
    return dates[0]


def _cmd_plan(args) -> None:
    """隔夜预案（9:00 前运行）：基于昨日复盘 + 隔夜消息。"""
    from daily_review.data.eastmoney_news import fetch_overnight_news
    from daily_review.llm.client import LLMError
    from daily_review.llm.premarket import generate_overnight_plan
    from daily_review.pipeline import collect, compute

    trade_date = args.date or _probe_recent_date()
    if not args.date:
        print(f"[plan] 缺省交易日: {trade_date}")

    prev_date = _find_prev_trade_date(trade_date)
    if prev_date == trade_date:
        print(f"[plan] 未找到 {trade_date} 的前一交易日，用昨日复盘数据")
    else:
        print(f"[plan] 昨日复盘基准: {prev_date}")

    # 1. 采集昨日指标（复用 CSV 缓存）
    try:
        collected = collect(prev_date)
        indicators = compute(collected)
    except Exception as exc:
        print(f"[plan] 采集昨日数据失败：{type(exc).__name__}: {exc}")
        return 1
    _print_summary(indicators)

    # 2. 采集隔夜消息
    print("\n[plan] 采集隔夜消息（东财7x24快讯）...")
    try:
        news = fetch_overnight_news(trade_date)
        print(f"  隔夜消息 {len(news)} 条")
    except Exception as exc:
        print(f"[plan] 隔夜消息采集失败：{exc}")
        news = []

    # 3. 生成隔夜预案
    print("\n[LLM] 生成隔夜预案...")
    try:
        md = generate_overnight_plan(indicators, news, trade_date)
    except LLMError as exc:
        print(f"[plan] LLM 隔夜预案生成失败：{exc}")
        return 1
    out = get_settings().output_dir / f"{trade_date}_隔夜预案.md"
    print(f"\n已生成: {out}")
    print(f"（{len(md.splitlines())} 行）")
    return 0


def _cmd_open(args) -> None:
    """开盘策略（9:25-9:30 运行）：基于竞价数据 + 隔夜预案。"""
    from daily_review.analysis.auction import compute_auction, fetch_auction_data
    from daily_review.llm.client import LLMError
    from daily_review.llm.premarket import generate_open_strategy
    from daily_review.pipeline import collect, compute

    trade_date = args.date or _probe_recent_date()
    if not args.date:
        print(f"[open] 缺省交易日: {trade_date}")

    prev_date = _find_prev_trade_date(trade_date)
    if prev_date == trade_date:
        print(f"[open] 未找到 {trade_date} 的前一交易日")
    else:
        print(f"[open] 昨日复盘基准: {prev_date}")

    # 1. 采集昨日指标（复用 CSV 缓存）
    try:
        collected = collect(prev_date)
        indicators = compute(collected)
    except Exception as exc:
        print(f"[open] 采集昨日数据失败：{type(exc).__name__}: {exc}")
        return 1
    _print_summary(indicators)

    # 2. 加载隔夜预案文案
    plan_path = get_settings().output_dir / f"{trade_date}_隔夜预案.md"
    plan_text = ""
    if plan_path.exists():
        plan_text = plan_path.read_text(encoding="utf-8")
        print(f"\n[open] 已加载隔夜预案: {plan_path}")
    else:
        print(f"\n[open] 未找到隔夜预案文件（{plan_path}），继续执行")

    # 3. 获取竞价数据（昨日涨停股）
    zt = collected.get("zt")
    if zt is not None and not zt.empty:
        codes = [str(c) for c in zt["code"]]
        print(f"\n[open] 采集竞价数据（{len(codes)} 只涨停股）...")
        try:
            quotes = fetch_auction_data(codes)
            # 昨日封单映射
            prev_seal_map = {}
            if "fund" in zt.columns:
                import pandas as pd
                for _, r in zt.iterrows():
                    code = str(r["code"])
                    fund = r.get("fund")
                    if fund is not None and pd.notna(fund):
                        prev_seal_map[code] = float(fund)
            auction_data = compute_auction(quotes, prev_seal_map=prev_seal_map)
            ready = [r for r in auction_data if r.get("auction_pct") is not None]
            print(f"  竞价数据 {len(auction_data)} 条（有竞价价 {len(ready)} 只）")
        except Exception as exc:
            print(f"[open] 竞价数据采集失败：{exc}")
            auction_data = []
    else:
        print("\n[open] 昨日无涨停股，竞价数据为空")
        auction_data = []

    # 4. 生成开盘策略
    print("\n[LLM] 生成开盘策略...")
    try:
        md = generate_open_strategy(indicators, auction_data, plan_text, trade_date)
    except LLMError as exc:
        print(f"[open] LLM 开盘策略生成失败：{exc}")
        return 1
    out = get_settings().output_dir / f"{trade_date}_开盘策略.md"
    print(f"\n已生成: {out}")
    print(f"（{len(md.splitlines())} 行）")
    return 0


def _cmd_push(args) -> int:
    """生成报告并推送飞书群机器人（v0.21，供 GitHub Actions 定时 / 本地手动调用）。"""
    from daily_review.push import REPORT_TYPE_LABEL, push_report

    date = args.date or None
    if not date:
        print(f"[push] 缺省交易日: {args.type}（北京时间自动探测）")

    result = push_report(args.type, date, force=args.force)
    label = REPORT_TYPE_LABEL[args.type]
    if result["status"] == "sent":
        print(f"[push] ✅ {result['message']}")
        return 0
    if result["status"] == "skipped":
        print(f"[push] ⏭ {result['message']}")
        return 0
    print(f"[push] ❌ {result['message']}")
    return 1


def _cmd_schedule(args) -> int:
    """本地计划任务：准点推送（v0.22，schtasks，仅工作日触发）。"""
    from daily_review import schedule as sched

    if args.action == "install":
        results = sched.install(dry_run=args.dry_run)
    elif args.action == "remove":
        results = sched.remove()
    else:  # list
        results = sched.list_tasks()
    all_ok = True
    for r in results:
        mark = "✅" if r.ok else "❌"
        print(f"[schedule] {mark} {r.name}: {r.output}")
        all_ok = all_ok and r.ok
    return 0 if all_ok else 1


def _cmd_calendar(args) -> int:
    """权威交易日历（v0.23 A3）：查看/判定/更新。上证指数日K 实证生成。"""
    from daily_review.data import trade_calendar as cal

    if args.update:
        try:
            dates = cal.refresh()
        except Exception as exc:  # noqa: BLE001 —— 网络/解析失败属正常降级
            print(f"[calendar] ❌ 刷新失败：{type(exc).__name__}: {exc}")
            return 1
        print(f"[calendar] ✅ 已刷新 {len(dates)} 个交易日 → {cal._table_path()}")
        return 0

    if args.check:
        verdict = cal.is_trade_date(args.check)
        if verdict is None:
            print(f"[calendar] ⚠ {args.check} 无法判定（日历表缺失/未覆盖，将走探测兜底）")
            return 1
        print(f"[calendar] {args.check}：{'交易日' if verdict else '非交易日（休市）'}")
        return 0

    if args.year:
        holidays = cal.holidays_of_year(args.year)
        if not holidays:
            print(f"[calendar] ⚠ {args.year} 无法判定（日历表缺失/未覆盖 {args.year}）")
            return 1
        print(f"[calendar] {args.year} 年工作日休市日（法定节假日等，{len(holidays)} 天）：")
        for i in range(0, len(holidays), 8):
            print("  " + "  ".join(holidays[i : i + 8]))
        return 0

    # 无参：概况
    dates = cal._load()
    if not dates:
        print(f"[calendar] ⚠ 日历表为空（联网失败或未生成）：{cal._table_path()}")
        print("[calendar] 可手动执行 calendar --update 联网生成")
        return 1
    print(f"[calendar] 日历表 {len(dates)} 个交易日，最新 {max(dates)}，文件 {cal._table_path()}")
    print("[calendar] 用法：calendar --check 20260818 / --year 2026 / --update")
    return 0


def _cmd_intraday(args) -> int:
    """盘中增量监控（v0.27 D1）：基准快照/增量 diff/时间轴累计曲线。"""
    from daily_review.analysis.intraday import load_snapshots, snapshot, summary, take_baseline
    from daily_review.data import eastmoney_pool as em

    trade_date = args.date or _probe_recent_date()
    if args.action == "baseline":
        force = getattr(args, "force", False)
        bl = take_baseline(trade_date, force=force)
        print(f"[intraday] 基准已{'强制' if force else '保存'}: {trade_date} "
              f"涨停{bl['zt_count']} / 炸板{bl['zb_count']} 于 {bl['timestamp']}")
        return 0
    if args.action == "snapshot":
        delta = snapshot(trade_date, force_baseline=args.force_baseline)
        print(f"[intraday] 快照: {trade_date} 于 {delta['timestamp']}")
        if delta["new_zt"]:
            print(f"  🔺 新涨停 {len(delta['new_zt'])} 只: {', '.join(delta['new_zt'][:10])}")
        if delta["broken"]:
            print(f"  🔻 炸板 {len(delta['broken'])} 只: {', '.join(delta['broken'][:10])}")
        if delta["re_sealed"]:
            print(f"  🔄 回封 {len(delta['re_sealed'])} 只: {', '.join(delta['re_sealed'][:10])}")
        print(f"  当前涨停 {delta['zt_count']} / 炸板 {delta['zb_count']}")
        return 0
    if args.action == "snapshots":
        records = load_snapshots(trade_date)
        if not records:
            print(f"[intraday] {trade_date} 无盘中增量记录")
            return 1
        print(f"[intraday] {trade_date} 共 {len(records)} 次快照:")
        for i, r in enumerate(records, 1):
            new_n = len(r.get("new_zt", []))
            broken_n = len(r.get("broken", []))
            re_n = len(r.get("re_sealed", []))
            print(f"  #{i} {r['timestamp']} — 涨停{r['zt_count']} 炸板{r['zb_count']} "
                  f"[+{new_n}新/-{broken_n}炸/↺{re_n}回封]")
        return 0
    # 缺省：summary
    s = summary(trade_date)
    if s["status"] == "no_data":
        print(f"[intraday] {trade_date}：{s['message']}")
        return 1
    print(f"[intraday] {trade_date} 盘中增量摘要:")
    print(f"  基准: 涨停{s['baseline_zt_count']} / 炸板{s['baseline_zb_count']}")
    print(f"  最新: 涨停{s['latest_zt_count']} / 炸板{s['latest_zb_count']} ({s['snapshot_count']} 次快照)")
    if s["cumulative_new_zt"]:
        print(f"  累计新涨停 {len(s['cumulative_new_zt'])} 只: {', '.join(s['cumulative_new_zt'][:10])}")
    if s["cumulative_broken"]:
        print(f"  累计炸板 {len(s['cumulative_broken'])} 只: {', '.join(s['cumulative_broken'][:10])}")
    if s["cumulative_re_sealed"]:
        print(f"  累计回封 {len(s['cumulative_re_sealed'])} 只: {', '.join(s['cumulative_re_sealed'][:10])}")
    return 0


def _cmd_update_data(args) -> None:
    """手动一键更新数据：刷新静态缓存 + 重采近 N 天数据。"""
    from daily_review.data import eastmoney_pool as em
    from daily_review.data.local_cache import refresh_all, trade_dates_path
    from daily_review.pipeline import collect

    trade_date = args.date or _probe_recent_date()
    if not args.date:
        print(f"[update-data] 缺省交易日: {trade_date}")
    days = max(1, args.days)

    # 1. 刷新静态缓存
    print("\n[update-data] 刷新静态缓存...")

    def _fetch_industry():
        return em.fetch_stock_industry_map()

    def _fetch_trade_dates():
        dates = em.resolve_recent_trade_dates(trade_date, n_days=days)
        return set(dates) if dates else set()

    result = refresh_all(_fetch_industry, _fetch_trade_dates, force=args.force)
    if result["industry_map"]:
        print("  行业映射：已刷新")
    else:
        print("  行业映射：缓存有效，跳过")
    if result["trade_dates"]:
        print("  交易日历：已刷新")
    else:
        print("  交易日历：缓存有效，跳过")

    # 2. 遍历近 N 天，重采数据
    dates = em.resolve_recent_trade_dates(trade_date, n_days=days)
    print(f"\n[update-data] 重采 {len(dates)} 个交易日数据...")
    for i, d in enumerate(dates, 1):
        print(f"  [{i}/{len(dates)}] {d} ...", end=" ", flush=True)
        try:
            collected = collect(d)
            print(f"涨停 {len(collected.get('zt', []))} 家", end="")
            zb = collected.get("zb", [])
            if len(zb):
                print(f" / 炸板 {len(zb)} 家", end="")
            lhb = collected.get("lhb_daily", [])
            if len(lhb):
                print(f" / 龙虎榜 {len(lhb)} 条", end="")
            print()
        except Exception as exc:
            print(f"失败：{type(exc).__name__}: {exc}")

    print(f"\n[update-data] 完成！已更新 {len(dates)} 天数据")
    return 0


def _cmd_split_pool(args) -> None:
    """供选股数据按日期切分。"""
    from daily_review.tools import split_stock_pool_by_date

    src = args.src
    result = split_stock_pool_by_date(src=src)
    total = sum(result.values())
    print(f"\n[split-pool] 共 {len(result)} 个日期文件，{total} 行数据")
    return 0


def _cmd_skill(args) -> None:
    """战法 ↔ SKILL.md 桥：import 把 SKILL.md 转成个人战法；export 把战法导出为 SKILL.md。"""
    from daily_review.web.skill_bridge import export_strategy, import_skill
    from daily_review.web.strategy import StrategyError, user_dir

    if args.action == "import":
        p = Path(args.file).resolve()
        if not p.exists():
            print(f"[skill] 文件不存在: {p}")
            return 1
        try:
            result = import_skill(p.read_text(encoding="utf-8"))
        except ValueError as exc:
            print(f"[skill] 导入失败：{exc}")
            return 1
        print(f"[skill] 已导入为战法: {result['name']}（{result['id']}）")
        print(f"[skill] 落盘: {user_dir()}")
        if result["missing_sections"]:
            print(f"[skill] 缺节（不影响执行，建议 Web 编辑补全）: {'、'.join(result['missing_sections'])}")
        return 0

    try:
        skill_md = export_strategy(args.strategy_id)
    except StrategyError as exc:
        print(f"[skill] 导出失败：{exc}")
        return 1
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(skill_md, encoding="utf-8")
        print(f"[skill] 已导出: {out.resolve()}")
    else:
        print(skill_md)
    return 0


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


def _cmd_launch(args) -> int:
    from daily_review.launcher import run_gui

    return run_gui(dry_run=args.dry_run)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="daily-review", description="每日复盘 · 数据采集 + 端到端复盘 CLI（v0.9）"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_kline = sub.add_parser("kline", help="东方财富日 K 线")
    p_kline.add_argument("--code", required=True, help="股票代码，如 600000")
    p_kline.add_argument("--lmt", type=int, default=120, help="返回条数（默认 120）")
    p_kline.add_argument("--klt", type=int, default=101, help="周期 101=日线 102=周线 103=月线（基金风格周K/月K分析用）")
    p_kline.add_argument("--fqt", type=int, default=0, help="复权 0=不复权 1=前复权 2=后复权")
    p_kline.set_defaults(func=_cmd_kline)

    p_rt = sub.add_parser("realtime", help="新浪实时行情")
    p_rt.add_argument(
        "--codes", required=True, help="逗号分隔代码，如 600601,002398,600789"
    )
    p_rt.set_defaults(func=_cmd_realtime)

    p_rev = sub.add_parser(
        "review",
        help="端到端复盘：采集→指标→LLM 报告（涨停数据收盘 15:00 后、龙虎榜 18:00 后完整）",
    )
    p_rev.add_argument("--date", default="", help="交易日 YYYYMMDD，缺省探测最近交易日")
    p_rev.add_argument(
        "--no-llm", action="store_true", help="只采集+算指标，跳过 LLM 报告"
    )
    p_rev.add_argument(
        "--strategy", default="", help="战法 ID（如 strategy.user-xxx；缺省走通用预案）"
    )
    p_rev.set_defaults(func=_cmd_review)

    p_plan = sub.add_parser(
        "plan",
        help="隔夜预案（9:00 前运行）：基于昨日复盘 + 东财7x24隔夜消息，输出今日关注方向",
    )
    p_plan.add_argument("--date", default="", help="今日交易日 YYYYMMDD（预案适用日），缺省探测最近交易日")
    p_plan.set_defaults(func=_cmd_plan)

    p_open = sub.add_parser(
        "open",
        help="开盘策略（9:25-9:30 运行）：基于竞价数据 + 隔夜预案，筛选有机会的个股",
    )
    p_open.add_argument("--date", default="", help="今日交易日 YYYYMMDD，缺省探测最近交易日")
    p_open.set_defaults(func=_cmd_open)

    p_push = sub.add_parser(
        "push",
        help="生成报告并推送飞书群机器人（定时推送，GitHub Actions 用；也可本地手动测试）",
    )
    p_push.add_argument(
        "--type", required=True, choices=["review", "plan", "open"],
        help="报告类型：review=复盘 / plan=隔夜预案 / open=开盘策略",
    )
    p_push.add_argument(
        "--force", action="store_true",
        help="强制重推：忽略幂等（该日已推送过也重新生成并推送）",
    )
    p_push.add_argument("--date", default="", help="交易日 YYYYMMDD，缺省按北京时间自动探测")
    p_push.set_defaults(func=_cmd_push)

    p_sched = sub.add_parser(
        "schedule",
        help="本地计划任务：schtasks 准点推送（v0.22，仅工作日；绕开 GitHub Actions 排程延迟）",
    )
    sched_sub = p_sched.add_subparsers(dest="action", required=True)
    p_sched_install = sched_sub.add_parser("install", help="创建计划任务（09:25 开盘策略，周一~周五）")
    p_sched_install.add_argument(
        "--dry-run", action="store_true", help="只打印将执行的 schtasks 命令，不创建"
    )
    p_sched_install.set_defaults(func=_cmd_schedule, action="install")
    p_sched_remove = sched_sub.add_parser("remove", help="删除全部计划任务")
    p_sched_remove.set_defaults(func=_cmd_schedule, action="remove")
    p_sched_list = sched_sub.add_parser("list", help="查询计划任务注册状态")
    p_sched_list.set_defaults(func=_cmd_schedule, action="list")

    p_cal = sub.add_parser(
        "calendar",
        help="权威交易日历（v0.23）：查看/判定交易日，上证指数日K 实证生成 data/trade_calendar.csv",
    )
    p_cal.add_argument("--check", default="", help="判定单日是否交易日，如 20260818")
    p_cal.add_argument("--year", type=int, default=0, help="列出该年工作日休市日（法定节假日等）")
    p_cal.add_argument("--update", action="store_true", help="强制联网刷新日历表")
    p_cal.set_defaults(func=_cmd_calendar)

    p_intra = sub.add_parser(
        "intraday",
        help="盘中增量监控（v0.27 D1）：基准快照/增量 diff/时间轴累计曲线",
    )
    intra_sub = p_intra.add_subparsers(dest="action", required=True)
    p_intra_base = intra_sub.add_parser("baseline", help="采集并保存早盘基准快照")
    p_intra_base.add_argument("--date", default="", help="交易日 YYYYMMDD，缺省探测最近交易日")
    p_intra_base.add_argument("--force", action="store_true", help="强制刷新基准")
    p_intra_base.set_defaults(func=_cmd_intraday, action="baseline")
    p_intra_snap = intra_sub.add_parser("snapshot", help="拉取当前快照，与基准 diff 并落盘")
    p_intra_snap.add_argument("--date", default="", help="交易日 YYYYMMDD，缺省探测最近交易日")
    p_intra_snap.add_argument("--force-baseline", action="store_true", help="强制刷新基准（无基准时自动创建）")
    p_intra_snap.set_defaults(func=_cmd_intraday, action="snapshot")
    p_intra_hist = intra_sub.add_parser("snapshots", help="列出当日所有增量记录")
    p_intra_hist.add_argument("--date", default="", help="交易日 YYYYMMDD，缺省探测最近交易日")
    p_intra_hist.set_defaults(func=_cmd_intraday, action="snapshots")

    p_update = sub.add_parser(
        "update-data",
        help="手动一键更新数据：刷新静态缓存（行业映射/交易日历）+ 重采近 N 天数据",
    )
    p_update.add_argument("--date", default="", help="结束交易日 YYYYMMDD，缺省探测最近交易日")
    p_update.add_argument("--days", type=int, default=6, help="重采近 N 个交易日（默认 6）")
    p_update.add_argument(
        "--force", action="store_true", help="强制刷新静态缓存（无视 TTL）"
    )
    p_update.set_defaults(func=_cmd_update_data)

    p_split = sub.add_parser(
        "split-pool",
        help="供选股数据.csv 按日期切分到 data/stock_pool/",
    )
    p_split.add_argument(
        "--src", default="", help="源 CSV 路径（缺省为项目根目录 供选股数据.csv）"
    )
    p_split.set_defaults(func=_cmd_split_pool)

    p_skill = sub.add_parser(
        "skill",
        help="战法 ↔ SKILL.md 桥：import 把 SKILL.md 转成个人战法；export 把战法导出为 SKILL.md",
    )
    skill_sub = p_skill.add_subparsers(dest="action", required=True)
    p_skill_import = skill_sub.add_parser("import", help="导入 SKILL.md 为个人战法")
    p_skill_import.add_argument("file", help="SKILL.md 文件路径")
    p_skill_import.set_defaults(func=_cmd_skill, action="import")
    p_skill_export = skill_sub.add_parser("export", help="导出战法为 SKILL.md 文本")
    p_skill_export.add_argument("strategy_id", help="战法 ID（如 strategy.user-xxx）")
    p_skill_export.add_argument("--out", default="", help="输出文件路径；缺省打印到控制台")
    p_skill_export.set_defaults(func=_cmd_skill, action="export")

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

    p_launch = sub.add_parser(
        "launch",
        help="图形启动器（tkinter 窗口，双击 启动.bat 同效；--dry-run 打印运行环境与命令不弹窗）",
    )
    p_launch.add_argument(
        "--dry-run", "--self-check", action="store_true", dest="dry_run",
        help="打印解析出的运行环境与各子命令，不打开窗口（自检）",
    )
    p_launch.set_defaults(func=_cmd_launch)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = args.func(args)
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    sys.exit(main())
