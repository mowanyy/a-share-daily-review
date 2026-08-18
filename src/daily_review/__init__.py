"""每日复盘 · 超短连板复盘系统。

v0.1：需求分析 + Prompt 工程骨架。
v0.2：数据采集层（东财 K 线 / 新浪实时行情）。
v0.3：端到端复盘程序——东财涨跌停池/资金流/概念板块采集、连板梯队/题材周期/炸板净流入指标、
      DeepSeek LLM 生成 Markdown 复盘报告（见 docs/需求分析.md、CLAUDE.md）。
v0.4：新增龙虎榜游资分析——东财龙虎榜榜单/买卖席位采集 + 知名游资识别（打板/趋势/量化/机构），
      报告章节扩展为六章（龙虎榜与游资）。
v0.5：新增情绪温度——涨停数/空间板高度/晋级延续率/炸板率(反向)/跌停家数(反向)五维加权合成
      0–100 温度分 + 情绪周期阶段（冰点期/修复期/高潮期/退潮期），报告章节扩展为七章（情绪温度）。
v0.6：新增数据看板——近 10 个交易日趋势图表（自包含单文件 HTML，纯 JS + 内联 SVG），
      顶部附 DeepSeek 多日趋势解读（可 --no-llm 降级），dashboard CLI 子命令。
v0.7：新增交互问答——RAG 短线知识库（混合检索：sklearn 关键词 n-gram + 可选 bge-small-zh
      向量 + RRF 融合；源= prompts/** + docs/{3 份} + knowledge/** 自动收录、sha256 增量重建，
      持续更新）+ 6 个数据工具 function-calling（tool.datatools 契约落地），qa CLI 子命令。
v0.8：新增 Web 工作台（Flask）——战法管理（页面上传/编辑/启停，落盘 gitignored
      data/strategies/，不入项目）+ 跑复盘看七章报告与次日预案（可指定个人战法驱动，
      后台线程单飞任务）+ 问答 QA + 数据看板；个人战法 → AI 次日预案落地（module.plan
      注入战法正文，web CLI 子命令，review --strategy <id>）。
v0.9：新增图形启动器——双击 启动.bat 或桌面快捷方式「每日复盘」打开 tkinter 窗口，
      一键启动 Web 工作台 / 跑复盘 / 数据看板 / 交互问答；launcher.py 纯核心（可离线
      单测）+ launcher_gui.py 窗口 + launch CLI 子命令（--dry-run 无头自检）；子进程
      用控制台 python.exe + PYTHONIOENCODING=utf-8 + CREATE_NO_WINDOW/NEW_CONSOLE。
v0.10：修复 Web 数据看板「不显示」——根因 /api/dashboard/view 每次请求都重新联网
      采集约 2 分钟导致 iframe 空白；新增进程内 DashboardCache（按 交易日期+N+no_llm
      缓存、最多 16 条逐出最旧、只缓存成功）+ 复用已生成 output/{date}_看板.html
      （默认 10 日）+ 自包含错误兜底页 + 保鲜规则（历史定稿常新 / 今日盘中 10 分钟 /
      收盘后须 15:00 后生成）；内容增强并放一页：新增「近 N 日趋势摘要表」与「情绪
      温度成分拆解」面板，iframe 随内容自适应高度与页面融为一体，LLM 解读配置了 key
      默认开启，review 页加「查看该日看板」跳转，新增 /api/config/llm 探测。
v0.11：多模型协作热点复盘——东财概念板块行情采集扩展领涨股字段（f128/f140/f136
      实测）；pipeline 新增「仅当日」概念板块可选块（clist 实时快照，历史日期硬守卫
      不采集）+ compute 透传 Top-12 概念榜；撰写复盘时由独立热点模型（模型 B，
      module.hotspot prompt，HOTSPOT_MODEL 可选，默认回落主模型）先提炼当日 2-4 条
      热点主线简报，再注入主分析师（模型 A）的「一总览/四题材/七次日预案」三章节撰写，
      让复盘贴合当天热点；不新增报告章节，generate_report 签名不变；热点调用失败降级
      为确定性 Top-N（概念板块涨幅/净流入）注入，无概念数据时逐字节保持旧行为。
v0.12：修复看板「涨停家数多时文字无法显示」+ 资金流缺失补齐——① 连板梯队「炸板/弱封」
      单元格把无上限弱封股列表 join 进一格（white-space:nowrap + 无 overflow 兜底）导致
      表格横向溢出被裁 → 弱封列截断显示前 5 只 +「等 N 只」，四个表格容器加 overflow-x:auto
      兜底；web 端 iframe fitFrame 加 window resize 重测（窗口缩放后底部行不再被裁/留空白）。
      ② 当日资金流根因=东财 clist 每页固定只返回 100 行（实测 pz=6000 亦然，只拿到主力
      净流入 Top-100）→ fetch_moneyflow 改为 clist 部分结果 + 对缺失炸板股代码逐个 fflow
      单股补齐（实测 17/17 全有数据）；DDX 日线/分时实测无公开稳定接口，本次只修日线补齐。
v0.12.1：LLM 后端切换商汤 SenseNova（主）+ 官方 DeepSeek 自动兜底——新 key 属商汤
	      Token Plan（token.sensenova.cn/v1，api.sensenova.cn 404），模型 deepseek-v4-flash
	      （仅此名可用）是推理模型（回包带 reasoning_content，思考先占 token）且免费额度
	      限流（实测 429）；四个小预算调用点 max_tokens 500/600/500/1500 → 1500/1200/1200/2500
	      给思考留预算；chat() 空 content 报错区分「推理模型思考占满预算」；LLMError 增
	      retryable 标记（429/5xx/网络=可重试），chat/chat_tools 经 _post_fallback 在可重试
	      失败时自动用兜底后端（DEEPSEEK_FALLBACK_API_KEY / LLM_FALLBACK_BASE_URL /
	      LLM_FALLBACK_MODEL，官方 DeepSeek）重试一次。
	v0.13：复盘三时段拆分——盘后复盘（18:00后，现有）+ 隔夜预案（9:00前，消息面）
	      + 开盘策略（9:25-9:30，竞价个股筛选）。新增东财7x24快讯采集（eastmoney_news）、
	      竞价指标计算（auction）、隔夜预案+开盘策略 LLM 生成（premarket）；CLI 新增
	      plan/open 子命令，Web 新增对应按钮与 API；两个新 prompt：module.overnight、
	      module.open_strategy。257 测试通过。
	v0.14：看板数据本地化加速 + 概念池 Agent 管理——补上 3 块静态缓存到 data/cache/
	      （行业映射/概念成分/交易日历，省~72s/次网络开销）；新增 data/local_cache.py；
	      pipeline 行业映射/概念成分、eastmoney_pool 交易日历均走缓存；新增 update-data
	      CLI 命令（刷新缓存+重采N天）与 Web 按钮；新增概念池 CRUD 服务 web/concept_pool.py
	      （data/stock_pool/concepts/{概念名}.csv），Agent 通过 6 个 function-calling 工具
	      增删改查概念股票池，自动同步到 knowledge/概念池/ 供 RAG 检索；新增 tools/stock_pool.py
	      供选股数据按日期切分（split-pool 命令）；新增 6 个概念池工具 schema 更新。
v0.15：Web 工作台移动端适配——base.html 全局媒体查询（≤767px 单列布局、
	      导航紧凑/表单堆叠/按钮触控/表格可横滑 .table-wrap），dashboard.py 自包含
	      看板适配手机端（KPI 网格列宽/字号缩放/表格溢出），review/concepts 页面微调；
	      新增文档 docs/移动端适配方案.md（响应式设计规范 9 章）。CLI web 曾新增
	      --lan 局域网参数与启动器复选框，因开发环境（公司网络）不使用网络分享已回退
	      （v0.15.1）。257 测试通过。
	v0.15.1：回退「局域网访问」功能——cli.py 移除 --lan 参数与启动打印局域网地址、
	      launcher.py build_web_argv 还原为固定 127.0.0.1、launcher_gui.py 移除
	      「局域网访问」复选框；web --host 参数保留（默认 127.0.0.1 仅本机）。
	      移动端适配保留。257 测试通过。
	v0.16：复盘报告持久化查询（历史报告档案）——复盘/隔夜/开盘策略每次生成已落盘
	      output/{date}_*.md，但 Web 只查进程内存 JobState.report_html，重启后旧报告
	      看不到、需重新生成，不利多日复盘。新增 web/history.py：list_reports 扫描
	      output/ 四种产物按日倒序、load_report 读回复盘 md → md_to_html 全文 +
	      section_html 提取次日预案，返回结构与 JobState.to_dict 兼容（前端 showResult
	      零改动复用）；Web 新增 /api/review/history 与 /api/review/history/<date>，
	      review.html 加「历史报告」日期下拉 + 查看。零改动生成逻辑，日期正则 +
	      resolve 防路径穿越。268 测试通过。
	v0.17：战法 ↔ SKILL.md 双向桥 + 基金风格 skill 档案——① 新增 web/skill_bridge.py：
	      import_skill 把外部 SKILL.md 一键转成个人战法（复用 strategy.create/make_id 链路，
	      id=strategy.user-<sha256(name)[:10]>、正文原样保留、缺节告警不拒绝），
	      export_strategy 把战法（tracked/user 均可）导出为 SKILL.md（name+description+正文），
	      Web 战法管理页加「导入 SKILL.md」面板与每行「导出 SKILL.md」下载按钮，路由
	      /api/strategies/import-skill 与 /api/strategies/<id>/export-skill，CLI 新增
	      skill import/export 子命令。② 项目内新增 skills/fund-styles/ 4 个基金风格档案
	      （深度价值张坤型/景气成长刘格菘型/低估值丘栋荣型/医药成长葛兰型：风格画像→
	      可判定指标清单→选股规则→买卖持有纪律→反例→输出格式），仅供本项目使用
	      （不同于复盘的短线战法逻辑，可作独立分析视角；可一键导入战法库再改写）；
	      kb/corpus.py 收录 skills/** 供问答检索；配套 docs/基金风格skill使用说明.md。
	      282 测试通过。
	v0.17.1：基金风格周K/月K 周期锚定——4 个基金风格档案各新增第 0 节「时间周期与
	      触发时点（先读）」：只在周K/月K 层面做判断、不用日K 做风格决策；每周一 → 周K
	      视角复盘（中期趋势/买点/量能），每月 1 号 → 月K 视角复盘（长线趋势/估值分位/
	      景气与政策）；各风格写明周期侧重（张坤=月K估值周K买点 / 刘格菘=月K景气周K加速 /
	      丘栋荣=月K估值分位周K左买 / 葛兰=月K景气政策周K龙头买点）。使用说明文档新增
	      「使用节奏」章节（周一/月初表 + kline --klt 102/103 拉周/月K 数据 + 禁止日K 冒充
	      周月K）；CLI kline 帮助补 103=月线。结构化回归测试锁定档案含周期方法论
	      （test_skill_bridge.py）。286 测试通过。
	v0.18：Web 问答页「基金经理分析」栏目（独立风格 agent）——问答页改双栏：左侧原
	      知识库问答、右侧空白处新增「基金经理分析」面板。新增 web/fund_agent.py：
	      list_managers 扫描 skills/fund-styles/*.md 档案（4 个基金经理可下拉选）；
	      analyze 按所选风格档案生成 system prompt（角色头+档案正文含「时间周期与触发
	      时点」第0节+输出纪律），从问题抠 6 位股票代码（去重限 3 只、剔除日期形数字）
	      注入真实 周K(klt=102)/月K(klt=103) 数据（eastmoney.fetch_kline lmt=36，
	      失败按数据不足处理并告知 agent），单次 llm.chat 回复，LLM 失败降级 error 字段。
	      Web 新增 GET /api/fund/managers 与 POST /api/fund/analyze（未知经理 404、
	      空问题/非法klt 400）；前端面板：基金经理下拉+周K/月K周期下拉+问题输入+分析
	      按钮（md_to_html 渲染回答，副行显示数据注入说明与异常）。零外链零 CDN。
	      298 测试通过。
	v0.19：基金经理 agent 上下文记忆 + 中军自动识别——新增 data/eastmoney_pool.py::
	      fetch_market_caps（clist 批量查总市值 f20）。web/fund_agent.py 大改：session
	      持久化到 data/fund_sessions/{manager_id}.json（gitignored data/*/ 已覆盖，
	      重启不丢上下文）；_ensure_zhongjun 从当日涨停池 CSV 按 industry 分组取市值
	      最大股作为中军（无 CSV/网络失败→中军为空，不抛异常，agent 仍可正常回答）；
	      analyze 新增 trade_date 参数，system prompt 末尾追加中军摘要（代码+名称+题材+
	      市值），上下文保留最近 10 轮对话（超过自动裁剪），LLM 失败保留历史供重试。
	      Web 新增 POST /api/fund/clear/<manager_id>（清空会话）与 GET /api/fund/
	      session/<manager_id>（history_length/zhongjun/updated_at）；POST /api/fund/
	      analyze 回包新增 history_length 与 zhongjun。前端改为聊天式界面：滚动历史
	      对话区（每轮 user+agent 可回溯）、清空记忆按钮、实时显示中军信息（来自涨停池
	      自动识别）。零外链零 CDN。309 测试通过。
	v0.20：多 Agent 通信框架（互相提问 + 多专家会诊）——新增 web/agent_registry.py：
	      Agent 统一注册中心（register/list_agents/call_agent），模块导入时自动注册 QA Agent
	      （qa_general）、基金经理 Agent（fund_张坤/fund_刘格菘/fund_丘栋荣/fund_葛兰）、
	      热点简报 Agent（hotspot_brief）。QA Agent 的 function-calling 工具新增 query_agent
	      工具（kb/tools.py），可调用其他 Agent 获取专业分析意见。基金经理 Agent 从 chat()
	      改为 chat_tools()，新增 query_qa 工具（可向 QA Agent 查询市场概况），支持最多 3 轮
	      工具循环。新增多 Agent 会诊端点 POST /api/agents/consult（选多个 Agent → 各自分析
	      → 合成 LLM 综合观点）与 GET /api/agents/list。Web 问答页底部新增「多 Agent 会诊」
	      面板（勾选 Agent → 输入问题 → 看各观点 + 综合结论）。零外链零 CDN。
		v0.20.1：修复历史报告下拉框漏掉隔夜预案/开盘策略——原 `load_report()` 只读 `_复盘.md`，
		      前端下拉 `.filter(r.has_review)` 排除仅有隔夜/开盘的日期。新增 `load_artifact(date,
		      type)` 支持 `review/plan/open` 三种类型加载；`GET /api/review/history/<date>` 新增
		      `?type=review|plan|open` 参数；前端下拉显示所有有产物的日期，查看时自动选最佳类型，
		      多类型产物时显示类型切换按钮。335 测试通过。
	v0.20.2：修复隔夜预案/开盘策略「模型把 max_tokens 全部用于思考」——v0.12.1 上调了
		      热点/总览/看板/预案 4 个调用点的 max_tokens，但 v0.13 新增的隔夜预案与开盘策略
		      两个调用点（llm/premarket.py）仍为 2000，推理模型（deepseek-v4-flash）思考先占
		      预算导致正文为空。两个调用点 max_tokens 2000 → 4000，新增回归测试锁定
		      （tests/test_premarket.py：max_tokens ≥ 4000 + LLM 失败兜底）。338 测试通过。
	v0.20.3：推理模型思考占满预算 → 自动切兜底非推理模型（根治大输入场景）——
	      v0.20.2 调大 max_tokens 到 4000 仍治标不治本：开盘策略含 68 只竞价股 +
	      隔夜预案全文，推理模型思考量极大。根因：chat() 的「思考占满预算」错误在
	      _post_fallback 返回之后才抛出（retryable=False），兜底机制根本不会触发。
	      修复：chat() 检测到 reasoning_content 非空 + finish_reason=length + 正文为空时，
	      直接用兜底后端（官方 deepseek-chat 非推理模型）重试一次；未配置兜底或兜底
	      同主时落回原报错（retryable=True 供上层判断）。chat_tools 不受影响（空正文 +
	      工具调用属正常）。新增测试 test_reasoning_budget_exhausted_falls_back
	      （主 key 空正文 → 兜底 key 返回完整正文）。339 测试通过。
	v0.21：定时推送报告到飞书（GitHub Actions 免费云端）——新增 notify.py（飞书群机器人
	      webhook 推送，支持加签 HMAC-SHA256）、push.py（生成报告→提取标题+摘要→推送，
	      摘要确定性提取不调 LLM，周末/休市自动跳过）；config 新增 FEISHU_WEBHOOK_URL/
	      FEISHU_SECRET；CLI 新增 push 子命令（push --type review|plan|open）。新增 3 个
	      GitHub Actions workflow 定时触发：盘后复盘 18:00 / 盘前隔夜预案 08:30 / 竞价后
	      开盘策略 09:25（北京时间，cron 转 UTC，1-5 避开周末），LLM 非机密配置写死 +
	      密钥走 GitHub Secrets（DEEPSEEK_API_KEY / DEEPSEEK_FALLBACK_API_KEY /
	      FEISHU_WEBHOOK_URL）。配套 docs/飞书推送说明.md。360 测试通过。
	v0.21.1：修复云端推送漏掉飞书签名——机器人设了「签名校验」时，GitHub Actions
	      workflow 只注入了 FEISHU_WEBHOOK_URL、漏了 FEISHU_SECRET，云端跑时不带签名
	      会被飞书拒绝。3 个 workflow 补 FEISHU_SECRET 环境变量，docs 说明补第 4 个 Secret。
	v0.21.2：推送摘要内容扩丰富——复盘报告由只推「一、总览」改为推 4 个核心章节
	      （总览 + 情绪温度 + 连板梯队 + 次日预案）；隔夜预案/开盘策略正文 15 行 → 30 行。
	v0.21.3~v0.21.6：README 面向用户改写（安装→配置→使用→docs 路由，技术细节改为
	      路由表引人）+ 复盘时间全库统一 18:00（盘后复盘 cron 17:30→18:00、龙虎榜
	      18:00 更新、隔夜消息窗口 18:00、看板缓存收盘边界 18:00，代码/文档/测试全同步）。
	v0.21.7：push 失败/跳过也推飞书状态提示——GitHub Actions schedule 派发实测迟到
	      （2026-08-17 复盘排程晚 35 分钟，隔夜预案 08:30 排程首日迟迟未派发），此前
	      非 sent 状态（周末/休市跳过、生成/推送失败）飞书端完全无声（skipped 还 exit 0
	      显绿，公司网络又打不开 Actions 页面）。push.py 新增 _status_notice/_try_notify/
	      _with_status_notice：非 sent 结果补发 ⏭/❌ 状态提示（含原因），提示发送失败
	      静默记录不改主状态；docs/飞书推送说明.md 补「排程延迟与状态提示」小节。
	      366 测试通过。
"""

__version__ = "0.21.7"
