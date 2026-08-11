# CLAUDE.md — 项目总纲（AI 索引入口）

> 本文件是 Claude Code / AI 协作者在本仓库工作时的**总索引**。改代码、改 prompt、加战法前，先读本节与相关链接。

## 项目一句话

A 股**超短连板**收盘复盘系统：采集东方财富行情 → 结构化分析（连板梯队 / 题材运行周期 / 炸板净流入 / 龙虎榜游资）→ LLM 生成 Markdown 复盘文档，并支持问答；未来支持用户**个人战法**驱动的**次日预案**。

## 当前阶段（重要）

`v0.10.0`：**端到端复盘 + 数据看板（修复+增强）+ 交互问答 + Flask 全套工作台 + 个人战法 + 图形启动器已可用**——东财涨跌停池/资金流/概念板块采集（`data/eastmoney_pool.py`）、**龙虎榜榜单/买卖席位采集（`data/eastmoney_lhb.py`）+ 知名游资识别名单（`data/hotmoney_seats.py`，可人工增补）**、指标计算（**情绪温度 `analysis/emotion.py`** / 连板梯队 / 题材周期 / 炸板净流入 / 龙虎榜游资，`analysis/`）、**DeepSeek LLM 自动生成 Markdown 复盘报告**（`llm/`）、**数据看板 `dashboard.py`**（近 10 个交易日趋势图表单文件 HTML + 趋势摘要表 + 情绪温度成分拆解 + LLM 多日趋势解读）、**交互问答 `kb/`**（RAG 短线知识库：`prompts/**`+`docs/` 部分+`knowledge/**`+`data/strategies/**` 自动收录、混合检索【sklearn 关键词 + 可选 bge-small-zh 向量 + RRF 融合】、源文件 sha256 增量重建持续更新；6 个数据工具 function-calling 落地 `tool.datatools`）、**Web 工作台 `web/`**（Flask：战法管理 / 跑复盘看报告与次日预案 / 问答 / 数据看板）、**个人战法 → 次日预案**（`web/strategy.py` 服务 + `llm/reporter.py::generate_report(..., strategy=)` 注入；战法由用户**页面上传**落盘 gitignored `data/strategies/`，**不写死进代码/tracked 目录**）、**图形启动器**（`launcher.py` 纯核心 + `launcher_gui.py` tkinter 窗口 + 根目录 `启动.bat`/`launcher.py` + `launch` CLI 子命令 + 桌面快捷方式；一键启动 Web/复盘/看板/问答，`--dry-run` 无头自检）。管道 `pipeline.py` + CLI `review`/`dashboard`/`qa`/`web`/`launch` 子命令。报告为**七章结构**（总览 / 情绪温度 / 连板梯队 / 题材 / 炸板资金 / 龙虎榜游资 / 次日预案）。运行环境固定为 `E:/conda_envs/envs/mowan_dm/python.exe`（勿改用其他解释器）。

**Web 数据看板性能修复（v0.10）**：`/api/dashboard/view` 此前每次请求都重新联网采集约 2 分钟 → iframe 空白「不显示」。现采用三层——① 进程内 `DashboardCache`（按 交易日期+N+no_llm 缓存、最多 16 条逐出最旧、**只缓存成功**）；② 复用已生成 `output/{date}_看板.html`（仅默认 10 日）；③ 采集/指标失败 → 自包含错误兜底页（HTTP 200 进 iframe，不裸 500）。保鲜规则：历史日期定稿常新；今日盘中 10 分钟 TTL；今日 15:00 后须 15:00 后生成才有效（`_dashboard_cache_is_fresh`，`_clock()` 可测）。看板内容增强放一页：新增「近 N 日趋势摘要表」「情绪温度成分拆解」面板，iframe 随内容自适应高度，LLM 解读配置 key 默认开启，review 页新增「查看该日看板」跳转，`/api/config/llm` 探测 key。

**LLM 密钥**：`.env`（不入库，已 gitignore）里 `DEEPSEEK_API_KEY=sk-xxx`；缺 key 时 `review --no-llm` / `dashboard --no-llm` 仍可跑通数据+指标+看板。

已可用命令（在项目根目录，`PYTHONPATH=src`）：
```bash
"E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review kline --code 600000 --lmt 30
"E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review realtime --codes 600601,002398,600789
# 端到端复盘：采集→指标→LLM 报告（涨停数据 15:00 后、龙虎榜 17:30 后完整、情绪温度需近5日时序；--no-llm 跳过 LLM）
"E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review review --date 20260806
"E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review review --date 20260806 --no-llm
"E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review review            # 缺省探测最近交易日
# 指定个人战法跑复盘（战法 ID 见 Web 战法管理页 / data/strategies/ 内 front-matter；--no-llm 亦可）
"E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review review --date 20260806 --strategy strategy.user-xxx
# Web 工作台（Flask）：战法管理 / 跑复盘看报告与次日预案 / 问答 / 数据看板（默认仅本机 127.0.0.1:5000；--open 用系统浏览器打开）
"E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review web --open
# 数据看板：近 10 个交易日趋势（单文件 output/{date}_看板.html）+ LLM 多日趋势解读（--no-llm 跳过解读）
"E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review dashboard --date 20260806 --no-llm
"E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review dashboard --date 20260806 --open
"E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review dashboard          # 缺省探测最近交易日
# 交互问答：RAG 短线知识库 + 数据工具（--ask 一次性提问，缺省进 REPL；--no-embedding 纯关键词）
"E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review qa
"E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review qa --ask "什么是炸板率？" --no-embedding
"E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review qa --setup          # 安装向量检索依赖+下载 bge 模型
# 图形启动器（tkinter 窗口；双击根目录 启动.bat 或桌面快捷方式同效；--dry-run 自检不弹窗）
"E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review launch
"E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review launch --dry-run
```

## 目录地图

| 路径 | 用途 | 何时读 |
|---|---|---|
| `docs/需求分析.md` | 完整需求（模块、数据、LLM 角色、战法扩展） | 任何需求相关改动前必读 |
| `docs/数据结构.md` | 核心数据对象字段定义 | 写数据模型 / 爬虫 / 指标时 |
| `docs/东财接口清单.md` | 东财接口清单、风险、防封控 | 做数据采集时 |
| `docs/开发环境.md` | **Windows 运行环境**（解释器、激活、复现）、版本控制配套 | 任何新机器/新协作者工作前必读 |
| `docs/版本管理.md` | **Git 工作流 + 版本标注规则**（每轮对话结束提交） | 每轮对话收尾时必读 |
| `docs/战法规范.md` | 战法编写规范 | 新增/修改战法时 |
| `prompts/INDEX.md` | **Prompt 总索引**（id→文件→角色→依赖→状态） | 改任何 prompt 前必读 |
| `prompts/glossary/术语表.md` | 超短术语统一语义 | 写 prompt / 判定术语时 |
| `src/daily_review/` | Python 包（v0.9：采集层 `data/` + 指标层 `analysis/`（含 `emotion.py`）+ LLM 层 `llm/` + 问答知识库 `kb/`（corpus/manifest/embedding/index/tools/qa）+ 数据看板 `dashboard.py` + Web 工作台 `web/`（app/routes/strategy/jobs/md/templates）+ 图形启动器 `launcher.py`（纯核心）/`launcher_gui.py`（tkinter 窗口）+ 管道 `pipeline.py`） | 实现阶段 |
| `knowledge/` | 个人短线知识库（`.md` 自动入库，增量重索引持续更新；含 `README.md` 说明） | 用户维护 |

## 工作流约定

### 新增/修改一个 Prompt
1. 参考 `prompts/modules/*.md` 的现有格式，复制结构
2. 文件顶部**必须**有 YAML front-matter：`id / name / version / role / status / depends / output`
3. 术语只使用 `prompts/glossary/术语表.md` 中的定义；新术语先补进术语表
4. 在 `prompts/INDEX.md` 登记一行（字段与 front-matter 一一对应）
5. `status` 用 `draft`（草稿）或 `active`（可用）；新写的默认 `draft`

### 新增/修改一个战法（用户个人战法）
- 遵循 `docs/战法规范.md` 与 `prompts/strategies/战法模板.md`
- 每个战法一个文件放入 `prompts/strategies/`，命名 `战法-<名称>.md`
- 战法被 `prompts/modules/次日预案.md` 引用；修改战法前读该文件确认字段契约

### 术语一致性
- 所有 prompt、文档中的术语定义以 `prompts/glossary/术语表.md` 为唯一权威
- 新增术语流程：术语表 → 使用处的 prompt

### 运行环境与版本控制
- 唯一可用的解释器：`E:/conda_envs/envs/mowan_dm/python.exe`（Python 3.14.6）。**不得改用其他解释器**，详见 `docs/开发环境.md`
- 环境变更时（新装依赖/换版本）：更新 `requirements.txt` / `requirements-dev.txt` 锁定版本，并在 `docs/开发环境.md` 同步
- 新装包只允许进入 mowan_dm 环境或本项目文件夹
- `.gitignore` 已排除 `data/*/`、`output/*/`、`*.csv`、缓存目录——这些不入库

### 提交与版本（每轮对话结束时必须执行）
用户要求：**每一轮对话结束后，AI 主动做一次 git 提交 + 标注版本 + 上传**。
1. 提交前 `git status --short` 审查、`python -m pytest` 通过
2. `git add -A` + `git commit -m "<type>(<scope>): <简述>"`（格式见 `docs/版本管理.md`）
3. 按**工作量和改动大小**标注语义化版本并 `git tag`（PATCH=小改/文档，MINOR=新功能，MAJOR=架构级）
4. 同步 `pyproject.toml` 的 `version` 字段与 tag 一致
5. 远程配置后 `git push origin main --tags`；未配置则告知用户待推

## Prompt ID 命名规范

`{层级}.{模块}`，层级取值：
- `system.*`   角色级（复盘分析师、问答助手）
- `module.*`   分析模块（ladder=连板梯队 / theme=题材 / break=炸板 / plan=次日预案）
- `strategy.*` 战法库
- `tool.*`     问答模式工具
- `example.*`  示例

## 已登记 Prompt 快速视图

| ID | 文件 | 状态 |
|---|---|---|
| `system.analyst` | `prompts/system/复盘分析师.md` | draft |
| `system.assistant` | `prompts/system/问答助手.md` | draft |
| `module.emotion` | `prompts/modules/情绪温度.md` | draft |
| `module.dashboard` | `prompts/modules/数据看板.md` | draft |
| `module.ladder` | `prompts/modules/连板梯队.md` | draft |
| `module.theme` | `prompts/modules/题材周期与归类.md` | draft |
| `module.break` | `prompts/modules/炸板净流入.md` | draft |
| `module.lhb` | `prompts/modules/龙虎榜游资.md` | draft |
| `module.plan` | `prompts/modules/次日预案.md` | draft |
| `strategy.template` | `prompts/strategies/战法模板.md` | draft |
| `strategy.example` | `prompts/strategies/示例-连板接力.md` | draft |
| `tool.datatools` | `prompts/tools/数据工具schema.md` | draft |

完整信息见 [prompts/INDEX.md](prompts/INDEX.md)。

## 边界（不做 / 尚未做）

- 数据源定为**东方财富接口爬取**（非 akshare / tushare）。v0.3 已实现涨跌停池/资金流/概念板块（`data/eastmoney_pool.py`），v0.4 已实现龙虎榜/买卖席位（`data/eastmoney_lhb.py`）；**连板池（LB）、分时数据待实现**，详见 `docs/东财接口清单.md` 的状态列
- 龙虎榜盘后更新：`review` 在**下午 17:30 之后**跑才包含当日龙虎榜章节；盘中/未更新日该章节自动降级为「未更新」说明
- 运行环境固定为 `E:/conda_envs/envs/mowan_dm`；安装依赖只进该环境或本项目文件夹
- LLM 角色：**自动报告已实现**（DeepSeek，`llm/`）、**数据看板多日解读已实现**（`dashboard.py`，可 `--no-llm` 降级）、**交互问答已实现**（`kb/`，RAG 知识库 + 6 个数据工具 function-calling；向量路径可选，未装自动降级纯关键词）、**Web 工作台已实现**（`web/`，Flask，默认仅本机 `127.0.0.1:5000`，无认证勿暴露 LAN）
- 首期模块：情绪温度、连板梯队、题材运行周期与归类、炸板净流入、龙虎榜游资（已实现）
- 数据看板：近 N 日趋势图表单文件 HTML（`output/{date}_看板.html`，不入库）+ 趋势摘要表 + 情绪温度成分拆解 + LLM 多日趋势解读；历史数据缺日时该日按缺数据标记，看板照常渲染；Web 端带进程内缓存+文件复用+错误兜底（见上方 v0.10 修复说明）
- **个人战法**（v0.8 已实现）：两条路径——① tracked 种子示例 `prompts/strategies/`（`strategy.template/example`，只读，禁改/禁删，登记者 `prompts/INDEX.md`）；② **用户 UI 上传**落盘 `data/strategies/`（gitignored `data/*/` 已覆盖，**不入库**），id 自动 `strategy.user-<sha256(name)[:10]>`，驱动 `review --strategy <id>` 与 Web 复盘任务的次日预案（`module.plan` + 战法正文注入）
- 未来：策略回测 / 更多数据工具 / 登录鉴权（工作台目前仅 localhost 无认证）
