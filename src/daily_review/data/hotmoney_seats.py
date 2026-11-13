"""知名游资营业部名单（人工维护，供龙虎榜游资识别）。

匹配规则：对营业部全名/简称做**子串匹配**（关键词命中即视为该游资席位）。
关键词尽量用**路名/区域**，对券商改名（如 国泰君安→国泰海通）免疫。
新增/修正时按「更具体的放前面」追加；误配时把关键词改得更具体。

风格标签（style）：
  daban   打板（连板接力/首板/龙头）
  trend   趋势/大资金（低吸半路）
  quant   量化
  inst    机构专用
  retail  散户通道（拉萨系，用于排除而非游资）
"""

HOTMONEY_SEATS: list[dict] = [
    # 炒股养家：华鑫证券上海宛平南路（老席位）→ 华鑫证券上海分公司（新）
    {"tag": "炒股养家", "style": "daban", "keywords": ["华鑫证券上海", "上海宛平南路"]},
    # 章盟主：国泰君安（现国泰海通）上海江苏路
    {"tag": "章盟主", "style": "trend", "keywords": ["上海江苏路"]},
    # 赵老哥：中国银河证券绍兴
    {"tag": "赵老哥", "style": "daban", "keywords": ["银河证券绍兴"]},
    # 小鳄鱼：财通证券杭州上塘路（次新/接力）
    {"tag": "小鳄鱼", "style": "daban", "keywords": ["财通证券杭州上塘路"]},
    # 宁波敢死队：光大证券宁波解放南路 / 甬江大道
    {"tag": "宁波敢死队", "style": "daban", "keywords": ["宁波解放南路", "宁波甬江大道"]},
    # 宁波系：国盛证券宁波桑田路
    {"tag": "宁波系", "style": "daban", "keywords": ["宁波桑田路"]},
    # 佛山系：招商证券佛山季华五路（低位首板）
    {"tag": "佛山系", "style": "daban", "keywords": ["佛山季华"]},
    # 成都帮：华泰证券成都南一环路 / 国泰君安成都北一环路
    {"tag": "成都帮", "style": "daban", "keywords": ["成都南一环路", "成都北一环路"]},
    # 量化打板：中信证券上海溧阳路（活跃量化/游资）
    {"tag": "量化打板", "style": "quant", "keywords": ["上海溧阳路"]},
    # 机构专用
    {"tag": "机构专用", "style": "inst", "keywords": ["机构专用"]},
    # 拉萨系：东方财富证券拉萨 X 路（散户通道，非游资）
    {"tag": "拉萨系·散户", "style": "retail", "keywords": ["拉萨"]},
]

_STYLE_CN = {
    "daban": "打板",
    "trend": "趋势",
    "quant": "量化",
    "inst": "机构",
    "retail": "散户",
}


def match_hotmoney(seat_name: str) -> dict | None:
    """匹配营业部名 → {tag, style}；无命中返回 None（未知席位）。"""
    if not seat_name:
        return None
    for item in HOTMONEY_SEATS:
        for kw in item["keywords"]:
            if kw in seat_name:
                return {"tag": item["tag"], "style": item["style"]}
    return None


def seat_style_cn(style: str) -> str:
    return _STYLE_CN.get(style, style)
