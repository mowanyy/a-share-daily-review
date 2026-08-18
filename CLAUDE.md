# CLAUDE.md — 项目总纲（AI 索引入口）

> 本文件是 Claude Code / AI 协作者在本仓库工作时的**总索引**。改代码、改 prompt、加战法前，先读本节与相关链接。

## 项目一句话

A 股**超短连板**收盘复盘系统：采集东方财富行情 → 结构化分析（连板梯队 / 题材运行周期 / 炸板净流入 / 龙虎榜游资 / 情绪温度）→ LLM 生成 Markdown 复盘文档；**复盘三时段**：盘后复盘（18:00 后）→ 隔夜预案（9:00 前，消息面）→ 开盘策略（9:25-9:30，竞价筛选个股）；支持问答、个人战法驱动的次日预案、**多 Agent 通信**（QA ↔ 基金经理互相提问 + 多专家会诊）与**定时推送报告到飞书**（GitHub Actions 免费云端）。

## 当前阶段（重要）

`v0.21.7`：**push 失败/跳过也推飞书状态提示（不再无声）+ 排程延迟说明**——实测 GitHub Actions `schedule` 派发会迟到（2026-08-17 复盘排程 18:00 该跑实际 18:35，晚 35 分钟；隔夜预案 08:30 排程当日迟迟未派发），此前非 sent 状态（周末/休市跳过、生成/推送失败）飞书端完全无声（`skipped` 在 Actions 里还 `exit 0` 显绿勾），公司网络又打不开 Actions 页面，只能干等。`push.py` 新增 `_status_notice`/`_try_notify`/`_with_status_notice`：非 sent 结果也向飞书补发一条 **⏭ 跳过 / ❌ 失败** 短消息（含原因），状态提示本身发送失败静默记录、不改主状态；`docs/飞书推送说明.md` 新增「排程延迟与状态提示」小节（实测表现 + Actions 页/gh/api 三种核实方式 + 手动补发命令）。**366 测试通过**。

`v0.21.0`：**定时推送报告到飞书（GitHub Actions 免费云端）**——新增 `notify.py`（飞书群机器人 webhook 推送，支持加签 HMAC-SHA256）与 `push.py`（生成报告 → 提取标题+摘要 → 推送；摘要确定性提取不调 LLM，周末/休市自动跳过）。`config.py` 新增 `FEISHU_WEBHOOK_URL`/`FEISHU_SECRET`；CLI 新增 `push` 子命令（`push --type review|plan|open [--date]`）。新增 3 个 GitHub Actions workflow（`push-review.yml`/`push-plan.yml`/`push-open.yml`）定时：盘后复盘 18:00、盘前隔夜预案 08:30、竞价后开盘策略 09:25（北京时间，cron 转 UTC、`1-5` 避开周末）；LLM 非机密配置写死在 workflow，密钥走 GitHub Secrets（`DEEPSEEK_API_KEY`/`DEEPSEEK_FALLBACK_API_KEY`/`FEISHU_WEBHOOK_URL`）。配套 `docs/飞书推送说明.md`。**360 测试通过**。

`v0.20.0`：**多 Agent 通信框架（互相提问 + 多专家会诊）**——新增 `web/agent_registry.py`：Agent 统一注册中心（`register()`/`list_agents()`/`call_agent()`），模块导入时自动注册 6+ 个 Agent（`qa_general` 知识问答 / `fund_张坤`·`fund_刘格菘`·`fund_丘栋荣`·`fund_葛兰` 基金经理 / `hotspot_brief` 热点简报）。**① QA → 基金经理**：QA 的 function-calling 工具新增 `query_agent`（`kb/tools.py`，可指定 agent_id 调用其他 Agent，schema 契约同步 `tool.datatools` v0.3.0 13 个工具）；**② 基金经理 → QA**：`fund_agent.analyze` 由 `chat()` 改为 `chat_tools()`，新增 `query_qa` 工具（可向 QA Agent 查市场概况），支持最多 3 轮工具循环，session/中军/历史裁剪逻辑不变；**③ 多 Agent 会诊**：Web 新增 `GET /api/agents/list` 与 `POST /api/agents/consult`（选多个 Agent → 顺序调用各自分析 → 合成 LLM `_synthesize_consult` 综合观点，合成失败降级拼接原始观点），问答页底部新增「多 Agent 会诊」面板（勾选 Agent → 输入问题 → 看各观点 + 综合结论）。零外链零 CDN。**326 测试通过**。

`v0.19.0`：**基金经理 agent 上下文记忆 + 中军自动识别**——`eastmoney_pool.py` 新增 `fetch_market_caps`（clist 批量查总市值 `f20`）。`fund_agent.py` 大改：session 持久化到 `data/fund_sessions/{manager_id}.json`（`data/*/` 已 gitignore，重启不丢上下文）；`_ensure_zhongjun` 从当日涨停池 CSV 按 `industry` 分组取市值最大股作为**中军**（无 CSV/网络失败返回空，不抛异常，agent 仍可正常回答）；`analyze` 新增 `trade_date` 参数，system prompt 末尾追加中军摘要（代码 + 名称 + 题材 + 市值），上下文保留最近 10 轮对话（超过自动裁剪），LLM 失败保留历史供重试。Web 新增 `POST /api/fund/clear/<manager_id>`（清空记忆）与 `GET /api/fund/session/<manager_id>`（含 `history_length`/`zhongjun`/`updated_at`）；`POST /api/fund/analyze` 回包新增 `history_length` 与 `zhongjun`。前端改为**聊天式界面**：滚动历史对话区（每轮 user+agent 可回溯）、清空记忆按钮、实时显示中军信息（来自涨停池自动识别）。零外链零 CDN。**309 测试通过**。

`v0.18.0`：**Web 问答页「基金经理分析」栏目（独立风格 agent）**——问答页改**双栏**：左侧原知识库问答、右侧空白处新增「基金经理分析」面板。新增 `web/fund_agent.py`：`list_managers()` 扫描 `skills/fund-styles/*.md` 档案（4 个基金经理：张坤/刘格菘/丘栋荣/葛兰，下拉可选）；`analyze(manager_id, question, klt)` 按所选风格档案生成 **system prompt**（角色头 + 档案正文含「时间周期与触发时点」第 0 节 + 输出纪律），从问题里正则抠 **6 位股票代码**（去重、限 3 只、剔除日期形数字）注入真实 **周K(klt=102)/月K(klt=103)** 数据（`eastmoney.fetch_kline lmt=36`，失败一律按「数据不足」处理并告知 agent），单次 `llm.chat` 回复；LLM 失败降级返回 `error` 字段（同 QA）。Web 新增 `GET /api/fund/managers`（下拉数据）与 `POST /api/fund/analyze`（未知经理 404、空问题/非法 klt 400）；前端面板含基金经理下拉 + 周K/月K 周期下拉 + 问题输入 + 分析按钮，`md_to_html` 渲染回答、副行显示 K 线注入说明与异常。零外链零 CDN。298 测试通过。

`v0.17.1`：**基金风格周K/月K 周期锚定（周一/月初触发）**——4 个基金风格档案（`skills/fund-styles/`）各新增第 0 节「时间周期与触发时点（先读）」：本风格是**中长线定价逻辑，只在周K/月K 层面判断、不用日K 做风格决策**；**每周一 → 周K 视角**（中期趋势/买点/量能，输出本周操作倾向）、**每月 1 号 → 月K 视角**（长线趋势/估值分位/景气与政策核对，输出本月持有倾向）；各档案写明周期侧重（张坤=月K定估值周K找买点 / 刘格菘=月K定景气周K找加速 / 丘栋荣=月K定估值分位周K找左侧买点 / 葛兰=月K定景气政策周K选龙头买点），且统一「无周/月K 数据时明说数据不足、禁止用日K 冒充周月K」。`docs/基金风格skill使用说明.md` 新增「使用节奏」章节（周一/月初触发表 + 示例说法 + `kline --klt 102`周K / `--klt 103`月K 拉数命令）。CLI `kline --klt` 帮助补 103=月线。结构化回归测试锁定档案含周/月K 方法论。286 测试通过。

`v0.17.0`：**战法 ↔ SKILL.md 双向桥 + 基金风格 skill 档案（仅限本项目内）**——① 新增 `web/skill_bridge.py`：`import_skill()` 把外部 SKILL.md 一键转成个人战法（复用 `strategy.create()`/`make_id()` 链路，id=`strategy.user-<sha256(name)[:10]>`、正文原样保留、缺节走现有 `validate()` 告警不拒绝、`applies_to` 取 description 首句）；`export_strategy()` 把战法（tracked 模板/示例与 user 战法均可）导出为 SKILL.md（`name` + 自动生成 `description` + 正文原样）。Web 战法管理页新增「导入 SKILL.md」粘贴面板与每行「导出 SKILL.md」下载按钮（`POST /api/strategies/import-skill`、`GET /api/strategies/<id>/export-skill`）；CLI 新增 `skill import <file.md>` / `skill export <id> [--out]` 子命令。② 项目内新增 `skills/fund-styles/` 4 个基金风格档案（深度价值张坤型 / 景气成长刘格菘型 / 低估值丘栋荣型 / 医药成长葛兰型：风格画像→可判定指标清单→选股→买卖持有纪律→反例→输出格式），**供本项目独立分析视角使用**（中长线逻辑，区别于超短连板战法；可导入战法库后改写成短线规则再驱动预案）；`kb/corpus.py` 收录 `skills/**` 供问答检索；配套 `docs/基金风格skill使用说明.md`。**skill 只放项目内、不入用户级目录。** 282 测试通过。

`v0.16.0`：**复盘报告持久化查询——历史报告档案已可用**——复盘/隔夜预案/开盘策略每次生成已落盘 `output/{date}_*.md`，但此前 Web 只查进程内存的 `JobState.report_html`，**重启 Web 后旧报告看不到、需重新生成**，不利多日复盘挖掘因子。本版对照数据看板文件读回模式（`output/{date}_看板.html` 复用）新增 `web/history.py`：`list_reports()` 扫描 `output/` 识别四种产物（复盘/隔夜预案/开盘策略/看板，按日倒序返回 `has_*` 标记）、`load_report(date)` 读回该日复盘 md → `md_to_html()` 全文 + `section_html()` 提取「七、次日预案」章节，返回结构与 `JobState.to_dict()` 兼容（前端 `showResult` 零改动复用）。Web 新增 `GET /api/review/history`（日期列表）与 `GET /api/review/history/<date>`（该日报告详情，无文件 404）；`review.html` 控制面板新增「历史报告」日期下拉 + 查看按钮。**零改动生成逻辑**（reporter/jobs/pipeline 不动），日期正则 + `resolve().is_relative_to` 防路径穿越。268 测试通过。

`v0.15.1`：**Web 工作台移动端适配（保留）+ 局域网访问（已回退）**——① 移动端适配：`base.html` 全局媒体查询（≤767px 单列布局、导航紧凑/表单堆叠/按钮触控优化/表格可横滑 `.table-wrap`），`dashboard.py` 自包含看板适配手机端（KPI 网格列宽/字号缩放/`#trend-summary` `#emotion-comp` 加 `overflow-x:auto`），`review.html`/`concepts.html` 页面微调；新增设计规范文档 `docs/移动端适配方案.md`（9 章：设计原则/断点体系/组件规范/CSS 模式/测试方法）。② v0.15.0 曾实现 `web --lan` 快捷参数 + 启动器「局域网访问」复选框，**因当前处于公司网络、不使用网络分享，v0.15.1 已全部回退**：cli.py 移除 `--lan`、launcher.py `build_web_argv` 还原固定 `127.0.0.1`、launcher_gui.py 移除复选框；`--host` 参数保留（默认 `127.0.0.1` 仅本机，无认证不对外暴露）。257 测试通过。

`v0.14.0`：**看板数据本地化加速 + 概念池 Agent 管理**——补上 3 块静态缓存到 `data/cache/`（行业映射 `industry_map.csv` 7天TTL / 概念成分 `board_constituents/{code}.csv` 3天TTL / 交易日历 `trade_dates.csv`），省约 72s/次网络开销；新增 `data/local_cache.py`；pipeline 行业映射/概念成分、eastmoney_pool 交易日历均走缓存。新增 `update-data` CLI 命令（`python -m daily_review update-data [--days N] [--date] [--force]`，刷新缓存+重采近N天）与 Web「更新数据」按钮。新增**概念池 CRUD** 服务 `web/concept_pool.py`（落盘 `data/stock_pool/concepts/{概念名}.csv`，gitignored），Agent 通过 6 个 function-calling 工具（`concept_pool_create/delete/add_stocks/remove_stocks/list/query`）对短线题材股票池增删改查，自动同步到 `knowledge/概念池/*.md` 供 RAG 检索；Web 新增 `/concepts` 管理页。新增 `tools/stock_pool.py` 供选股数据按日期切分（`split-pool` 命令 → 218 个 `data/stock_pool/{日期}.csv`）。工具 schema 更新至 12 个。257 测试通过。

`v0.13.0`：**复盘三时段拆分**——盘后复盘（18:00 后，现有七章报告）+ **隔夜预案（9:00 前，纯消息面分析）** + **开盘策略（9:25-9:30，竞价后聚焦有机会的个股）**。新增东财 7x24 快讯采集（`data/eastmoney_news.py`，接口 `newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_{N}_{P}_.html`，响应键 `LivesList`、字段 `showtime`/`digest`/`simtype_zh`，URL 尾部需下划线）、竞价指标计算（`analysis/auction.py`，复用新浪实时行情取竞价价/量，高开幅度/竞价量能/昨日封单）、隔夜预案+开盘策略 LLM 生成（`llm/premarket.py`，两个新 prompt：`module.overnight` / `module.open_strategy`）。CLI 新增 `plan`/`open` 子命令（`python -m daily_review plan|open`），Web `/review` 页新增「隔夜预案」「开盘策略」按钮（`/api/plan/start`、`/api/open/start`）。隔夜消息多页翻取直到覆盖隔夜窗口（昨日 18:00 → 今早 9:00）。257 测试通过。

`v0.12.1`：**LLM 后端=商汤 SenseNova（主，DeepSeek V4 Flash 推理模型）+ 官方 DeepSeek 自动兜底**——新 API key 属商汤 Token Plan 平台，实测 `token.sensenova.cn` 为有效接口（`api.sensenova.cn` 404 路由不存在）。`.env` 主三件套：`DEEPSEEK_API_KEY`（新）/ `LLM_BASE_URL=https://token.sensenova.cn/v1` / `LLM_MODEL=deepseek-v4-flash`（仅此模型名可用，`deepseek-v4` 报 model not found）；**兜底三件套**：`DEEPSEEK_FALLBACK_API_KEY`（官方旧 key）/ `LLM_FALLBACK_BASE_URL=https://api.deepseek.com` / `LLM_FALLBACK_MODEL=deepseek-chat`。该模型是**推理模型**（回包带 `reasoning_content`，思考先占 token）且 **Token Plan 免费额度有调用频率上限（实测 429）** → 四个小预算调用点 `max_tokens` 由 500/600/500/1500 上调至 1500/1200/1200/2500 给思考留预算（热点/总览/看板/预案）；`chat()` 空 content 报错区分「推理模型思考占满预算」（提示调大 max_tokens / 换非推理模型），不再笼统「返回为空」；function-calling 守卫仅拦含 `reasoner` 的模型，`deepseek-v4-flash` 不误拦，QA 工具回放已含 `reasoning_content`。**自动兜底**：`LLMError` 增 `retryable` 标记（429/5xx/网络错误=可重试），`chat`/`chat_tools` 经 `_post_fallback` 在可重试失败时自动用兜底后端重试一次（无兜底 key 或兜底同主则原样抛出；兜底再失败如实上报）。换平台只改 `.env` 六键，不动代码。

`v0.12.0`：**修复看板「涨停家数多时文字无法显示」+ 资金流缺失补齐**——① 连板梯队「炸板/弱封」单元格把无上限弱封股列表 `join(" / ")` 拼进一格（`white-space:nowrap` + 无 overflow 兜底）→ 表格被撑破容器宽度、文字横向溢出被裁：弱封列改为**截断显示前 5 只 +「等 N 只」**，`#ladder/#themes/#break/#lhb` 四个表格容器加 `overflow-x:auto` 兜底；web 端 iframe `fitFrame` 加 `window resize` 重测（窗口缩放后底部行不再被裁/留空白）。② 当日资金流根因=东财 clist 接口**每页固定只返回 100 行**（实测 `pz=6000` 亦然，只拿到按主力净流入 Top-100）→ `fetch_moneyflow` 改为 clist 部分结果 + **对缺失炸板股代码逐个 fflow 单股补齐**（实测 17/17 全有数据）；DDX 日线/分时实测无公开稳定接口，本次只修日线补齐。

`v0.11.0`：**多模型协作热点复盘已可用**——撰写复盘时由独立**热点模型（模型 B）**（`module.hotspot` prompt，`HOTSPOT_MODEL` 可选、默认回落主模型）基于已采集概念板块行情+当日题材提炼当日 2-4 条热点主线简报，注入主分析师（模型 A）的「一总览/四题材/七次日预案」三章节撰写，让复盘贴合当天热点；概念板块行情采集扩展领涨股字段（f128/f140/f136 实测），pipeline 新增**仅当日**概念板块可选块（clist 实时快照，历史日期硬守卫不采集），不新增报告章节、`generate_report` 签名不变，热点调用失败降级确定性 Top-N，无概念数据逐字节保持旧行为。

`v0.10.0`：**端到端复盘 + 数据看板（修复+增强）+ 交互问答 + Flask 全套工作台 + 个人战法 + 图形启动器已可用**——东财涨跌停池/资金流/概念板块采集（`data/eastmoney_pool.py`）、**龙虎榜榜单/买卖席位采集（`data/eastmoney_lhb.py`）+ 知名游资识别名单（`data/hotmoney_seats.py`，可人工增补）**、指标计算（**情绪温度 `analysis/emotion.py`** / 连板梯队 / 题材周期 / 炸板净流入 / 龙虎榜游资，`analysis/`）、**DeepSeek LLM 自动生成 Markdown 复盘报告**（`llm/`）、**数据看板 `dashboard.py`**（近 10 个交易日趋势图表单文件 HTML + 趋势摘要表 + 情绪温度成分拆解 + LLM 多日趋势解读）、**交互问答 `kb/`**（RAG 短线知识库：`prompts/**`+`docs/` 部分+`knowledge/**`+`data/strategies/**` 自动收录、混合检索【sklearn 关键词 + 可选 bge-small-zh 向量 + RRF 融合】、源文件 sha256 增量重建持续更新；6 个数据工具 function-calling 落地 `tool.datatools`）、**Web 工作台 `web/`**（Flask：战法管理 / 跑复盘看报告与次日预案 / 问答 / 数据看板）、**个人战法 → 次日预案**（`web/strategy.py` 服务 + `llm/reporter.py::generate_report(..., strategy=)` 注入；战法由用户**页面上传**落盘 gitignored `data/strategies/`，**不写死进代码/tracked 目录**）、**图形启动器**（`launcher.py` 纯核心 + `launcher_gui.py` tkinter 窗口 + 根目录 `启动.bat`/`launcher.py` + `launch` CLI 子命令 + 桌面快捷方式；一键启动 Web/复盘/看板/问答，`--dry-run` 无头自检）。管道 `pipeline.py` + CLI `review`/`dashboard`/`qa`/`web`/`launch` 子命令。报告为**七章结构**（总览 / 情绪温度 / 连板梯队 / 题材 / 炸板资金 / 龙虎榜游资 / 次日预案）。运行环境固定为 `E:/conda_envs/envs/mowan_dm/python.exe`（勿改用其他解释器）。

**Web 数据看板性能修复（v0.10）**：`/api/dashboard/view` 此前每次请求都重新联网采集约 2 分钟 → iframe 空白「不显示」。现采用三层——① 进程内 `DashboardCache`（按 交易日期+N+no_llm 缓存、最多 16 条逐出最旧、**只缓存成功**）；② 复用已生成 `output/{date}_看板.html`（仅默认 10 日）；③ 采集/指标失败 → 自包含错误兜底页（HTTP 200 进 iframe，不裸 500）。保鲜规则：历史日期定稿常新；今日盘中 10 分钟 TTL；今日 15:00 后须 15:00 后生成才有效（`_dashboard_cache_is_fresh`，`_clock()` 可测）。看板内容增强放一页：新增「近 N 日趋势摘要表」「情绪温度成分拆解」面板，iframe 随内容自适应高度，LLM 解读配置 key 默认开启，review 页新增「查看该日看板」跳转，`/api/config/llm` 探测 key。

**多模型协作（v0.11）**：`generate_report` 在模块循环前先做一次独立 LLM 调用（模型 B，`module.hotspot`，`max_tokens=1500`）提炼当日热点简报，注入 一总览/四题材/七预案 三章节（措辞块「【另一模型提炼的当日热点（…）】」，须引用并校验、不得编造）。热点模型名用环境变量 `HOTSPOT_MODEL`（如 `deepseek-reasoner`）切换，默认空回落 `llm_model`。概念板块数据仅当日采集（`_fetch_concept_boards_block` 非今日硬守卫），不入报告章节、只供热点简报。

**LLM 密钥**：`.env`（不入库，已 gitignore）。**当前后端=商汤 SenseNova 主用 + 官方 DeepSeek 自动兜底**：主 `DEEPSEEK_API_KEY` / `LLM_BASE_URL=https://token.sensenova.cn/v1` / `LLM_MODEL=deepseek-v4-flash`（推理模型、Token Plan 免费额度有频率上限 429），兜底 `DEEPSEEK_FALLBACK_API_KEY` / `LLM_FALLBACK_BASE_URL=https://api.deepseek.com` / `LLM_FALLBACK_MODEL=deepseek-chat`（429/5xx/网络失败自动切换，`client._post_fallback`）。换平台只改 `.env` 六键不改代码。缺 key 时 `review --no-llm` / `dashboard --no-llm` 仍可跑通数据+指标+看板。

已可用命令（在项目根目录，`PYTHONPATH=src`）：
```bash
"E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review kline --code 600000 --lmt 30
"E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review realtime --codes 600601,002398,600789
# 端到端复盘：采集→指标→LLM 报告（涨停数据 15:00 后、龙虎榜 18:00 后完整、情绪温度需近5日时序；--no-llm 跳过 LLM）
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
# 战法 ↔ SKILL.md 双向桥（import 外部 skill 转成战法；export 战法导出为 SKILL.md）
"E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review skill import skills/fund-styles/深度价值-张坤型.md
"E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review skill export strategy.user-xxx --out out.skill.md
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
| `skills/fund-styles/` | 基金风格 skill 档案（v0.17，仅本项目内用；`kb/corpus.py` 收录供问答检索） | 做风格分析 / 导入为战法时 |

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
| `module.hotspot` | `prompts/modules/热点信息简报.md` | draft |
| `strategy.template` | `prompts/strategies/战法模板.md` | draft |
| `strategy.example` | `prompts/strategies/示例-连板接力.md` | draft |
| `tool.datatools` | `prompts/tools/数据工具schema.md` | draft |

完整信息见 [prompts/INDEX.md](prompts/INDEX.md)。

## 边界（不做 / 尚未做）

- 数据源定为**东方财富接口爬取**（非 akshare / tushare）。v0.3 已实现涨跌停池/资金流/概念板块（`data/eastmoney_pool.py`），v0.4 已实现龙虎榜/买卖席位（`data/eastmoney_lhb.py`）；**连板池（LB）、分时数据待实现**，详见 `docs/东财接口清单.md` 的状态列
- 龙虎榜盘后更新：`review` 在**下午 18:00 之后**跑才包含当日龙虎榜章节；盘中/未更新日该章节自动降级为「未更新」说明
- 运行环境固定为 `E:/conda_envs/envs/mowan_dm`；安装依赖只进该环境或本项目文件夹
- LLM 角色：**自动报告已实现**（DeepSeek，`llm/`）、**数据看板多日解读已实现**（`dashboard.py`，可 `--no-llm` 降级）、**交互问答已实现**（`kb/`，RAG 知识库 + 6 个数据工具 function-calling；向量路径可选，未装自动降级纯关键词）、**Web 工作台已实现**（`web/`，Flask，默认仅本机 `127.0.0.1:5000`，无认证勿暴露 LAN）
- 首期模块：情绪温度、连板梯队、题材运行周期与归类、炸板净流入、龙虎榜游资（已实现）
- 数据看板：近 N 日趋势图表单文件 HTML（`output/{date}_看板.html`，不入库）+ 趋势摘要表 + 情绪温度成分拆解 + LLM 多日趋势解读；历史数据缺日时该日按缺数据标记，看板照常渲染；Web 端带进程内缓存+文件复用+错误兜底（见上方 v0.10 修复说明）
- **个人战法**（v0.8 已实现）：两条路径——① tracked 种子示例 `prompts/strategies/`（`strategy.template/example`，只读，禁改/禁删，登记者 `prompts/INDEX.md`）；② **用户 UI 上传**落盘 `data/strategies/`（gitignored `data/*/` 已覆盖，**不入库**），id 自动 `strategy.user-<sha256(name)[:10]>`，驱动 `review --strategy <id>` 与 Web 复盘任务的次日预案（`module.plan` + 战法正文注入）
- 未来：策略回测 / 更多数据工具 / 登录鉴权（工作台目前仅 localhost 无认证）
