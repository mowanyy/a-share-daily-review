# 每日复盘 · 超短连板复盘系统

> 基于 Python 的 A 股收盘后复盘工具，聚焦**超短连板**风格：连板梯队、题材运行周期、炸板资金，并为「个人战法 → AI 次日预案」预留扩展位。

**当前阶段**：`v0.3 · 端到端复盘已可用`（东财池子/资金流采集 → 连板梯队/题材/炸板指标 → DeepSeek LLM 生成 Markdown 复盘报告；问答模式与个人战法下期）

---

## 📂 文档导航

| 位置 | 内容 | 读者 |
|---|---|---|
| [docs/需求分析.md](docs/需求分析.md) | 完整需求分析（业务定义 / 模块 / 数据 / 角色设计） | 所有人 |
| [docs/数据结构.md](docs/数据结构.md) | 核心数据对象定义（涨停/连板/题材/资金流） | 开发 |
| [docs/东财接口清单.md](docs/东财接口清单.md) | 所需东方财富接口、字段、风险与防封控策略 | 开发 |
| [docs/开发环境.md](docs/开发环境.md) | Windows 运行环境、激活方式、依赖复现（版本控制配套） | 开发 / 新机器 |
| [docs/版本管理.md](docs/版本管理.md) | Git 工作流、版本标注规则（每轮对话结束提交） | 所有人 |
| [docs/战法规范.md](docs/战法规范.md) | 个人战法的编写规范（下期启用） | 用户 + AI |
| [prompts/INDEX.md](prompts/INDEX.md) | **Prompt 总索引**（导航表） | 所有人 |
| [prompts/glossary/术语表.md](prompts/glossary/术语表.md) | 超短黑话统一语义 | AI |
| [CLAUDE.md](CLAUDE.md) | AI/Claude Code 索引入口与扩展指南 | AI |

---

## 🗺 目录结构

```
每日复盘/
├── CLAUDE.md           # AI 索引入口
├── README.md           # 人类索引入口
├── docs/               # 需求与设计文档
│   ├── 需求分析.md     # 完整需求分析
│   ├── 数据结构.md     # 数据对象定义
│   ├── 东财接口清单.md  # 东财接口与风险
│   ├── 开发环境.md     # Windows 运行环境与依赖复现
│   └── 战法规范.md     # 战法编写规范（预留）
├── prompts/            # ★ Prompt 工程核心
│   ├── INDEX.md        #   Prompt 总索引
│   ├── system/         #   角色级 prompt（复盘分析师 / 问答助手）
│   ├── modules/        #   分析模块 prompt（连板梯队/题材/炸板/次日预案）
│   ├── strategies/     #   战法库（可插拔）
│   ├── examples/       #   Few-shot 示例
│   ├── glossary/       #   术语表
│   └── tools/          #   问答模式工具定义
├── src/daily_review/   # Python 包
│   ├── data/           #   采集层：eastmoney_pool（池子/资金流/板块）eastmoney sina repo http_client
│   ├── analysis/       #   指标层：ladder（连板梯队）theme（题材周期）break_flow（炸板资金）
│   ├── llm/            #   LLM 层：client（DeepSeek）reporter（模块 prompt 组装报告）
│   ├── pipeline.py     #   管道：采集 → 指标 → 报告
│   └── cli.py          #   CLI：kline / realtime / review
├── data/               # 数据缓存 data/{YYYYMMDD}/*.csv（不入库）
├── output/             # 复盘报告 output/{YYYYMMDD}_复盘.md（不入库）
├── tests/              # 测试（离线：池子解析 / 指标 / prompt 一致性 / 载荷对齐）
├── requirements.txt    # 运行时依赖（锁定版本）
├── requirements-dev.txt# 开发依赖
└── pyproject.toml
```

---

## 🚀 快速开始

**运行环境**：`E:/conda_envs/envs/mowan_dm/python.exe`（Python 3.14 + pandas 3.0 + requests）

```bash
cd "e:/workspace/AI应用工具/每日复盘"

# 东财日 K 线（代码 600000，取 30 条）
PYTHONPATH=src "E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review kline --code 600000 --lmt 30

# 新浪实时行情（多个代码逗号分隔）
PYTHONPATH=src "E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review realtime --codes 600601,002398,600789

# ★ 端到端复盘：采集 → 指标 → DeepSeek LLM 生成 output/YYYYMMDD_复盘.md
PYTHONPATH=src "E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review review --date 20260806
# 只跑数据+指标（无 key / 调试用）
PYTHONPATH=src "E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review review --date 20260806 --no-llm
# 缺省探测最近交易日（收盘 15:00 后数据完整）
PYTHONPATH=src "E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review review
```

> **LLM 密钥**：首次使用前在项目根目录 `.env` 写入 `DEEPSEEK_API_KEY=sk-xxx`（`.env` 已被 gitignore，不入库）。数据自动落盘到 `data/{YYYYMMDD}/`，报告输出到 `output/{YYYYMMDD}_复盘.md`。

---

## 🧩 三大核心原则

1. **索引优先**：一切新增内容必须登记到对应索引（CLAUDE.md / README.md / prompts/INDEX.md）
2. **语义统一**：术语定义以 `prompts/glossary/术语表.md` 为准
3. **战法可插拔**：个人战法写入 `prompts/strategies/`，AI 据此生成次日预案
