---
name: git-push-no-vpn
description: 在公司网络无加速器/无 VPN 环境下把代码推送到 GitHub 的方法。当 git push 报「Failed to connect to 127.0.0.1」「Connection was reset」「Couldn't connect to server 443」等网络错误，或用户要求推送 GitHub 但网络不通时使用。
---

# 无加速器推送 GitHub（公司网络直连方案）

## 适用场景

- 在公司网络环境，无加速器 / 无 VPN
- `git push` 报以下任一错误：
  - `Failed to connect to 127.0.0.1 port XXXX`（代理残留，代理客户端已关闭）
  - `Recv failure: Connection was reset`（防火墙重置连接）
  - `Failed to connect to github.com port 443`（443 间歇性不通）
- 用户说「帮我提交 GitHub / 推送一下」但网络不通

## 核心命令（记住这一条）

```bash
git -c http.proxy= -c https.proxy= -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=30 push origin main --tags
```

## 原理（为什么这样写）

### ① 置空代理 → 强制直连

`-c http.proxy= -c https.proxy=` 把代理设为空，**仅对这一条命令生效**（不改全局配置）。

- 背景：本机全局/系统曾配置过本地代理（如 `127.0.0.1:26561`）。加速器关闭后代理配置残留，git 试图连接已死掉的代理端口 → `Failed to connect to 127.0.0.1`
- 置空后 git 直连 `github.com:443`，绕开死代理

### ② 宽容慢速连接

`-c http.lowSpeedLimit=1000 -c http.lowSpeedTime=30`：

- 含义：只有**连续 30 秒**传输速度低于 **1000 字节/秒**才中断
- 背景：公司网络直连 GitHub 慢且抖动大，git 默认会对"太慢"的传输主动中断，误杀慢但在走的连接

### ③ 多次重试撞窗口期（关键！）

公司防火墙对 GitHub 443 的干扰是**间歇性**的，失败后隔几秒重试，总能碰到能通的窗口：

- 实测记录（本项目）：
  - v0.19：重试 11 次、第 4 轮成功
  - v0.20.0：第 1 次成功
  - v0.20.2：先失败 6 次，隔一段时间后第 1 次成功
- 结论：**不要因为连续几次失败就放弃**，重试 5-10 次、必要时隔几分钟再试

### ④ TLS 证书校验已关闭（全局一次性设置）

本环境全局 git config 已有 `http.sslVerify=false`：

- 背景：公司防火墙可能做 TLS 证书拦截（MITM），证书校验会失败
- 关闭后连接照常；push 时会看到 `SECURITY WARNING: TLS certificate verification has been disabled` 警告，**属预期现象，忽略即可**
- 若新机器需要设置：`git config --global http.sslverify false`

## 一键重试脚本（实战直接用）

```bash
for i in $(seq 1 10); do
  echo "=== 第 $i 次尝试 $(date +%H:%M:%S) ==="
  if git -c http.proxy= -c https.proxy= -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=30 push origin main --tags; then
    echo "=== 推送成功（第 $i 次）==="
    break
  fi
  sleep 10
done
```

10 次都失败时：**本地提交不会丢**（commit + tag 都在本地），告知用户网络暂时不通、稍后再推即可。

## 使用纪律

1. **先本地提交，再推送**：`git add -A` + `git commit` + `git tag` 全部完成后再 push，推送失败不影响成果保存
2. **不修改全局代理配置**：全部用 `-c` 单命令参数，用完即走
3. **如实汇报**：成功要说明第几次成功；失败要如实说失败了几次、本地已安全
4. 本项目推送命令固定：`git push origin main --tags`（主分支 main，含版本 tag）

## 新机器一次性准备（备忘）

```bash
# 1. 关闭 TLS 校验（公司防火墙 MITM 场景）
git config --global http.sslverify false
# 2. 若全局有残留代理，清掉（可选，也可每次用 -c 覆盖）
git config --global --unset http.proxy
git config --global --unset https.proxy
```
