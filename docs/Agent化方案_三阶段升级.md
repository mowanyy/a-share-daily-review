# Agent 化方案 — 三阶段升级

**日期**：2026-08-20
**参考项目**：a-stock-agent（wispig66）
**当前阶段**：第一阶段已完成 ✅

---

## 核心差距

| 维度 | 现状 | 目标 |
|------|------|------|
| 飞书推送 | webhook 单向推送，只能发不能收 | WebSocket 长连接，双向交互 |
| 运行模式 | 定时任务（跑完就结束） | 常驻 daemon + 定时 skill 分离 |
| 盘中交互 | CLI 命令手动跑 | Agent 自动监控 + 随时问答 |
| 审计日志 | 无 | SQLite 全量记录 |

---

## 第一阶段：飞书升级到 WebSocket

### 目标
从 webhook 单向推送升级为 WebSocket 长连接，客户可以在飞书群里@机器人提问，**不需要公网地址**。

### 架构变化

```
现状：本机 ──HTTP POST──► 飞书 webhook（单向，只能发）

目标：本机 ──WebSocket 长连接──► 飞书服务器（双向，可收可发）
        ▲                              │
        │                              │ 客户@机器人
        │                              │ → 飞书推送消息事件
        │                              │ → WebSocket 到达本机
        │                              │ → Agent 处理
        └──────────────────────────────┘ → WebSocket 回复
```

### 具体改动

**1. 飞书 SDK 集成**
- 安装 `lark-oapi`（飞书官方 Python SDK）
- 配置 `.env`：`FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_HOME_CHANNEL`、`FEISHU_ALLOWED_CHAT_IDS`
- 新建 `web/feishu_gateway.py`：WebSocket 客户端，负责连接飞书服务器
- 监听 `im.message.receive_v1` 事件

**2. 推送通道改造**
- 现有 `notify.py`（webhook）保留作为降级通道
- 新增 `WebSocketPush` 类，通过 WebSocket 发送消息
- 推送渲染：支持飞书卡片消息（彩色标题，看涨绿色/看跌红色/中性蓝色）

**3. 消息路由**
- 客户消息 → 解析问题类型（查询数据/分析判断/越界）
- 数据查询类 → 调用现有 6 个数据工具（function-calling）
- 分析类 → 调用 LLM 生成回答
- 越界类 → 合规回复「无法给出建议」

**4. 合规边界**
- system prompt 写入合规规则
- 数据查询和历史对比正常回答
- 交易建议类回复「无法给出建议，请咨询投资顾问」

### 不做
- ❌ 不改变现有复盘/预案/开盘策略的生成逻辑
- ❌ 不改变现有定时推送（推送通道从 webhook 改为 WebSocket，但触发时机不变）

---

## 第二阶段：常驻 daemon（盘中实时化）

### 目标
常驻进程自动监控盘中变化，有异常时主动推送给客户，客户盘中随时可以问问题。

### 具体改动

**1. 常驻进程**
- 新增 `daily_review daemon` 子命令
- daemon 负责：
  - 每 5 分钟拉一次涨停池/实时行情
  - 计算变化（炸板潮/新题材爆发/龙头异动）
  - 有异常时主动推飞书
  - 整合飞书 WebSocket 监听

**2. 盘中监控增强**
- 现有 `intraday.py`（v0.27）做快照 diff
- 升级为：定时轮询 + 阈值触发 + 异常汇总
- 异常类型：涨停家数突变、炸板潮、新题材爆发、龙头跳水

**3. 盘中问答**
- 客户问「现在什么情况」→ Agent 拉最新数据 → 回复
- 客户问「某某股票」→ 拉实时行情 → 回复

---

## 第三阶段：Agent 深度化（未来）

- 多轮对话记忆（复用 fund_sessions 模式）
- SQLite 审计日志
- 浏览器插件（同花顺/东财页面，token 充足时）
- 微信通道扩展

---

## 参考项目

- **a-stock-agent**（wispig66）：GitHub 开源，MIT 协议
  - 飞书 WebSocket 集成（lark-oapi）
  - fact pack 白名单机制（防 LLM 编造）
  - 常驻 daemon + 定时 skill 分离
  - SQLite 审计日志