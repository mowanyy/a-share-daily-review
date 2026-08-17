"""飞书群机器人推送（v0.21）：用 requests 直接 POST 自定义机器人 webhook。

- 支持加签校验（机器人设置「签名校验」时用 FEISHU_SECRET）
- 返回飞书响应 JSON；成功判 `code == 0`
- 无第三方 SDK，复用项目已有 requests 依赖
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

import requests

from daily_review.config import get_settings


class FeishuError(RuntimeError):
    """飞书推送失败（网络/未配置/接口返回错误码）。"""


def _sign(secret: str, timestamp: int) -> str:
    """飞书加签：HMAC-SHA256(timestamp + "\\n" + secret) 的 base64。

    参考飞书开放平台自定义机器人签名校验算法。
    """
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(
        string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_feishu(
    text: str,
    *,
    webhook_url: str | None = None,
    secret: str | None = None,
    timeout: float = 10,
) -> dict:
    """向飞书群机器人发送 text 消息，返回飞书响应 JSON。

    未配置 webhook → 抛 FeishuError；飞书返回 code != 0 → 抛 FeishuError（含 msg）。
    """
    settings = get_settings()
    url = (webhook_url or settings.feishu_webhook_url).strip()
    if not url:
        raise FeishuError("未配置飞书 webhook：请在 .env 写 FEISHU_WEBHOOK_URL")

    payload: dict = {
        "msg_type": "text",
        "content": {"text": text},
    }
    if secret is None:
        secret = settings.feishu_secret
    if secret:
        ts = int(time.time())
        payload["timestamp"] = str(ts)
        payload["sign"] = _sign(secret, ts)

    try:
        resp = requests.post(url, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise FeishuError(f"飞书推送网络失败：{type(exc).__name__}: {exc}") from exc

    try:
        data = resp.json()
    except ValueError:
        raise FeishuError(f"飞书返回非 JSON（HTTP {resp.status_code}）：{resp.text[:200]}") from None

    if data.get("code") != 0:
        raise FeishuError(
            f"飞书推送失败：code={data.get('code')} msg={data.get('msg', '')}"
        )
    return data