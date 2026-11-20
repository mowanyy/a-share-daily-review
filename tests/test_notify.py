"""notify.py 飞书群机器人推送测试（全离线，mock requests）。"""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from daily_review.notify import FeishuError, _sign, send_feishu


class _FakeResp:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self._data


def _patch_post(monkeypatch, data, status_code=200):
    """mock requests.post，捕获 payload。"""
    import daily_review.notify as notify

    captured = {}

    def fake_post(url, json=None, timeout=None, **kw):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return _FakeResp(data, status_code)

    monkeypatch.setattr(notify.requests, "post", fake_post)
    return captured


def _set_webhook(monkeypatch, url="https://open.feishu.cn/open-apis/bot/v2/hook/test", secret=""):
    from daily_review.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "feishu_webhook_url", url)
    monkeypatch.setattr(s, "feishu_secret", secret)


# ---------------------------------------------------------------- 加签


def test_sign_is_valid_hmac():
    """_sign 输出符合飞书加签算法：base64(HMAC-SHA256(timestamp\nsecret))。"""
    secret = "test-secret"
    ts = 1700000000
    expected = base64.b64encode(
        hmac.new(f"{ts}\n{secret}".encode("utf-8"), digestmod=hashlib.sha256).digest()
    ).decode("utf-8")
    assert _sign(secret, ts) == expected


def test_sign_differs_by_timestamp():
    assert _sign("s", 100) != _sign("s", 101)


# ---------------------------------------------------------------- 请求构造


def test_send_text_no_secret(monkeypatch):
    _set_webhook(monkeypatch, secret="")
    captured = _patch_post(monkeypatch, {"code": 0, "msg": "success"})

    send_feishu("测试消息")

    assert captured["url"] == "https://open.feishu.cn/open-apis/bot/v2/hook/test"
    assert captured["json"]["msg_type"] == "text"
    assert captured["json"]["content"]["text"] == "测试消息"
    assert "sign" not in captured["json"] and "timestamp" not in captured["json"]


def test_send_text_with_secret_adds_sign(monkeypatch):
    _set_webhook(monkeypatch, secret="my-secret")
    captured = _patch_post(monkeypatch, {"code": 0, "msg": "success"})

    send_feishu("带签名消息")

    payload = captured["json"]
    assert payload["timestamp"]  # 时间戳非空
    assert payload["sign"] == _sign("my-secret", int(payload["timestamp"]))


def test_send_feishu_returns_data(monkeypatch):
    _set_webhook(monkeypatch)
    _patch_post(monkeypatch, {"code": 0, "msg": "success", "data": {}})
    assert send_feishu("x") == {"code": 0, "msg": "success", "data": {}}


# ---------------------------------------------------------------- 错误路径


def test_missing_webhook_raises(monkeypatch):
    _set_webhook(monkeypatch, url="")
    with pytest.raises(FeishuError, match="未配置"):
        send_feishu("x")


def test_feishu_error_code_raises(monkeypatch):
    _set_webhook(monkeypatch)
    _patch_post(monkeypatch, {"code": 19001, "msg": "签名校验失败"})
    with pytest.raises(FeishuError) as ei:
        send_feishu("x")
    assert "19001" in str(ei.value)
    assert "签名校验失败" in str(ei.value)


def test_network_error_raises(monkeypatch):
    _set_webhook(monkeypatch)
    import requests

    from daily_review import notify

    def boom(url, json=None, timeout=None, **kw):
        raise requests.RequestException("连接超时")

    monkeypatch.setattr(notify.requests, "post", boom)
    with pytest.raises(FeishuError, match="网络失败"):
        send_feishu("x")


def test_non_json_response_raises(monkeypatch):
    _set_webhook(monkeypatch)

    class _PlainResp:
        status_code = 200
        text = "not json"

        def json(self):
            raise ValueError("no json")

    import daily_review.notify as notify

    monkeypatch.setattr(notify.requests, "post", lambda *a, **kw: _PlainResp())
    with pytest.raises(FeishuError, match="非 JSON"):
        send_feishu("x")