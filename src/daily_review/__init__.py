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
"""

__version__ = "0.10.0"
