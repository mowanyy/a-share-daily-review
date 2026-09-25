"""龙虎榜层离线测试：字段映射解析 / 去重规则 / 游资识别 / 分析输出 / payload 对齐（不联网）。"""

from __future__ import annotations

import pandas as pd

import daily_review.data.eastmoney_lhb as lhb_em
from daily_review.analysis.lhb import analyze_lhb
from daily_review.data.hotmoney_seats import match_hotmoney
from daily_review.llm.reporter import _lhb_payload

# ---------------------------------------------------------------- 合成 API 行


def _api_daily_rows() -> list[dict]:
    base = {
        "TRADE_DATE": "2026-08-06 00:00:00",
        "SECURITY_CODE": "002428", "SECUCODE": "002428.SZ",
        "SECURITY_NAME_ABBR": "云南锗业", "CLOSE_PRICE": 90.98,
        "CHANGE_RATE": 9.9988, "TURNOVERRATE": 11.9683,
        "ACCUM_AMOUNT": 12527546124,
        "BILLBOARD_NET_AMT": 1153853827.89, "BILLBOARD_BUY_AMT": 2370099930.67,
        "BILLBOARD_SELL_AMT": 1216246102.78,
        "DEAL_AMOUNT_RATIO": 28.62, "DEAL_NET_RATIO": 9.21,
        "EXPLANATION": "连续三个交易日内，涨幅偏离值累计达到20%的证券",
        "EXPLAIN": "2家机构买入，成功率42.50%",
        "MARKET": "SZ", "TRADE_MARKET": "深交所主板",
        "BUY_SEAT": 11133, "SELL_SEAT": 11331, "BUY_RATIO": 18.08, "SELL_RATIO": 9.70,
    }
    second = {
        "TRADE_DATE": "2026-08-06 00:00:00",
        "SECURITY_CODE": "002428", "SECUCODE": "002428.SZ",
        "SECURITY_NAME_ABBR": "云南锗业", "CLOSE_PRICE": 90.98,
        "CHANGE_RATE": 9.9988, "TURNOVERRATE": 11.9683,
        "ACCUM_AMOUNT": 12527546124,
        "BILLBOARD_NET_AMT": 479146325.25, "BILLBOARD_BUY_AMT": 1300000000.0,
        "BILLBOARD_SELL_AMT": 820853674.75,
        "DEAL_AMOUNT_RATIO": 15.0, "DEAL_NET_RATIO": 3.8,
        "EXPLANATION": "日涨幅偏离值达到7%的前5只证券",
        "EXPLAIN": "普通席位买入，成功率43.79%",
        "MARKET": "SZ", "TRADE_MARKET": "深交所主板",
        "BUY_SEAT": 5, "SELL_SEAT": 5, "BUY_RATIO": 10.0, "SELL_RATIO": 6.5,
    }
    zt_stock = {
        "TRADE_DATE": "2026-08-06 00:00:00",
        "SECURITY_CODE": "603221", "SECUCODE": "603221.SH",
        "SECURITY_NAME_ABBR": "爱丽家居", "CLOSE_PRICE": 22.0,
        "CHANGE_RATE": 9.99, "TURNOVERRATE": 5.0,
        "ACCUM_AMOUNT": 1000000000,
        "BILLBOARD_NET_AMT": 80000000.0, "BILLBOARD_BUY_AMT": 120000000.0,
        "BILLBOARD_SELL_AMT": 40000000.0,
        "DEAL_AMOUNT_RATIO": 12.0, "DEAL_NET_RATIO": 8.0,
        "EXPLANATION": "连续三个交易日内，涨幅偏离值累计达到20%的证券",
        "EXPLAIN": "普通席位买入，成功率40.00%",
        "MARKET": "SH", "TRADE_MARKET": "上交所主板",
        "BUY_SEAT": 5, "SELL_SEAT": 5, "BUY_RATIO": 12.0, "SELL_RATIO": 4.0,
    }
    return [base, second, zt_stock]


def _api_seat_rows() -> list[dict]:
    return [
        {
            "OPERATEDEPT_CODE": "10467900",
            "OPERATEDEPT_NAME": "国泰海通证券股份有限公司上海自贸试验区第二分公司",
            "SECURITY_CODE": "002428", "SECURITY_NAME_ABBR": "云南锗业",
            "ACT_BUY": 906666399.25, "ACT_SELL": 0.0, "NET_AMT": 906666399.25,
            "EXPLANATION": "连续三个交易日内，涨幅偏离值累计达到20%的证券",
            "CHANGE_RATE": 9.9988, "ORG_NAME_ABBR": "国泰海通证券上海自贸试验区第二分公司",
        },
        {
            "OPERATEDEPT_CODE": "10467900",
            "OPERATEDEPT_NAME": "国泰海通证券股份有限公司上海自贸试验区第二分公司",
            "SECURITY_CODE": "002428", "SECURITY_NAME_ABBR": "云南锗业",
            "ACT_BUY": 410436399.25, "ACT_SELL": 0.0, "NET_AMT": 410436399.25,
            "EXPLANATION": "日涨幅偏离值达到7%的前5只证券",
            "CHANGE_RATE": 9.9988, "ORG_NAME_ABBR": "国泰海通证券上海自贸试验区第二分公司",
        },
        {
            "OPERATEDEPT_CODE": "10499999",
            "OPERATEDEPT_NAME": "华鑫证券有限责任公司上海分公司",
            "SECURITY_CODE": "603221", "SECURITY_NAME_ABBR": "爱丽家居",
            "ACT_BUY": 50000000.0, "ACT_SELL": 0.0, "NET_AMT": 50000000.0,
            "EXPLANATION": "连续三个交易日内，涨幅偏离值累计达到20%的证券",
            "CHANGE_RATE": 9.99, "ORG_NAME_ABBR": "华鑫证券上海分公司",
        },
        {
            "OPERATEDEPT_CODE": "10011111",
            "OPERATEDEPT_NAME": "东方财富证券股份有限公司拉萨团结路第二证券营业部",
            "SECURITY_CODE": "002428", "SECURITY_NAME_ABBR": "云南锗业",
            "ACT_BUY": 1000000.0, "ACT_SELL": 5000000.0, "NET_AMT": -4000000.0,
            "EXPLANATION": "日涨幅偏离值达到7%的前5只证券",
            "CHANGE_RATE": 9.9988, "ORG_NAME_ABBR": "东方财富证券拉萨团结路第二营业部",
        },
    ]


def _mk_daily(rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=lhb_em.LHB_DAILY_COLUMNS)


def _mk_seats(rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=lhb_em.LHB_SEAT_COLUMNS)


def _mk_zt() -> pd.DataFrame:
    return pd.DataFrame([
        {"trade_date": "20260806", "code": "603221", "name": "爱丽家居", "lb_num": 10,
         "first_limit_time": "09:25", "last_limit_time": "09:25", "open_times": 1,
         "seal_amount": 6.5e7, "turnover": 1.0, "amount": 1e8, "industry": "家居用品"},
    ])


def _daily_df() -> pd.DataFrame:
    rows = []
    for r in _api_daily_rows():
        rows.append({
            "trade_date": "20260806",
            "code": r["SECURITY_CODE"], "secucode": r["SECUCODE"], "name": r["SECURITY_NAME_ABBR"],
            "close_price": r["CLOSE_PRICE"], "change_rate": r["CHANGE_RATE"],
            "turnover_rate": r["TURNOVERRATE"], "accum_amount": r["ACCUM_AMOUNT"],
            "lhb_net_amt": r["BILLBOARD_NET_AMT"], "lhb_buy_amt": r["BILLBOARD_BUY_AMT"],
            "lhb_sell_amt": r["BILLBOARD_SELL_AMT"],
            "deal_amount_ratio": r["DEAL_AMOUNT_RATIO"], "deal_net_ratio": r["DEAL_NET_RATIO"],
            "reason": r["EXPLANATION"], "reason_type": r["EXPLAIN"],
            "market": r["MARKET"], "trade_market": r["TRADE_MARKET"],
            "buy_seats": r["BUY_SEAT"], "sell_seats": r["SELL_SEAT"],
            "buy_ratio": r["BUY_RATIO"], "sell_ratio": r["SELL_RATIO"],
        })
    return _mk_daily(rows)


def _seat_df() -> pd.DataFrame:
    rows = []
    for r in _api_seat_rows():
        rows.append({
            "trade_date": "20260806",
            "code": r["SECURITY_CODE"], "name": r["SECURITY_NAME_ABBR"],
            "seat_code": r["OPERATEDEPT_CODE"], "seat_name": r["OPERATEDEPT_NAME"],
            "seat_abbr": r["ORG_NAME_ABBR"],
            "act_buy": r["ACT_BUY"], "act_sell": r["ACT_SELL"], "net_amt": r["NET_AMT"],
            "reason": r["EXPLANATION"], "change_rate": r["CHANGE_RATE"],
        })
    return _mk_seats(rows)


# ---------------------------------------------------------------- 测试


class TestLhbFetchMapping:
    """接口 → DataFrame 字段映射（monkeypatch _paginate 模拟 API 返回）。"""

    def test_daily_mapping(self, monkeypatch):
        monkeypatch.setattr(lhb_em, "_paginate", lambda *a, **k: _api_daily_rows())
        df = lhb_em.fetch_lhb_daily("20260806")
        assert list(df.columns) == lhb_em.LHB_DAILY_COLUMNS
        assert len(df) == 3
        row = df.iloc[0]
        assert row["code"] == "002428"
        assert row["name"] == "云南锗业"
        assert row["lhb_net_amt"] == 1153853827.89
        assert row["reason"] == "连续三个交易日内，涨幅偏离值累计达到20%的证券"

    def test_seat_mapping(self, monkeypatch):
        monkeypatch.setattr(lhb_em, "_paginate", lambda *a, **k: _api_seat_rows())
        df = lhb_em.fetch_lhb_seats("20260806")
        assert list(df.columns) == lhb_em.LHB_SEAT_COLUMNS
        assert len(df) == 4
        row = df.iloc[0]
        assert row["seat_abbr"] == "国泰海通证券上海自贸试验区第二分公司"
        assert row["act_buy"] == 906666399.25

    def test_empty_page_returns_empty(self, monkeypatch):
        monkeypatch.setattr(lhb_em, "_paginate", lambda *a, **k: [])
        assert lhb_em.fetch_lhb_daily("20260806").empty
        assert lhb_em.fetch_lhb_seats("20260806").empty


class TestHotmoneyMatch:
    def test_known_seat(self):
        # 分析层传入「全名 + 简称」拼接串（简称含关键词，防「有限责任公司」隔断）
        hit = match_hotmoney("华鑫证券有限责任公司上海分公司 华鑫证券上海分公司")
        assert hit["tag"] == "炒股养家"
        assert hit["style"] == "daban"

    def test_broker_rename_immune(self):
        # 券商改名（国泰君安→国泰海通）不影响路名关键词
        hit = match_hotmoney("国泰海通证券股份有限公司上海江苏路证券营业部")
        assert hit["tag"] == "章盟主"

    def test_retail_lasa(self):
        hit = match_hotmoney("东方财富证券股份有限公司拉萨团结路第二证券营业部")
        assert hit["style"] == "retail"

    def test_unknown(self):
        assert match_hotmoney("招商证券股份有限公司北京远大路证券营业部") is None
        assert match_hotmoney("") is None


class TestAnalyzeLhb:
    def test_dedup_and_analysis(self):
        res = analyze_lhb(_daily_df(), _seat_df(), _mk_zt())
        ov = res["overview"]
        assert ov["stock_count"] == 2  # 002428 两行按代码去重为 1 家 + 603221
        assert ov["inst_stock_count"] == 1  # reason_type 含「2家机构买入」
        # 代表行 = |净买额| 最大一条，原因合并
        top = res["net_rank"][0]
        assert top["code"] == "002428"
        assert top["net_amt"] == 1153853827.89
        assert len(top["reasons"]) == 2  # 两个上榜原因都保留

        # 游资：华鑫证券上海 → 炒股养家，买入 603221（涨停股）
        hm = {h["tag"]: h for h in res["hotmoney"]}
        assert "炒股养家" in hm
        assert hm["炒股养家"]["net_amt"] == 50000000.0
        stocks = hm["炒股养家"]["stocks"]
        assert stocks[0]["code"] == "603221"
        assert stocks[0]["is_zt"] is True
        assert stocks[0]["lb_num"] == 10
        # 拉萨系（散户通道）不进入知名游资列表
        assert "拉萨系·散户" not in hm

        # 涨停联动：603221 上榜且涨停
        assert any(x["code"] == "603221" for x in res["zt_cross"])
        # 次日关注：炒股养家 5000 万净买 + 涨停
        assert any(w["code"] == "603221" for w in res["watch"])

    def test_dedup_seat_picks_max_abs(self):
        # 同一(股票,席位)多原因 → 取 |净额| 最大一条（9066 万 > 4104 万）
        res = analyze_lhb(_daily_df(), _seat_df(), _mk_zt())
        seats = res["active_seats"]
        ght = [s for s in seats if s["seat_code"] == "10467900"]
        assert ght and ght[0]["net_amt"] == 906666399.25

    def test_empty_inputs(self):
        empty = pd.DataFrame(columns=lhb_em.LHB_DAILY_COLUMNS)
        empty_seats = pd.DataFrame(columns=lhb_em.LHB_SEAT_COLUMNS)
        res = analyze_lhb(empty, empty_seats, pd.DataFrame())
        assert res["overview"]["stock_count"] == 0
        assert res["net_rank"] == []
        assert res["hotmoney"] == []
        assert res["watch"] == []


class TestLhbPayloadContract:
    def test_payload_fields_align_prompt(self):
        ind = {
            "lhb": {
                "overview": {"stock_count": 1, "total_net_amt": 1.2e9, "total_buy_amt": 2e9,
                             "total_sell_amt": 8e8, "inst_stock_count": 1},
                "net_rank": [{"code": "002428", "name": "云南锗业", "net_amt": 1.1e9}],
                "hotmoney": [{"tag": "炒股养家", "style_cn": "打板", "net_amt": 5e7, "stocks": []}],
                "active_seats": [], "zt_cross": [], "watch": [],
            }
        }
        p = _lhb_payload(ind)
        for key in ("龙虎榜概览", "个股净买排行", "知名游资动向", "活跃席位", "涨停联动", "次日关注候选"):
            assert key in p, f"payload 缺 {key}"
        assert p["知名游资动向"][0]["tag"] == "炒股养家"

    def test_payload_empty_lhb(self):
        p = _lhb_payload({"lhb": {}})
        assert p["个股净买排行"] == []
