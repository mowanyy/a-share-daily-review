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
| `system.assistant` | [system/问答助手.md](system/问答助手.md) | qa | 工具调用结果 | 对话回答 | draft |
| `module.ladder` | [modules/连板梯队.md](modules/连板梯队.md) | report | 涨停池、连板统计 | 梯队章节 | draft |
| `module.theme` | [modules/题材周期与归类.md](modules/题材周期与归类.md) | report | 题材+成分+多日时序 | 题材归类章节 | draft |
| `module.break` | [modules/炸板净流入.md](modules/炸板净流入.md) | report | 炸板池、资金流 | 炸板资金章节 | draft |
| `module.plan` | [modules/次日预案.md](modules/次日预案.md) | report | 当日复盘 + 战法 | 次日预案章节 | draft |
| `strategy.template` | [strategies/战法模板.md](strategies/战法模板.md) | strategy | —（模板） | 战法文件结构 | draft |
| `strategy.example` | [strategies/示例-连板接力.md](strategies/示例-连板接力.md) | strategy | —（示例） | 示范战法 | draft |
| `example.report` | [examples/复盘示例_20260806.md](examples/复盘示例_20260806.md) | example | 示意数据 | 复盘范文 | draft |
| `tool.datatools` | [tools/数据工具schema.md](tools/数据工具schema.md) | tool | —（定义） | 工具 JSON Schema | draft |

---

## 使用关系

```
报告模式（自动复盘）
  system.analyst
    ├─ module.ladder → 连板梯队章节
    ├─ module.theme  → 题材归类章节
    ├─ module.break  → 炸板资金章节
    └─ module.plan   → 次日预案章节（引用 strategy.* 战法）

问答模式（交互对话）
  system.assistant
    └─ tool.datatools（按需查数据，再作答）
```

## 状态说明

- `draft`：结构已定、语义待打磨、**尚未接入代码**
- `active`：已接入生成流程，可被程序调用

## 新增 Prompt 的登记步骤

1. 复制 `modules/` 下现有文件结构，写 front-matter（`id/name/version/role/status/depends/output`）
2. 本表追加一行，保持与 front-matter 一致
3. 若引入新术语，先补 [glossary/术语表.md](glossary/术语表.md)
4. 若有新 ID 层级，同步更新 [CLAUDE.md](../CLAUDE.md) 的命名规范
