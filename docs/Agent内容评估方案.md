# Agent 内容评估方案（L0/L1：确定性校验 + 数据回比）

> 状态：draft 提案（v0.37 候选，本期仅出方案不改代码）｜ 编写日期：2026-09-09 ｜ 关联版本：v0.36.3
> 一句话：**不做 LLM 互评**，用「免费、秒级、可全量」的确定性机器校验为 Agent 生成内容把关——
> L0 结构与纪律校验 + L1 生成内容与权威数据回比。

---

## 一、背景与目标

### 1.1 为什么需要评估

本项目有多个 LLM 生成入口（详见 AGENTS.md）：复盘报告「一、总览」「七、次日预案」章节（`llm/reporter.py`）、隔夜预案与开盘策略（`llm/premarket.py`）、QA 问答（`kb/qa.py`）、基金经理分析（`web/fund_agent.py`）、多 Agent 会诊（`web/agent_registry.py`）。LLM 生成内容存在四类风险：

1. **事实性错误**：数字与当日真实行情不符（涨停家数、连板高度、情绪温度、炸板率等被写错）；
2. **纪律违反**：prompt 要求「数据缺失时必须输出『数据缺失：{说明}』」「数值必须逐字引用」等，LLM 可能不遵守；
3. **结构残缺**：章节缺失、输出被截断、非交易日生成了报告；
4. **合规风险**：越过「不构成投资建议」边界、出现荐股话术（已有 `is_compliance_risk` 可兜底扫描）。

历史上出过真实事故：v0.31.1 开盘策略引用了「两天前」的涨停池、v0.30 前同一历史日期情绪温度被算出 59/66 两个矛盾值。评估系统就是把这些事故变成**机器可检出、可回归**的防线。

### 1.2 方案取舍：为什么不做 LLM 互评

- LLM-as-judge 成本高（每次评估都要再调 LLM、烧 API 额度）、延迟高、且 judge 自身会幻觉——「用幻觉评幻觉」；
- 本项目的独特优势是**生成内容的输入全部是本地结构化数据，且有权威快照锚定**（`data/review_snapshots/{date}.json`，v0.30），事实类错误几乎全部可以**免费、确定性**地检出，无需 LLM；
- 因此本方案只做两层确定性校验（L0 + L1），零 LLM 调用、零网络请求。LLM 互评、人工反馈、预案次日验证回测列为边界外（见第九节）。

### 1.3 评估对象与范围

| 档位 | 内容 | 是否评估 | 说明 |
|---|---|---|---|
| A. 强约束结构化 | 复盘第 2~6 章（代码直接拼接） | **不评** | 非 LLM 生成，无自由发挥 |
| B. 半约束摘要 | 复盘「一、总览」「七、次日预案」、隔夜预案、开盘策略 | **评估**（本期核心） | LLM 生成，有结构化注入 + 输出纪律 |
| C. 自由生成 | QA 回答、基金经理分析、多 Agent 会诊 | 评估（可选扩展） | 本期以 B 档为主，C 档规则后续补充 |
| D. 纯确定性 | 数据看板（无 LLM） | **不评** | 无 LLM 参与 |

## 二、总体架构

```
output/{date}_复盘.md / _隔夜预案.md / _开盘策略.md
        │
        ▼
┌─ eval 包（src/daily_review/eval/）──────────────────────┐
│  extract.py   章节感知解析 + 数字抽取（约数归一化）       │
│  checks.py    L0 确定性规则（纯函数）                    │
│  verify.py    L1 回比：抽取值 vs 权威源                   │
│  report.py    EvalReport 汇总（pass/fail/skip + 差值明细）│
└──────────────────────┬──────────────────────────────────┘
                       ▼
        权威源：review_snapshots/{date}.json
                data/{date}/*.csv、trade_calendar
                       ▼
        audit.db 新增 evaluations 表（可追溯、看趋势）
                       ▼
        CLI `eval` 子命令 / （可选）生成后自动 hook
```

**触发方式**：
- **手动**：`python -m daily_review eval --date 20260806 --type review [--json]`；
- **可选自动 hook**：cli/jobs/push 的 review 分支生成报告后自动跑一次，**失败仅告警不阻断主流程**（与 v0.30 快照保存失败的策略一致）；默认 `EVAL_AUTO_HOOK=False` 关闭，避免影响推送稳定性。

## 三、L0 确定性校验规则清单

纯函数、秒级、零成本，每次评估必跑。每条规则输出一个 `CheckResult(id, level, passed, message)`。

| ID | 规则 | 判定逻辑 | 权威/工具 |
|---|---|---|---|
| EVAL-001 | 产物存在且非空 | 目标 md 文件存在、正文长度 > 0 | `settings.output_dir` |
| EVAL-002 | 章节结构完整 | 复盘须含「一、总览」~「七、次日预案」全部标题；预案/开盘策略须含约定章节 | 正则匹配 `^## ` 标题 |
| EVAL-003 | 交易日合法性 | `trade_date` 为合法交易日（非周末、在交易日历内） | `trade_calendar.is_trade_date` |
| EVAL-004 | 数据缺失标注纪律 | 若权威数据缺失（如当日无龙虎榜），正文必须出现「数据缺失」字样，不得静默省略或编造 | 快照字段缺失性判断 |
| EVAL-005 | 合规扫描 | 正文不得命中交易建议关键词（推荐/买入/卖出/持仓/荐股等） | 复用 `web.feishu_gateway.is_compliance_risk` / `COMPLIANCE_REPLY` 同款话术 |
| EVAL-006 | 正文长度下限 | 去标题后正文 ≥ `EVAL_MIN_BODY_LEN`（默认 500 字），低于则 warn | — |

## 四、L1 数据回比规则

把 LLM 文本里出现的**关键数字/事实**抽取出来，与权威源逐项比对。权威源优先顺序：`review_snapshots/{date}.json`（v0.30 权威快照）→ `data/{date}/*.csv`。**快照缺失的字段一律 skip，不误报**。

### 4.1 字段抽取对照表

| ID | 字段 | 抽取方式（章节感知） | 权威源（snapshot.indicators） | 比对规则 |
|---|---|---|---|---|
| EVAL-101 | 情绪温度分数 | 「一、总览」/「二、情绪温度」中 `(\d+(\.\d+)?)\s*分` | `emotion` 的温度分值（字段名实现时以 `analysis/emotion.py` 输出对齐） | 绝对差 ≤ `EVAL_EMOTION_ABS_TOL`(1.0) |
| EVAL-102 | 涨停家数 | 「涨停 (\d+) 家」 | `ladder.zt_count` | 相对误差 ≤ 5% |
| EVAL-103 | 连板家数 | 「连板 (\d+) 家」 | `ladder.lianban_count` | 相对误差 ≤ 5% |
| EVAL-104 | 首板家数 | 「首板 (\d+) 家」 | `ladder.first_board_count` | 相对误差 ≤ 5% |
| EVAL-105 | 最高板高度 | 「最高板 (\d+) 板」/「空间板 (\d+) 板」 | `ladder.max_lb` | 精确相等 |
| EVAL-106 | 空间板龙头 | 「(\d+) 板（([0-9]{6})?\s*名称）」 | `ladder.max_lb_stock` | 名称包含匹配 |
| EVAL-107 | 炸板率 | 「炸板率 (\d+(\.\d+)?)%」 | `ladder.break_rate`（注意 20.2% vs 0.202 单位换算） | 相对误差 ≤ 5% |
| EVAL-108 | 晋级率 | 「(\d+)进(\d+).*?(\d+(\.\d+)?)%」 | `ladder.promotion` 字典（如 `{"1进2": 0.1268}`） | 相对误差 ≤ 5% |
| EVAL-109 | 昨日情绪温度（预案/开盘策略） | 引用「昨日情绪温度 (\d+(\.\d+)?)」 | 前一日 `review_snapshots/{prev_date}.json`（v0.30 规则） | 绝对差 ≤ 1.0 |
| EVAL-110 | 龙虎榜净买入（可选） | 「净买入 .*?(\d+(\.\d+)?)(亿|万)」 | `lhb` 结构（实现时对齐） | 相对误差 ≤ 5% |
| EVAL-111 | 题材龙头（可选） | 题材表中「龙头（代码 名称）」 | `themes[].leader.{code,name}` | 名称包含匹配 |

> 说明：`ladder`/`themes` 的键名以 `tests/test_reporter_payload.py` 的 fixtures 为准（`zt_count/lianban_count/max_lb/max_lb_stock/break_count/break_rate/first_board_count/promotion`、`themes[].theme_name/member_count/max_lb/stage/leader`）；`emotion`/`lhb` 的键名在实现时以 `analysis/` 实际输出对齐，本表先给语义约定。

### 4.2 数字抽取与归一化

- 支持格式：整数、小数、百分比（`20.2%`）、带单位（`79 家`、`10 板`）；
- **约数归一化**：LLM 可能写「约 80 只」「近 20%」，抽取时剥离 `约/近/大约/约莫/差不多` 等前缀后再比对；
- **单位换算**：`0.202` 与 `20.2%` 视作同一数值；
- 百分比 vs 分数（`73.3 分` vs `73.3%`）须区分语境，按字段的抽取上下文限定；
- 一节出现多个候选时取**首次出现**并记录位置，供人工复核。

### 4.3 容差参数（集中为常量，可 env 覆盖）

```python
EVAL_NUM_REL_TOL = 0.05      # 数值相对误差容差
EVAL_EMOTION_ABS_TOL = 1.0   # 情绪温度绝对差容差（分）
EVAL_MIN_BODY_LEN = 500      # L0-006 正文长度下限（字符）
EVAL_AUTO_HOOK = False       # 生成后自动评估开关（默认关）
```

## 五、模块设计（`src/daily_review/eval/`）

```
src/daily_review/eval/
├── __init__.py     # evaluate_report(trade_date, report_type) 入口 + EvalReport/CheckResult 数据类
├── checks.py       # L0 确定性规则（纯函数：结构/日期/纪律/合规/长度）
├── extract.py      # 章节感知解析 + 数字抽取（约数归一化、单位换算）
└── verify.py       # L1 回比（抽取值 vs load_review_snapshot / CSV）
```

核心接口（示意签名）：

```python
@dataclass
class CheckResult:
    id: str          # 如 "EVAL-101"
    level: str       # "error" | "warn" | "skip"
    passed: bool
    message: str     # 差值明细 / 原因

@dataclass
class EvalReport:
    trade_date: str
    report_type: str          # review | plan | open
    checks: list[CheckResult]
    @property
    def pass_count(self) -> int: ...
    @property
    def fail_count(self) -> int: ...
    @property
    def skip_count(self) -> int: ...

def evaluate_report(trade_date: str, report_type: str = "review") -> EvalReport: ...
#  1. 定位产物文件（settings.output_dir / f"{trade_date}_{后缀}.md"）
#  2. checks.py 跑 L0 全量规则
#  3. verify.py 跑 L1 回比（快照缺失 → skip）
#  4. 汇总 EvalReport；AuditDB.log_evaluation(...) 入库
```

**复用现有资产（零新增依赖）**：`load_review_snapshot`（`analysis/review_snapshot.py`）、`get_settings`（`config.py`）、`trade_calendar.is_trade_date`、`is_compliance_risk`（`web/feishu_gateway.py`，纯函数无 lark 依赖）、`AuditDB`（`web/audit.py`）。

**CLI 子命令**（与 `kline`/`review` 同级，`cli.py` 现有 `add_subparsers` 模式）：

```bash
"E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review eval [--date 20260806] [--type review|plan|open] [--json]
```

- 缺省 `--date`：探测最近交易日（复用现有探测逻辑）；
- `--json`：输出结构化 JSON（供脚本/CI 消费），否则打印人类可读摘要；
- 退出码：`0` = 无 error 级失败；`1` = 存在 error 级失败（可接入 CI）。

## 六、结果入库与展示

`audit.db` 新增 `evaluations` 表（与现有 messages/anomalies/errors/tool_calls 同模式，按 db_path 独立缓存连接、线程安全）：

```sql
CREATE TABLE IF NOT EXISTS evaluations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date  TEXT NOT NULL,
    report_type TEXT NOT NULL,
    checks_json TEXT NOT NULL,          -- 全部 CheckResult 明细
    pass_count  INTEGER NOT NULL DEFAULT 0,
    fail_count  INTEGER NOT NULL DEFAULT 0,
    skip_count  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
```

- `AuditDB` 新增方法：`log_evaluation(...)`、`recent_evaluations(limit)`；
- （可选）Web `/audit` 页新增「评估记录」tab，复用现有 tab 框架。

## 七、回归测试

新增 `tests/test_eval.py`，断言模式与现有离线 mock 风格一致（**不发 LLM、不发网络请求**）：

| 用例 | 内容 |
|---|---|
| 抽取单测 | 报告 md 样例 → `extract.py` 正确抽出情绪温度/涨停家数/炸板率；「约 80 只」「20.2% vs 0.202」归一化正确 |
| L0 规则 | 章节缺失 → EVAL-002 fail；非法日期 → EVAL-003 fail；含「推荐买入」→ EVAL-005 fail |
| L1 回比 | fixture 造 snapshot json + 篡改数字的报告 → 对应 EVAL-10x fail，差值明细正确 |
| skip 降级 | 无快照日期 → 回比项全部 skip、不误报 |
| **golden set** | 真实历史数据 `output/20260806_复盘.md` + `data/review_snapshots/20260806.json` → `evaluate_report` 全部 error 级通过（warn/skip 白名单记录） |
| 零成本断言 | 评估路径不触发 `requests`/LLM 调用（monkeypatch 断言未调用） |

现有 598 测试必须保持通过（新增文件不触碰现有生成链路，无兼容性影响）。

## 八、验收标准

1. `pytest` 全量通过（598 + 新增）；
2. `eval --date 20260806 --type review --json` 输出结构化 JSON，情绪温度/涨停/连板/炸板率等关键字段与快照一致 → 通过；
3. 构造负例（篡改报告数字）→ 对应 EVAL-10x 正确 fail；
4. 无快照日期 → skip 不误报；
5. 评估链路零 LLM 调用、零网络请求（有测试断言）。

## 九、边界与未来（本期不做）

- **LLM 互评**：成本高、judge 自幻觉，已明确排除；
- **人工反馈回路**：飞书推送卡片反馈入口、Web 报告页打分/纠错 → 审计库（依赖 UI 改动，另立需求）；
- **预案次日验证回测**：用次日实际涨跌统计「开盘策略机会股」表现（需累积数据 + 回测模块）；
- **C 档自由生成评估**：QA/基金经理/会诊的规则化评估（当前以 B 档为主，C 档规则后续补充）。

## 十、实施影响与版本

- **影响面**：仅新增 `src/daily_review/eval/` 包 + `cli.py` 一个子命令 + `web/audit.py` 一个表；**不改** `reporter.py`/`premarket.py`/`pipeline.py` 等任何生成链路；
- **版本规划**：实施时按 MINOR 升 `v0.37.0`（新功能），并同步 `pyproject.toml`、AGENTS.md/CLAUDE.md「当前阶段」；本期仅文档，不动版本。
