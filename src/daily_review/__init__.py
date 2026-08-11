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
"""

__version__ = "0.12.1"
