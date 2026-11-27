"""LLM client 行为测试：chat() 空 content 报错路径（v0.12.1 推理模型适配）。

推理模型（如 SenseNova 托管的 deepseek-v4-flash）会把 max_tokens 用在
reasoning_content（思考）上导致正文字段为空——此时应给出可行动提示而非笼统报错。
"""

import pytest

from daily_review.config import get_settings
from daily_review.llm import client
from daily_review.llm.client import LLMError


_PRIMARY = {"api_key": "sk-primary", "base_url": "https://token.sensenova.cn/v1", "model": "deepseek-v4-flash"}


def _post_fake(raise_for_key=None, retryable=True, content="ok"):
    """构造一个按 api_key 区分行为的假 _post：主 key 可抛错，兜底 key 返回成功。"""
    calls = []

    def fake(messages, *, api_key, model, base_url, temperature, max_tokens, timeout,
             tools=None, tool_choice=None):
        calls.append({"api_key": api_key, "model": model, "base_url": base_url})
        if raise_for_key is not None and api_key == raise_for_key:
            raise LLMError("可重试错误", retryable=retryable)
        return {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}

    fake.calls = calls
    return fake


def _make_choice(content: str = "", reasoning_content: str = "", finish_reason: str = "stop") -> dict:
    message = {"role": "assistant", "content": content}
    if reasoning_content:
        message["reasoning_content"] = reasoning_content
    return {"index": 0, "message": message, "finish_reason": finish_reason}


def _patch_post(monkeypatch, choice: dict) -> None:
    """把 client._post 替换为固定返回，chat() 不再真正联网。"""
    monkeypatch.setattr(client, "_post", lambda *a, **kw: choice)


class TestChatEmptyContent:
    def test_normal_content_returned(self, monkeypatch):
        _patch_post(monkeypatch, _make_choice(content="  你好  "))
        assert client.chat([{"role": "user", "content": "hi"}]) == "你好"

    def test_empty_content_raises_generic(self, monkeypatch):
        _patch_post(monkeypatch, _make_choice(content="", finish_reason="stop"))
        with pytest.raises(LLMError) as ei:
            client.chat([{"role": "user", "content": "hi"}])
        assert "返回为空" in str(ei.value)
        assert "reasoning_content" not in str(ei.value)

    def test_reasoning_length_gives_actionable_hint(self, monkeypatch):
        _patch_post(
            monkeypatch,
            _make_choice(content="", reasoning_content="思考了很久", finish_reason="length"),
        )
        with pytest.raises(LLMError) as ei:
            client.chat([{"role": "user", "content": "hi"}])
        msg = str(ei.value)
        assert "reasoning_content" in msg
        assert "max_tokens" in msg
        # v0.20.3：思考占满预算 → 可重试，自动切兜底非推理模型
        assert ei.value.retryable is True


class TestFallbackProvider:
    """v0.12.1：主后端（商汤 SenseNova）可重试失败时自动切兜底后端（官方 DeepSeek）。"""

    def _set_fallback(self, monkeypatch, fb_key="sk-fallback"):
        s = get_settings()
        monkeypatch.setattr(s, "llm_fallback_api_key", fb_key)
        monkeypatch.setattr(s, "llm_fallback_base_url", "https://api.deepseek.com")
        monkeypatch.setattr(s, "llm_fallback_model", "deepseek-chat")
        return s

    def test_retryable_primary_failure_uses_fallback(self, monkeypatch):
        self._set_fallback(monkeypatch)
        fake = _post_fake(raise_for_key="sk-primary")
        monkeypatch.setattr(client, "_post", fake)
        out = client.chat([{"role": "user", "content": "hi"}], **_PRIMARY)
        assert out == "ok"
        keys = [c["api_key"] for c in fake.calls]
        assert keys == ["sk-primary", "sk-fallback"]

    def test_success_no_fallback_call(self, monkeypatch):
        self._set_fallback(monkeypatch)
        fake = _post_fake()
        monkeypatch.setattr(client, "_post", fake)
        out = client.chat([{"role": "user", "content": "hi"}], **_PRIMARY)
        assert out == "ok"
        assert [c["api_key"] for c in fake.calls] == ["sk-primary"]

    def test_no_fallback_key_raises_without_retry(self, monkeypatch):
        self._set_fallback(monkeypatch, fb_key="")  # 未配置兜底 key
        fake = _post_fake(raise_for_key="sk-primary")
        monkeypatch.setattr(client, "_post", fake)
        with pytest.raises(LLMError):
            client.chat([{"role": "user", "content": "hi"}], **_PRIMARY)
        assert len(fake.calls) == 1  # 只请求一次，不重复

    def test_non_retryable_error_no_fallback(self, monkeypatch):
        self._set_fallback(monkeypatch)
        fake = _post_fake(raise_for_key="sk-primary", retryable=False)  # 401 类错误
        monkeypatch.setattr(client, "_post", fake)
        with pytest.raises(LLMError) as ei:
            client.chat([{"role": "user", "content": "hi"}], **_PRIMARY)
        assert getattr(ei.value, "retryable", False) is False
        assert len(fake.calls) == 1

    def test_fallback_same_as_primary_no_retry(self, monkeypatch):
        # 兜底与主后端相同（同 key 同 base_url）→ 不重复请求
        self._set_fallback(monkeypatch, fb_key="sk-primary")
        s = get_settings()
        monkeypatch.setattr(s, "llm_fallback_base_url", _PRIMARY["base_url"])
        fake = _post_fake(raise_for_key="sk-primary")
        monkeypatch.setattr(client, "_post", fake)
        with pytest.raises(LLMError):
            client.chat([{"role": "user", "content": "hi"}], **_PRIMARY)
        assert len(fake.calls) == 1

    def test_reasoning_budget_exhausted_falls_back(self, monkeypatch):
        """v0.20.3：推理模型思考占满 max_tokens → 自动切兜底非推理模型重试并返回正文。"""
        self._set_fallback(monkeypatch)
        calls = []

        def fake(messages, *, api_key, model, base_url, temperature, max_tokens, timeout,
                 tools=None, tool_choice=None):
            calls.append({"api_key": api_key, "model": model})
            if api_key == "sk-primary":
                # 推理模型把预算全花在思考上 → 正文为空
                return _make_choice(content="", reasoning_content="思考了很久", finish_reason="length")
            # 兜底（非推理 deepseek-chat）正常返回
            return _make_choice(content="兜底后生成的完整正文")

        monkeypatch.setattr(client, "_post", fake)
        out = client.chat([{"role": "user", "content": "hi"}], **_PRIMARY)
        assert out == "兜底后生成的完整正文"
        assert [c["api_key"] for c in calls] == ["sk-primary", "sk-fallback"]
        assert calls[1]["model"] == "deepseek-chat"  # 兜底是非推理模型

    def test_chat_tools_fallback_retries_with_tools(self, monkeypatch):
        self._set_fallback(monkeypatch)
        calls = []
        tools_schema = [{"type": "function", "function": {"name": "x"}}]

        def fake(messages, *, api_key, model, base_url, temperature, max_tokens, timeout,
                 tools=None, tool_choice=None):
            calls.append({"api_key": api_key, "tools": tools, "tool_choice": tool_choice})
            if api_key == "sk-primary":
                raise LLMError("限流 429", retryable=True)
            # 兜底后端返回一次工具调用
            return {"index": 0, "message": {"role": "assistant", "content": "",
                                            "tool_calls": [{"id": "c1", "type": "function",
                                                            "function": {"name": "x", "arguments": "{}"}}]},
                    "finish_reason": "tool_calls"}

        monkeypatch.setattr(client, "_post", fake)
        res = client.chat_tools([{"role": "user", "content": "hi"}], tools=tools_schema, **_PRIMARY)
        assert len(res.tool_calls) == 1
        assert [c["api_key"] for c in calls] == ["sk-primary", "sk-fallback"]
        assert calls[1]["tools"] == tools_schema  # 兜底重试同样带 tools schema


class TestPostErrorRetryableFlag:
    def _patch_resp(self, monkeypatch, status_code, text=""):
        class _Resp:
            def __init__(self, code, txt):
                self.status_code = code
                self.text = txt
        monkeypatch.setattr(client.requests, "post", lambda *a, **kw: _Resp(status_code, text))

    def test_429_retryable(self, monkeypatch):
        self._patch_resp(monkeypatch, 429)
        with pytest.raises(LLMError) as ei:
            client._post([{"role": "user", "content": "hi"}], api_key="k", model="m",
                         base_url="https://x", temperature=0, max_tokens=10, timeout=10)
        assert ei.value.retryable is True

    def test_500_retryable(self, monkeypatch):
        self._patch_resp(monkeypatch, 500)
        with pytest.raises(LLMError) as ei:
            client._post([{"role": "user", "content": "hi"}], api_key="k", model="m",
                         base_url="https://x", temperature=0, max_tokens=10, timeout=10)
        assert ei.value.retryable is True

    def test_401_not_retryable(self, monkeypatch):
        self._patch_resp(monkeypatch, 401)
        with pytest.raises(LLMError) as ei:
            client._post([{"role": "user", "content": "hi"}], api_key="k", model="m",
                         base_url="https://x", temperature=0, max_tokens=10, timeout=10)
        assert ei.value.retryable is False

    def test_json_decode_error_retryable(self, monkeypatch):
        """B3：HTTP 200 但 body 非 JSON → _post 抛 LLMError(retryable=True)，走兜底。"""
        from json import JSONDecodeError

        class _BadResp:
            status_code = 200
            text = "not-json-garbage"

            @staticmethod
            def json():
                raise JSONDecodeError("Expecting value", "not json", 0)

        monkeypatch.setattr(client.requests, "post", lambda *a, **kw: _BadResp())
        with pytest.raises(LLMError) as ei:
            client._post([{"role": "user", "content": "hi"}], api_key="k", model="m",
                         base_url="https://x", temperature=0, max_tokens=10, timeout=10)
        assert ei.value.retryable is True
        assert "非 JSON" in str(ei.value)

    def test_json_decode_error_triggers_fallback(self, monkeypatch):
        """B3：主后端 JSONDecodeError → 自动切兜底重试，返回兜底回复。"""
        s = get_settings()
        monkeypatch.setattr(s, "llm_fallback_api_key", "sk-fallback")
        monkeypatch.setattr(s, "llm_fallback_base_url", "https://api.deepseek.com")
        monkeypatch.setattr(s, "llm_fallback_model", "deepseek-chat")
        calls = []

        def fake(messages, *, api_key, model, base_url, temperature, max_tokens, timeout,
                 tools=None, tool_choice=None):
            calls.append({"api_key": api_key, "model": model})
            if api_key == "sk-primary":
                raise LLMError("主后端返回非JSON", retryable=True)
            return {"index": 0, "message": {"role": "assistant", "content": "兜底成功"}, "finish_reason": "stop"}

        monkeypatch.setattr(client, "_post", fake)
        out = client.chat([{"role": "user", "content": "hi"}], **_PRIMARY)
        assert out == "兜底成功"
        assert [c["api_key"] for c in calls] == ["sk-primary", "sk-fallback"]
