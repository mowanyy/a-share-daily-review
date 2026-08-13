# Prompt 总索引

> 本文件是 `prompts/` 的唯一导航表。每个 prompt 文件顶部有 YAML front-matter，字段与本表一一对应。
> **新增/修改 prompt 后必须同步本表。** 术语权威定义见 [glossary/术语表.md](glossary/术语表.md)。

## 角色说明

- `report`：报告模式（自动生成复盘文档的模块）
- `qa`：问答模式（交互对话）
- `strategy`：战法库（供 report/qa 引用）
- `tool`：工具/函数定义（问答模式 function-calling）

---

## 索引表

| ID | 文件 | 角色 | 依赖数据 | 输出 | 状态 |
|---|---|---|---|---|---|
| `system.analyst` | [system/复盘分析师.md](system/复盘分析师.md) | report | 各模块摘要 | 全文复盘框架与纪律 | draft |
| `system.assistant` | [system/问答助手.md](system/问答助手.md) | qa | 知识库(RAG)片段 + 工具调用结果 | 对话回答 | active |
| `module.emotion` | [modules/情绪温度.md](modules/情绪温度.md) | report | 涨停/炸板/跌停池、多日时序 | 情绪温度章节 | draft |
| `module.dashboard` | [modules/数据看板.md](modules/数据看板.md) | report | 多日时序、涨停/炸板/跌停池、连板统计、情绪温度 | 多日趋势解读一段 | draft |
| `module.ladder` | [modules/连板梯队.md](modules/连板梯队.md) | report | 涨停池、连板统计 | 梯队章节 | draft |
| `module.theme` | [modules/题材周期与归类.md](modules/题材周期与归类.md) | report | 题材+成分+多日时序 | 题材归类章节 | draft |
| `module.break` | [modules/炸板净流入.md](modules/炸板净流入.md) | report | 炸板池、资金流 | 炸板资金章节 | draft |
| `module.lhb` | [modules/龙虎榜游资.md](modules/龙虎榜游资.md) | report | 龙虎榜榜单、买卖席位、涨停池 | 龙虎榜游资章节 | draft |
| `module.plan` | [modules/次日预案.md](modules/次日预案.md) | report | 当日复盘 + 战法 | 次日预案章节 | draft |
| `module.hotspot` | [modules/热点信息简报.md](modules/热点信息简报.md) | report | 概念板块行情、当日题材 | 当日热点主线简报 | draft |
| `module.overnight` | [modules/隔夜预案.md](modules/隔夜预案.md) | report | 昨日复盘 + 东财7x24隔夜消息 | 隔夜预案（消息面联动关注方向） | draft |
| `module.open_strategy` | [modules/开盘策略.md](modules/开盘策略.md) | report | 昨日复盘 + 隔夜预案 + 竞价数据 | 开盘策略（有机会个股清单） | draft |
| `strategy.template` | [strategies/战法模板.md](strategies/战法模板.md) | strategy | —（模板） | 战法文件结构 | draft |
| `strategy.example` | [strategies/示例-连板接力.md](strategies/示例-连板接力.md) | strategy | —（示例） | 示范战法 | draft |
| `example.report` | [examples/复盘示例_20260806.md](examples/复盘示例_20260806.md) | example | 示意数据 | 复盘范文 | draft |
| `tool.datatools` | [tools/数据工具schema.md](tools/数据工具schema.md) | tool | —（定义） | 工具 JSON Schema | active |

---

## 使用关系

```
报告模式（自动复盘）
	  system.analyst
	    ├─ module.emotion   → 情绪温度章节（市场级定位）
	    ├─ module.ladder    → 连板梯队章节
	    ├─ module.theme     → 题材归类章节
	    ├─ module.break     → 炸板资金章节
	    ├─ module.lhb       → 龙虎榜游资章节
	    ├─ module.plan      → 次日预案章节（引用 strategy.* 战法）
	    └─ module.hotspot   → 模型 B 独立提炼当日热点简报，注入 一总览/四题材/七预案（不新增章节）

	盘前模式（隔夜预案 + 开盘策略，v0.13）
	  module.overnight     → 隔夜预案（消息面汇总 → 题材联动 → 关注方向）
	  module.open_strategy → 开盘策略（竞价总览 → 有机会个股清单 → 开盘执行提示）

看板模式（数据看板 + LLM 多日趋势解读）
  module.dashboard → 「多日趋势解读」一段（数据看板主体为程序核算图表，
                     本 prompt 只负责顶部一段趋势解读；无 key/失败时降级为「（未生成解读）」）

问答模式（交互对话，v0.7）
  system.assistant
    ├─ 知识库(RAG)：prompts/** + docs/{需求分析,数据结构,战法规范} + knowledge/**
    │    自动入库 → 检索片段接地（[来源]标注），用户往 knowledge/ 放 .md 即自动重索引
    └─ tool.datatools（数据问题按需查数据，再作答）
```

## 状态说明

- `draft`：结构已定、语义待打磨、**尚未接入代码**
- `active`：已接入生成流程，可被程序调用

## 新增 Prompt 的登记步骤

1. 复制 `modules/` 下现有文件结构，写 front-matter（`id/name/version/role/status/depends/output`）
2. 本表追加一行，保持与 front-matter 一致
3. 若引入新术语，先补 [glossary/术语表.md](glossary/术语表.md)
4. 若有新 ID 层级，同步更新 [CLAUDE.md](../CLAUDE.md) 的命名规范
