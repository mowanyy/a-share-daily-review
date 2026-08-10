---
id: tool.datatools
name: 数据工具 Schema
version: 0.2.0
role: tool
status: active
depends: []
output: 问答模式可用工具的 JSON Schema（function-calling 契约）
---

# 数据工具 Schema（问答模式）

> 问答模式下，问答助手通过以下工具**按需获取数据**再作答。工具返回的字段遵循 [docs/数据结构.md](../../docs/数据结构.md)。
> 本文件定义函数契约（名称、入参、返回结构），实现层由 `kb/tools.py`（v0.7）落地：工具名即 function-calling 的 function name，返回按契约字段紧凑 JSON。

## 工具清单

### 1. `query_zt_pool` — 查询涨停池
```
入参:
  trade_date: string "YYYYMMDD"   # 交易日，缺省=最近交易日
返回: 涨停股列表 LimitUpStock[]
      字段: code name lb_num first_limit_time last_limit_time
            open_times seal_amount turnover amount industry concepts
```

### 2. `query_zb_pool` — 查询炸板池
```
入参:
  trade_date: string "YYYYMMDD"
返回: 炸板股列表 BreakStock[]
      字段: code name break_times first_seal_time last_break_time up_pct concepts
```

### 3. `query_moneyflow` — 查询个股资金流
```
入参:
  code: string "600001"
  trade_date: string "YYYYMMDD"
返回: MoneyFlow
      字段: code name main_net_inflow super_net_inflow big_net_inflow
            total_inflow total_outflow
```

### 4. `query_ladder_stats` — 查询连板统计（含晋级率）
```
入参:
  trade_date: string "YYYYMMDD"
返回: LadderStats
      字段: zt_count lianban_count max_lb max_lb_stock break_count break_rate
            promotion: {"1进2": 0.35, "2进3": 0.4, ...}
```

### 5. `query_theme` — 查询题材归类与阶段
```
入参:
  trade_date: string "YYYYMMDD"
  theme_name?: string   # 指定题材，缺省返回全部
返回: Theme[] 或 Theme
      字段: theme_name member_count stage leader members[]
```

### 6. `query_themes_timeline` — 查询题材多日时序（判周期用）
```
入参:
  theme_name: string
  days: int  # 近 N 日，缺省 5
返回: 每日期：member_count max_lb leader
```

## 工具返回约定

1. 字段名统一 snake_case，与 `docs/数据结构.md` 一致
2. 无数据返回空列表 `[]` 或 `null`，**不允许**编造
3. 交易日参数为 `YYYYMMDD`；「最近交易日」由交易日历工具解析
4. 每个工具返回附带 `trade_date`（实际数据日期），便于核对

## 实现备注（v0.7）

- 工具名称、入参即为问答模式 function-calling 的 function name / parameters
- 实现于 `src/daily_review/kb/tools.py`：按交易日 memo 复用管道采集/指标（同一会话不重复抓取）
- 结果用紧凑 JSON 返回；异常/未知工具返回 `{"error": ...}`，问答助手如实说明，不编造
