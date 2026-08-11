# 每日复盘 · 超短连板复盘系统

> 基于 Python 的 A 股收盘后复盘工具，聚焦**超短连板**风格：情绪温度、连板梯队、题材运行周期、炸板资金、龙虎榜游资，并为「个人战法 → AI 次日预案」预留扩展位。

**当前阶段**：`v0.12 · 看板溢出修复 + 资金流缺失补齐 · 多模型协作热点复盘 · Flask 全套工作台 + 个人战法 + 图形启动器已可用`（东财池子/资金流/龙虎榜/概念板块采集 → 情绪温度/连板梯队/题材/炸板/游资指标 → DeepSeek LLM 生成 Markdown 复盘报告；**多模型协作**：独立热点模型【模型 B，`HOTSPOT_MODEL` 可选】基于已采集概念板块行情提炼当日热点主线简报，注入主分析师的 一总览/四题材/七预案 三章节撰写，复盘贴合当天热点；近 10 个交易日趋势数据看板单文件 HTML【趋势摘要表 + 情绪温度成分拆解 + LLM 多日解读，Web 端带进程内缓存/文件复用/错误兜底，秒开不空白】；**看板修复（v0.12）**：涨停家数多时弱封列不再溢出被裁【截断前 5 只 + 表格横向滚动兜底 + iframe 窗口缩放自动重测高度】；**资金流补齐（v0.12）**：当日炸板股大单/主力资金流不再缺（clist Top-100 截断 → 缺失代码 fflow 单股补齐，实测 17/17 全有）；**交互问答**：RAG 短线知识库【混合检索 + 持续更新】+ 数据工具 function-calling；**Web 工作台**：战法管理（页面上传个人战法，落盘 `data/strategies/` 不入库）+ 页面跑复盘看全文报告与次日预案 + 问答 + 数据看板；**图形启动器**：双击 `启动.bat` 或桌面快捷方式「每日复盘」打开 tkinter 窗口，一键启动 Web 工作台 / 跑复盘 / 数据看板 / 交互问答）

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
├── 启动.bat            # 图形启动器入口（双击运行，无控制台闪窗）
├── launcher.py         # 启动器根引导脚本（桌面快捷方式指向它；自插入 src 到 sys.path）
├── docs/               # 需求与设计文档
│   ├── 需求分析.md     # 完整需求分析
│   ├── 数据结构.md     # 数据对象定义
│   ├── 东财接口清单.md  # 东财接口与风险
│   ├── 开发环境.md     # Windows 运行环境与依赖复现
│   └── 战法规范.md     # 战法编写规范（预留）
├── prompts/            # ★ Prompt 工程核心
│   ├── INDEX.md        #   Prompt 总索引
│   ├── system/         #   角色级 prompt（复盘分析师 / 问答助手）
│   ├── modules/        #   分析模块 prompt（情绪温度/数据看板/连板梯队/题材/炸板/龙虎榜/次日预案）
│   ├── strategies/     #   战法库（可插拔）
│   ├── examples/       #   Few-shot 示例
│   ├── glossary/       #   术语表
│   └── tools/          #   问答模式工具定义（tool.datatools 契约）
├── knowledge/          # 个人短线知识库（.md 自动入库，持续更新；含 README 说明）
├── src/daily_review/   # Python 包
│   ├── data/           #   采集层：eastmoney_pool（池子/资金流/板块）eastmoney_lhb（龙虎榜）hotmoney_seats（游资名单）eastmoney sina repo http_client
│   ├── analysis/       #   指标层：emotion（情绪温度）ladder（连板梯队）theme（题材周期）break_flow（炸板资金）lhb（游资分析）
│   ├── llm/            #   LLM 层：client（DeepSeek，含 function-calling）reporter（模块 prompt 组装报告）
│   ├── kb/             #   问答知识库：corpus（切块）manifest（增量）embedding（向量可选）index（检索）tools（数据工具）qa（会话）
│   ├── web/            #   Web 工作台（v0.8，Flask）：app（create_app）routes（页面+API）strategy（战法服务）jobs（后台复盘任务）md（Markdown 渲染）templates/
│   ├── launcher.py     #   图形启动器纯核心（v0.9）：resolve_runtime / build_*_argv / 快捷方式 / 自检（不 import tkinter，可离线单测）
│   ├── launcher_gui.py #   图形启动器窗口（v0.9，唯一 import tkinter 的文件）
│   ├── dashboard.py    #   数据看板：近 N 日趋势图表（单文件 HTML）+ LLM 多日解读
│   ├── pipeline.py     #   管道：采集 → 指标 → 报告
│   └── cli.py          #   CLI：kline / realtime / review / dashboard / qa / web / launch
├── data/               # 数据缓存 data/{YYYYMMDD}/*.csv（不入库）
├── output/             # 复盘报告 output/{YYYYMMDD}_复盘.md、数据看板 output/{YYYYMMDD}_看板.html（不入库）
├── models/             # 向量模型 bge-small-zh-v1.5（qa --setup 下载，不入库）
├── tests/              # 测试（离线：池子解析 / 指标 / prompt 一致性 / 知识库与问答）
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
# 缺省探测最近交易日（涨停数据收盘 15:00 后、龙虎榜 17:30 后完整）
PYTHONPATH=src "E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review review

# ★ 数据看板：近 10 个交易日趋势（自包含单文件 HTML，纯 JS + 内联 SVG，浏览器直接打开）
PYTHONPATH=src "E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review dashboard --date 20260806 --no-llm
# 缺省探测最近交易日 + 用系统浏览器打开 + 附 LLM 多日趋势解读
PYTHONPATH=src "E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review dashboard --open

# ★ 交互问答（v0.7）：RAG 短线知识库 + 数据工具（--ask 一次性提问，缺省进 REPL）
PYTHONPATH=src "E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review qa
PYTHONPATH=src "E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review qa --ask "什么是炸板率？" --no-embedding
PYTHONPATH=src "E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review qa --setup   # 安装向量检索依赖并下载 bge 模型

# ★ Web 工作台（v0.8，Flask）：战法管理 + 跑复盘看报告/次日预案 + 问答 + 数据看板
PYTHONPATH=src "E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review web --open   # 默认 127.0.0.1:5000，用系统浏览器打开

# ★ 图形启动器（v0.9，tkinter 窗口）：一键启动 Web 工作台 / 跑复盘 / 数据看板 / 交互问答
# 双击项目根目录 启动.bat（或桌面快捷方式「每日复盘」）即可；命令行等价：
PYTHONPATH=src "E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review launch
PYTHONPATH=src "E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review launch --dry-run   # 自检：打印环境与各子命令，不弹窗
```

> **LLM 密钥**：首次使用前在项目根目录 `.env` 写入 `DEEPSEEK_API_KEY=sk-xxx`（`.env` 已被 gitignore，不入库）。数据自动落盘到 `data/{YYYYMMDD}/`，报告输出到 `output/{YYYYMMDD}_复盘.md`，数据看板输出到 `output/{YYYYMMDD}_看板.html`。
>
> **问答知识库**：`qa` 自动收录 `prompts/**` + `docs/` 部分 + `knowledge/`（往 `knowledge/` 放 `.md` 即自动增量重索引，详见 [knowledge/README.md](knowledge/README.md)）；向量检索为可选增强，未装时自动降级纯关键词。

---

## 🧩 三大核心原则

1. **索引优先**：一切新增内容必须登记到对应索引（CLAUDE.md / README.md / prompts/INDEX.md）
2. **语义统一**：术语定义以 `prompts/glossary/术语表.md` 为准
3. **战法可插拔**：个人战法写入 `prompts/strategies/`，AI 据此生成次日预案
