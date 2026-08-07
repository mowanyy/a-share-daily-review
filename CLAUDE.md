# CLAUDE.md — 项目总纲（AI 索引入口）

> 本文件是 Claude Code / AI 协作者在本仓库工作时的**总索引**。改代码、改 prompt、加战法前，先读本节与相关链接。

## 项目一句话

A 股**超短连板**收盘复盘系统：采集东方财富行情 → 结构化分析（连板梯队 / 题材运行周期 / 炸板净流入 / 龙虎榜游资）→ LLM 生成 Markdown 复盘文档，并支持问答；未来支持用户**个人战法**驱动的**次日预案**。

## 当前阶段（重要）

`v0.5.0`：**端到端复盘程序已可用**——东财涨跌停池/资金流/概念板块采集（`data/eastmoney_pool.py`）、**龙虎榜榜单/买卖席位采集（`data/eastmoney_lhb.py`）+ 知名游资识别名单（`data/hotmoney_seats.py`，可人工增补）**、指标计算（**情绪温度 `analysis/emotion.py`** / 连板梯队 / 题材周期 / 炸板净流入 / 龙虎榜游资，`analysis/`）、**DeepSeek LLM 自动生成 Markdown 复盘报告**（`llm/`），管道 `pipeline.py` + CLI `review` 子命令。报告为**七章结构**（总览 / 情绪温度 / 连板梯队 / 题材 / 炸板资金 / 龙虎榜游资 / 次日预案）。**尚未做**：问答模式（tool.datatools）、个人战法→次日预案（战法待用户提供）。运行环境固定为 `E:/conda_envs/envs/mowan_dm/python.exe`（勿改用其他解释器）。

**LLM 密钥**：`.env`（不入库，已 gitignore）里 `DEEPSEEK_API_KEY=sk-xxx`；缺 key 时 `review --no-llm` 仍可跑通数据+指标。

已可用命令（在项目根目录，`PYTHONPATH=src`）：
```bash
"E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review kline --code 600000 --lmt 30
"E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review realtime --codes 600601,002398,600789
# 端到端复盘：采集→指标→LLM 报告（涨停数据 15:00 后、龙虎榜 17:30 后完整、情绪温度需近5日时序；--no-llm 跳过 LLM）
"E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review review --date 20260806
"E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review review --date 20260806 --no-llm
"E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review review            # 缺省探测最近交易日
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
| `src/daily_review/` | Python 包（v0.5：采集层 `data/` + 指标层 `analysis/`（含 `emotion.py`）+ LLM 层 `llm/` + 管道 `pipeline.py`） | 实现阶段 |

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
- LLM 角色：**自动报告已实现**（DeepSeek，`llm/`）；**交互问答（tool.datatools）尚未做**
- 首期模块：情绪温度、连板梯队、题材运行周期与归类、炸板净流入、龙虎榜游资（已实现）
- 未来：个人战法 → 次日预案（七章结构已预留，战法待用户提供）
