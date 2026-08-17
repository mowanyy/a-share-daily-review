# 每日复盘 · A 股超短连板收盘复盘系统

> 一个面向**超短连板**风格的 Python 收盘复盘工具：自动采集东方财富行情 → 结构化分析（连板梯队 / 题材运行周期 / 炸板净流入 / 龙虎榜游资 / 情绪温度）→ LLM 生成 Markdown 复盘文档；覆盖**复盘三时段**（盘后复盘 → 隔夜预案 → 开盘策略），并支持交互问答、个人战法驱动的次日预案、**多 Agent 通信**与**定时推送到飞书**。

**当前版本**：`v0.21.2` · 360 项测试全绿

---

## ✨ 功能全景

| 能力 | 说明 |
|---|---|
| 📊 **复盘三时段** | **盘后复盘**（17:00 后，七章报告）/ **隔夜预案**（9:00 前，纯消息面）/ **开盘策略**（9:25–9:30，竞价筛选个股） |
| 🧮 **五大指标层** | 情绪温度、连板梯队、题材运行周期与归类、炸板净流入、龙虎榜游资 |
| 🤖 **多模型协作热点复盘** | 独立热点模型基于概念板块行情提炼当日热点主线，注入主分析师的总览/题材/预案三章节 |
| 🔌 **LLM 双后端自动兜底** | 商汤 SenseNova `deepseek-v4-flash`（推理模型）主用，遇 429/5xx/网络失败或思考占满预算自动切官方 DeepSeek 重试 |
| 📈 **数据看板** | 近 N 日趋势图表单文件 HTML（趋势摘要表 + 情绪温度成分拆解 + LLM 多日解读），进程内缓存 + 文件复用 + 错误兜底，秒开不空白 |
| 💬 **交互问答** | RAG 短线知识库（混合检索 + 持续更新）+ 13 个数据工具 function-calling |
| 🖥️ **Web 工作台** | Flask 全套：战法管理 / 跑复盘看报告 / 问答 / 数据看板 / 基金经理分析 / 多 Agent 会诊 / 概念池管理 |
| 🎯 **个人战法** | 页面上传落盘（不入库）→ 注入次日预案；战法 ↔ SKILL.md 双向桥 |
| 👥 **多 Agent 通信** | QA ↔ 基金经理互相提问 + 多专家会诊综合观点 |
| 📲 **飞书定时推送** | GitHub Actions 免费云端定时：17:30 复盘 / 08:30 预案 / 09:25 开盘策略，推标题+摘要到飞书群机器人 |
| 🚀 **图形启动器** | tkinter 窗口一键启动 Web/复盘/看板/问答 + 桌面快捷方式 |

---

## 📂 文档导航

| 文档 | 内容 | 读者 |
|---|---|---|
| [docs/需求分析.md](docs/需求分析.md) | 完整需求分析（业务定义 / 模块 / 数据 / 角色设计） | 所有人 |
| [docs/数据结构.md](docs/数据结构.md) | 核心数据对象定义（涨停/连板/题材/资金流） | 开发 |
| [docs/东财接口清单.md](docs/东财接口清单.md) | 东方财富接口、字段、风险与防封控策略 | 开发 |
| [docs/开发环境.md](docs/开发环境.md) | Windows 运行环境、激活方式、依赖复现 | 开发 / 新机器 |
| [docs/版本管理.md](docs/版本管理.md) | Git 工作流、版本标注规则 | 所有人 |
| [docs/战法规范.md](docs/战法规范.md) | 个人战法编写规范 | 用户 + AI |
| [docs/移动端适配方案.md](docs/移动端适配方案.md) | Web 工作台移动端断点与组件规范 | 开发 |
| [docs/基金风格skill使用说明.md](docs/基金风格skill使用说明.md) | 基金风格档案使用节奏（周一/月初触发） | 用户 |
| [docs/飞书推送说明.md](docs/飞书推送说明.md) | 飞书机器人部署 + GitHub Secrets 配置 | 用户 |
| [prompts/INDEX.md](prompts/INDEX.md) | **Prompt 总索引**（id → 文件 → 角色 → 状态） | 所有人 |
| [prompts/glossary/术语表.md](prompts/glossary/术语表.md) | 超短术语统一语义（唯一权威） | AI |
| [AGENTS.md](AGENTS.md) / [CLAUDE.md](CLAUDE.md) | AI 协作者总索引入口 | AI |

---

## 🗺 目录结构

```
每日复盘/
├── README.md           # 人类索引入口（本文件）
├── 启动.bat            # 图形启动器入口（双击运行，无控制台闪窗）
├── launcher.py         # 启动器根引导脚本（自插入 src 到 sys.path）
├── docs/               # 需求与设计文档
├── prompts/            # ★ Prompt 工程核心
│   ├── INDEX.md        #   Prompt 总索引
│   ├── system/         #   角色级 prompt（复盘分析师 / 问答助手）
│   ├── modules/        #   分析模块 prompt（情绪温度/数据看板/连板梯队/题材/炸板/龙虎榜/次日预案/隔夜预案/开盘策略/热点简报）
│   ├── strategies/     #   战法库（可插拔）
│   ├── examples/       #   Few-shot 示例
│   ├── glossary/       #   术语表
│   └── tools/          #   问答模式工具定义（tool.datatools 契约，13 个工具）
├── knowledge/          # 个人短线知识库（.md 自动入库，持续更新）
├── skills/fund-styles/ # 基金风格 skill 档案（张坤/刘格菘/丘栋荣/葛兰，仅本项目内用）
├── src/daily_review/   # Python 包
│   ├── data/           #   采集层：eastmoney_pool（池子/资金流/板块）eastmoney_lhb（龙虎榜）eastmoney_news（7x24快讯）hotmoney_seats（游资名单）eastmoney sina repo http_client local_cache
│   ├── analysis/       #   指标层：emotion（情绪温度）ladder（连板梯队）theme（题材周期）break_flow（炸板资金）lhb（游资分析）auction（竞价）
│   ├── llm/            #   LLM 层：client（DeepSeek，含 function-calling + 自动兜底）reporter（模块 prompt 组装报告）premarket（隔夜预案/开盘策略）
│   ├── kb/             #   问答知识库：corpus（切块）manifest（增量）embedding（向量可选）index（检索）tools（数据工具）qa（会话）
│   ├── web/            #   Web 工作台（Flask）：app routes strategy jobs md history skill_bridge concept_pool fund_agent agent_registry templates/
│   ├── notify.py       #   飞书群机器人 webhook 推送（加签 HMAC-SHA256）
│   ├── push.py         #   报告生成 → 摘要 → 推送（交易日/休市判定）
│   ├── dashboard.py    #   数据看板：近 N 日趋势图表（单文件 HTML）+ LLM 多日解读
│   ├── pipeline.py     #   管道：采集 → 指标 → 报告
│   ├── launcher.py     #   图形启动器纯核心（resolve_runtime / build_*_argv / 快捷方式 / 自检）
│   ├── launcher_gui.py #   图形启动器窗口（唯一 import tkinter 的文件）
│   ├── config.py       #   全局配置（.env 加载、路径、LLM、飞书）
│   └── cli.py          #   CLI：kline / realtime / review / plan / open / push / update-data / split-pool / skill / dashboard / qa / web / launch
├── .github/workflows/  # GitHub Actions 定时推送（push-review/push-plan/push-open）
├── data/               # 数据缓存 data/{YYYYMMDD}/*.csv（不入库）
├── output/             # 报告 output/{date}_复盘.md / _隔夜预案.md / _开盘策略.md / _看板.html（不入库）
├── models/             # 向量模型 bge-small-zh-v1.5（qa --setup 下载，不入库）
├── tests/              # 测试（离线：池子解析 / 指标 / prompt 一致性 / 知识库 / 问答 / Agent / 推送）
├── requirements.txt    # 运行时依赖（锁定版本）
├── requirements-dev.txt# 开发依赖
└── pyproject.toml
```

---

## 🚀 快速开始

### 运行环境

- **唯一解释器**：`E:/conda_envs/envs/mowan_dm/python.exe`（Python 3.14 + pandas 3.0 + requests）
- **工作目录**：`e:/workspace/AI应用工具/每日复盘`
- 所有命令前加 `PYTHONPATH=src` 让包可导入

### LLM 密钥配置

首次使用前在项目根目录创建 `.env`（已被 gitignore，不入库）：

```ini
# 主后端（商汤 SenseNova 托管 deepseek-v4-flash，推理模型）
DEEPSEEK_API_KEY=sk-你的主key
LLM_BASE_URL=https://token.sensenova.cn/v1
LLM_MODEL=deepseek-v4-flash

# 兜底后端（官方 DeepSeek，非推理模型，429/5xx/网络失败自动切换）
DEEPSEEK_FALLBACK_API_KEY=sk-你的兜底key
LLM_FALLBACK_BASE_URL=https://api.deepseek.com
LLM_FALLBACK_MODEL=deepseek-chat

# 飞书定时推送（仅 push 命令/GitHub Actions 用，不需要可不填）
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx
FEISHU_SECRET=你的加签密钥
```

> 换平台只改 `.env` 六键（主 + 兜底），不改代码。缺 key 时 `review --no-llm` / `dashboard --no-llm` 仍可跑通数据 + 指标 + 看板。

### 常用命令

```bash
cd "e:/workspace/AI应用工具/每日复盘"
PY="E:/conda_envs/envs/mowan_dm/python.exe"

# ─── 基础数据 ───────────────────────────────────────────────
# 东财日 K 线（--klt 101=日 102=周 103=月）
$PY -m daily_review kline --code 600000 --lmt 30 --klt 102
# 新浪实时行情
$PY -m daily_review realtime --codes 600601,002398,600789

# ─── 复盘三时段 ─────────────────────────────────────────────
# 盘后复盘（17:00 后跑）：采集→指标→DeepSeek 七章报告
$PY -m daily_review review --date 20260806
$PY -m daily_review review --date 20260806 --no-llm        # 只跑数据+指标
$PY -m daily_review review                                  # 缺省探测最近交易日
$PY -m daily_review review --date 20260806 --strategy strategy.user-xxx  # 指定个人战法

# 隔夜预案（9:00 前跑）：昨日复盘 + 东财7x24隔夜消息
$PY -m daily_review plan --date 20260812

# 开盘策略（9:25-9:30 跑）：竞价数据 + 隔夜预案 → 筛选个股
$PY -m daily_review open --date 20260812

# ─── 数据看板 ───────────────────────────────────────────────
# 近 10 日趋势单文件 HTML（趋势摘要表 + 情绪温度拆解 + LLM 解读）
$PY -m daily_review dashboard --date 20260806 --no-llm
$PY -m daily_review dashboard --open                       # 缺省 + 浏览器打开 + LLM 解读

# ─── 交互问答 ───────────────────────────────────────────────
# RAG 知识库 + 13 个数据工具 function-calling
$PY -m daily_review qa                                       # 进 REPL
$PY -m daily_review qa --ask "什么是炸板率？" --no-embedding  # 一次性提问
$PY -m daily_review qa --setup                               # 装向量检索依赖 + 下载 bge 模型

# ─── Web 工作台（Flask）────────────────────────────────────
# 战法管理 / 跑复盘看报告 / 问答 / 数据看板 / 基金经理 / 多Agent会诊 / 概念池
$PY -m daily_review web --open                              # 默认 127.0.0.1:5000

# ─── 战法 ↔ SKILL.md 双向桥 ────────────────────────────────
$PY -m daily_review skill import skills/fund-styles/深度价值-张坤型.md
$PY -m daily_review skill export strategy.user-xxx --out out.skill.md

# ─── 数据维护 ───────────────────────────────────────────────
$PY -m daily_review update-data --days 6                    # 刷新静态缓存 + 重采近6天
$PY -m daily_review split-pool                              # 供选股数据按日期切分

# ─── 飞书定时推送（GitHub Actions 用，也可本地测试）─────────
$PY -m daily_review push --type review                      # 生成复盘并推送飞书
$PY -m daily_review push --type plan --date 20260812
$PY -m daily_review push --type open

# ─── 图形启动器 ─────────────────────────────────────────────
$PY -m daily_review launch                                  # tkinter 窗口一键启动
$PY -m daily_review launch --dry-run                        # 自检不弹窗
# 或直接双击项目根目录 启动.bat（桌面快捷方式同效）
```

> **数据落盘**：行情/指标 CSV → `data/{YYYYMMDD}/`；复盘报告 → `output/{date}_复盘.md`；隔夜预案 → `output/{date}_隔夜预案.md`；开盘策略 → `output/{date}_开盘策略.md`；数据看板 → `output/{date}_看板.html`。均不入库。
>
> **问答知识库**：`qa` 自动收录 `prompts/**` + `docs/` 部分 + `knowledge/**`（往 `knowledge/` 放 `.md` 即自动增量重索引）；向量检索为可选增强，未装时自动降级纯关键词。

---

## 🧠 核心设计

### 复盘三时段

| 时段 | 时间 | 输入 | 输出 | 命令 |
|---|---|---|---|---|
| 盘后复盘 | 17:00 后 | 涨停池/炸板/资金流/龙虎榜/概念板块 → 五大指标 | 七章 Markdown（总览/情绪/梯队/题材/炸板/游资/预案） | `review` |
| 隔夜预案 | 9:00 前 | 昨日复盘 + 东财 7x24 隔夜快讯 | 消息面分析与今日关注方向 | `plan` |
| 开盘策略 | 9:25–9:30 | 竞价数据（高开/量能/昨日封单）+ 隔夜预案 | 有机会的个股清单 | `open` |

### LLM 双后端自动兜底（v0.12.1）

- **主后端**：商汤 SenseNova（`token.sensenova.cn/v1`）托管的 `deepseek-v4-flash`，是**推理模型**（回包带 `reasoning_content`，思考先占 token）
- **兜底后端**：官方 DeepSeek `deepseek-chat`（非推理模型）
- **自动切换时机**：429 限流 / 5xx / 网络错误 / 推理模型思考占满预算导致正文为空 → 自动用兜底后端重试一次
- 换平台只改 `.env` 六键，不改代码

### 多模型协作热点复盘（v0.11）

撰写复盘前先做一次独立 LLM 调用（热点模型 B，`HOTSPOT_MODEL` 可选、默认回落主模型），基于当日概念板块行情提炼 2–4 条热点主线简报，注入主分析师（模型 A）的总览/题材/预案三章节。热点调用失败降级确定性 Top-N，无概念数据逐字节保持旧行为。

### 多 Agent 通信（v0.20）

- **Agent 注册中心**（`web/agent_registry.py`）：`register()/list_agents()/call_agent()`，模块导入时自动注册 6+ Agent（QA 通用 / 4 位基金经理 / 热点简报）
- **QA → 基金经理**：QA 的 function-calling 工具新增 `query_agent`，可指定 agent_id 调用其他 Agent
- **基金经理 → QA**：基金经理分析由 `chat()` 改为 `chat_tools()`，新增 `query_qa` 工具向 QA 查市场概况，支持最多 3 轮工具循环
- **多 Agent 会诊**：Web 问答页底部面板，勾选多个 Agent → 顺序调用各自分析 → LLM 综合观点（合成失败降级拼接原始观点）

### 个人战法 → 次日预案（v0.8 / v0.17）

- 两条路径：① tracked 种子示例 `prompts/strategies/`（只读）；② **用户 UI 上传**落盘 `data/strategies/`（gitignored，不入库），id 自动 `strategy.user-<sha256(name)[:10]>`
- 驱动 `review --strategy <id>` 与 Web 复盘任务的次日预案章节
- **战法 ↔ SKILL.md 双向桥**（v0.17）：`skill import` 把外部 SKILL.md 转成战法；`skill export` 把战法导出为 SKILL.md
- **基金风格 skill 档案**（`skills/fund-styles/`）：张坤/刘格菘/丘栋荣/葛兰四套中长线定价逻辑档案，周一/月初按周K/月K 触发，供独立分析视角

### 飞书定时推送（v0.21）

- `notify.py`：飞书自定义机器人 webhook 推送（`msg_type: text`，支持加签 HMAC-SHA256）
- `push.py`：生成报告 → 确定性提取标题+摘要（不调 LLM）→ 推送；周末/休市自动跳过
- 3 个 GitHub Actions workflow 定时（北京时间，cron 转 UTC）：

| Workflow | 北京时间 | cron(UTC) | 动作 |
|---|---|---|---|
| `push-review.yml` | 17:30 | `30 9 * * 1-5` | `push --type review` |
| `push-plan.yml` | 08:30 | `30 0 * * 1-5` | `push --type plan` |
| `push-open.yml` | 09:25 | `25 1 * * 1-5` | `push --type open` |

LLM 非机密配置写死在 workflow，密钥走 GitHub Secrets。部署步骤见 [docs/飞书推送说明.md](docs/飞书推送说明.md)。

### 数据看板（v0.10）

近 N 个交易日趋势单文件 HTML（纯 JS + 内联 SVG，浏览器直接打开）：
- 趋势摘要表 + 情绪温度成分拆解 + LLM 多日趋势解读
- Web 端三层缓存：① 进程内 `DashboardCache`（只缓存成功）；② 复用 `output/{date}_看板.html`；③ 采集失败自包含错误兜底页（HTTP 200 进 iframe）
- 保鲜规则：历史日期定稿常新；今日盘中 10 分钟 TTL；今日 15:00 后须 15:00 后生成才有效

---

## 📊 报告结构（七章）

`review` 生成的 `output/{date}_复盘.md` 遵循七章结构：

1. **一、总览** —— 当日市场全貌（涨停/连板/炸板/情绪概览）
2. **二、情绪温度** —— 0–100 分量化 + 周期阶段 + 成分拆解
3. **三、连板梯队** —— 晋级率、空间板、各梯队个股
4. **四、题材运行周期与归类** —— 主线题材、阶段（启动/加速/分歧/退潮）、龙头
5. **五、炸板净流入** —— 炸板股大单/主力资金流
6. **六、龙虎榜游资** —— 知名游资动向、机构上榜
7. **七、次日预案** —— 个人战法驱动的次日操作倾向

---

## 🧩 三大核心原则

1. **索引优先**：一切新增内容必须登记到对应索引（AGENTS.md / CLAUDE.md / README.md / prompts/INDEX.md）
2. **语义统一**：术语定义以 `prompts/glossary/术语表.md` 为唯一权威
3. **战法可插拔**：个人战法写入 `data/strategies/`，AI 据此生成次日预案

---

## ⚙️ 开发约定

- **唯一解释器**：`E:/conda_envs/envs/mowan_dm/python.exe`，不得改用其他解释器
- 环境变更时更新 `requirements.txt` / `requirements-dev.txt` 并同步 `docs/开发环境.md`
- 新装包只允许进入 mowan_dm 环境或本项目文件夹，不在 workspace 之外操作
- `.gitignore` 已排除 `data/*/`、`output/*/`、`*.csv`、缓存目录
- 每轮对话结束：`pytest` 全绿 → `git add -A` + 语义化版本 tag → 同步 `pyproject.toml` version → push

---

## 🔒 边界（不做 / 尚未做）

- 数据源定为**东方财富接口爬取**（非 akshare / tushare）
- 龙虎榜盘后更新：`review` 在 **17:30 之后**跑才包含当日龙虎榜章节；盘中自动降级为「未更新」
- Web 工作台默认仅本机 `127.0.0.1:5000`，**无认证勿暴露 LAN**
- 连板池（LB）、分时数据待实现，详见 `docs/东财接口清单.md`
- 未来：策略回测 / 登录鉴权 / 更多数据工具
