# 每日复盘 · A 股超短连板收盘复盘系统

> 自动采集东方财富行情 → 结构化分析（连板梯队 / 题材运行周期 / 炸板净流入 / 龙虎榜游资 / 情绪温度）→ DeepSeek LLM 生成 Markdown 复盘文档；覆盖**盘后复盘 → 隔夜预案 → 开盘策略**三时段，支持交互问答、个人战法、多 Agent 通信与飞书定时推送。

---

## 📖 这是什么

一个面向 **A 股超短连板**风格的收盘复盘工具。它替你完成每日盘后繁琐的数据整理与文案工作：

- **数据采集**：东方财富涨跌停池 / 资金流 / 概念板块 / 龙虎榜 / 7×24 快讯 / 竞价行情
- **指标计算**：情绪温度（0–100 分）、连板梯队与晋级率、题材运行周期、炸板净流入、龙虎榜游资动向
- **AI 生成报告**：DeepSeek 大模型生成七章 Markdown 复盘 + 隔夜预案 + 开盘策略
- **交互问答**：RAG 知识库 + 数据工具 function-calling，可问"今天炸板率多少""什么是空间板"
- **Web 工作台**：浏览器里管理战法、跑复盘、看数据看板、问答
- **定时推送**：复盘 18:00 / 隔夜预案 07:30 用 GitHub Actions 云端推送（电脑关机也能收到）；开盘策略 09:25 用本机计划任务**秒级准点**推送（GitHub 排程会迟到，不适合竞价后即时内容）

> 💡 想了解完整需求、数据结构、接口清单等设计细节，请到 [docs/](docs/) 目录查看对应文档。

---

## ✨ 功能一览

| 能力 | 一句话 |
|---|---|
| 📊 复盘三时段 | 盘后复盘（七章报告）/ 隔夜预案（消息面）/ 开盘策略（竞价筛选个股） |
| 🤖 多模型协作 | 独立热点模型提炼当日热点，注入主分析师报告 |
| 🔌 LLM 自动兜底 | 主后端限流/失败自动切兜底重试，换平台只改 `.env` |
| 📈 数据看板 | 近 N 日趋势单文件 HTML，秒开不空白 |
| 💬 交互问答 | RAG 知识库 + 13 个数据工具 |
| 🖥️ Web 工作台 | 战法/复盘/看板/问答/基金经理/多 Agent 会诊/概念池 |
| 🎯 个人战法 | 上传战法 → 注入次日预案；战法 ↔ SKILL.md 双向桥 |
| 👥 多 Agent 通信 | QA ↔ 基金经理互相提问 + 多专家会诊 |
| 📲 飞书推送 | GitHub Actions（复盘/隔夜预案）+ 本地计划任务（开盘策略准点）推摘要到飞书 |
| 🚀 图形启动器 | tkinter 窗口一键启动 + 桌面快捷方式 |

---

## 🛠 环境要求

- **操作系统**：Windows（脚本与路径针对 Windows 测试）
- **Python**：3.10+（开发环境为 3.14）
- **依赖**：见 [requirements.txt](requirements.txt)，核心是 `pandas` + `requests` + `flask`
- **LLM 密钥**：DeepSeek 或兼容 OpenAI 协议的后端 API key（可选，无 key 仍可跑数据+看板）

---

## 📥 安装

```bash
# 1. 克隆仓库
git clone https://github.com/mowanyy/a-share-daily-review.git
cd a-share-daily-review

# 2. （推荐）创建 conda 环境
conda create -n mowan_dm python=3.14 -y
conda activate mowan_dm

# 3. 安装依赖
pip install -r requirements.txt

# 4. 安装包（让 daily_review 可导入）
pip install -e .
```

> 交互问答如需向量检索增强，额外执行 `python -m daily_review qa --setup` 安装 `sentence-transformers` 并下载 bge 模型；不装会自动降级为纯关键词检索，不影响使用。

---

## ⚙️ 配置

在项目根目录创建 `.env` 文件（已被 gitignore，不入库）：

```ini
# ── LLM 主后端（必填，想生成 AI 报告需要）──
DEEPSEEK_API_KEY=sk-你的key
LLM_BASE_URL=https://api.deepseek.com        # 官方 DeepSeek
LLM_MODEL=deepseek-chat

# ── LLM 兜底后端（可选，主后端失败时自动切换）──
DEEPSEEK_FALLBACK_API_KEY=sk-你的兜底key
LLM_FALLBACK_BASE_URL=https://api.deepseek.com
LLM_FALLBACK_MODEL=deepseek-chat

# ── 飞书定时推送（可选，仅 push 命令/GitHub Actions 用）──
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx
FEISHU_SECRET=你的加签密钥
```

> 换 LLM 平台只改 `.env` 这几个键，不改代码。详细配置说明见 [docs/开发环境.md](docs/开发环境.md)。

---

## 🚀 使用

> 以下命令在项目根目录执行。若未 `pip install -e .`，请加 `PYTHONPATH=src` 前缀。

### 1️⃣ 拉行情数据

```bash
# 东财日 K 线（--klt 101=日 102=周 103=月）
python -m daily_review kline --code 600000 --lmt 30

# 新浪实时行情
python -m daily_review realtime --codes 600601,002398,600789
```

### 2️⃣ 跑复盘三时段

```bash
# 盘后复盘（18:00 后跑，生成 output/{日期}_复盘.md 七章报告）
python -m daily_review review --date 20260806
python -m daily_review review --date 20260806 --no-llm     # 只跑数据+指标，不调 LLM
python -m daily_review review                                # 缺省自动探测最近交易日

# 隔夜预案（9:00 前跑，基于昨日复盘+隔夜消息）
python -m daily_review plan --date 20260812

# 开盘策略（9:25-9:30 跑，基于竞价数据+隔夜预案筛选个股）
python -m daily_review open --date 20260812
```

### 3️⃣ 看数据看板

```bash
# 近 10 日趋势单文件 HTML（浏览器直接打开，含 LLM 多日解读）
python -m daily_review dashboard --date 20260806 --open
python -m daily_review dashboard --no-llm        # 跳过 LLM 解读
```

### 4️⃣ 交互问答

```bash
python -m daily_review qa                                   # 进入 REPL 逐轮提问
python -m daily_review qa --ask "什么是炸板率？" --no-embedding  # 一次性提问
```

### 5️⃣ 启动 Web 工作台（推荐日常使用）

```bash
python -m daily_review web --open    # 默认 127.0.0.1:5000，自动打开浏览器
```

浏览器里可：管理个人战法、一键跑复盘看全文报告、数据看板、交互问答、基金经理分析、多 Agent 会诊、概念池管理。

### 6️⃣ 图形启动器（给不想敲命令的用户）

```bash
python -m daily_review launch          # tkinter 窗口一键启动上述各功能
python -m daily_review launch --dry-run # 自检不弹窗
```

或直接双击项目根目录的 `启动.bat`（可生成桌面快捷方式）。

### 7️⃣ 飞书定时推送（可选）

```bash
# 本地手动测试一次
python -m daily_review push --type review

# 安装本机计划任务（v0.22）：09:25 开盘策略准点推送，仅周一~周五触发
python -m daily_review schedule install            # 查看将执行的命令用 --dry-run
```

- **开盘策略 09:25**：本机 `schedule install` 秒级准点推送（电脑需开机且能访问飞书）
- **复盘 18:00 / 隔夜预案 07:30**：云端推送需配置 GitHub Secrets 并启用 Actions
- 详见 [docs/飞书推送说明.md](docs/飞书推送说明.md)

---

## 📂 产出文件

| 产物 | 路径 | 说明 |
|---|---|---|
| 复盘报告 | `output/{日期}_复盘.md` | 七章 Markdown |
| 隔夜预案 | `output/{日期}_隔夜预案.md` | 消息面分析 |
| 开盘策略 | `output/{日期}_开盘策略.md` | 个股清单 |
| 数据看板 | `output/{日期}_看板.html` | 单文件 HTML |
| 行情数据 | `data/{日期}/*.csv` | 原始缓存 |

> `data/` 与 `output/` 均不入库（gitignore）。

---

## 📚 更多文档

想深入了解某一块，直接点对应文档：

| 想了解 | 看 |
|---|---|
| 完整需求与模块设计 | [docs/需求分析.md](docs/需求分析.md) |
| 数据对象字段定义 | [docs/数据结构.md](docs/数据结构.md) |
| 东方财富接口清单与防封 | [docs/东财接口清单.md](docs/东财接口清单.md) |
| 运行环境与依赖复现 | [docs/开发环境.md](docs/开发环境.md) |
| Git 工作流与版本规则 | [docs/版本管理.md](docs/版本管理.md) |
| 个人战法编写规范 | [docs/战法规范.md](docs/战法规范.md) |
| Web 移动端适配 | [docs/移动端适配方案.md](docs/移动端适配方案.md) |
| 基金风格档案使用 | [docs/基金风格skill使用说明.md](docs/基金风格skill使用说明.md) |
| 飞书推送部署 | [docs/飞书推送说明.md](docs/飞书推送说明.md) |
| Prompt 工程总索引 | [prompts/INDEX.md](prompts/INDEX.md) |
| 超短术语统一语义 | [prompts/glossary/术语表.md](prompts/glossary/术语表.md) |
| AI 协作者总索引 | [AGENTS.md](AGENTS.md) |

---

## ⚠️ 注意事项

- **龙虎榜**：盘后 18:00 后更新，此前跑 `review` 该章节会自动降级为"未更新"
- **Web 工作台**：默认仅绑 `127.0.0.1`（本机），**无认证，勿暴露到局域网**
- **数据源**：固定为东方财富接口爬取（非 akshare / tushare），接口变更可能影响采集
- **LLM 费用**：生成报告会调用 DeepSeek API，注意额度

---

## 📄 License

本项目仅供个人学习与研究使用，不构成任何投资建议。股市有风险，入市需谨慎。
