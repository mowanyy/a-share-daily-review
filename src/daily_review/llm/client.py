"""DeepSeek LLM 客户端（OpenAI 兼容协议，v0.3）。

v0.7 扩展 function-calling：新增 `chat_tools` / `ToolCall` / `ChatResult`，
供问答模式按需调用数据工具（tool.datatools）。`chat()` 签名与行为保持不变。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json

import requests

from daily_review.config import get_settings


class LLMError(RuntimeError):
    """LLM 调用错误（缺 key / 鉴权 / 限流 / 网络），错误信息可直接展示给用户。"""


@dataclass
class ToolCall:
    """模型请求的工具调用（function-calling）。"""

    id: str
    name: str
    arguments: dict              # json.loads(arguments) 的 dict
    raw: dict                    # 原始 tool_call dict（供原样回放）


@dataclass
class ChatResult:
    """chat_tools 的返回：正文 + 工具调用请求。"""

    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    reasoning_content: str | None = None
    raw_tool_calls: list[dict] | None = None   # 原样回放用的 messages 片段


def _post(
    messages: list[dict],
    *,
    api_key: str,
    model: str,
    base_url: str,
    temperature: float,
    max_tokens: int,
    timeout: float,
    tools: list[dict] | None = None,
    tool_choice: str | None = None,
) -> dict:
    """POST /chat/completions 并返回完整响应 dict（错误映射集中在此）。"""
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if tools is not None:
        payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(f"{base_url}/chat/completions", headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise LLMError(f"网络请求失败: {exc}") from exc

    if resp.status_code == 401:
        raise LLMError("DeepSeek API Key 无效（401），请检查 .env 中的 DEEPSEEK_API_KEY")
    if resp.status_code == 429:
        raise LLMError("DeepSeek 限流（429），请稍后重试")
    if resp.status_code != 200:
        raise LLMError(f"DeepSeek 返回异常: HTTP {resp.status_code} {resp.text[:300]}")

    data = resp.json()
    try:
        return data["choices"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"DeepSeek 返回格式异常: {str(data)[:300]}") from exc


def chat(
    messages: list[dict],
    *,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    timeout: float = 120,
) -> str:
    """调用 DeepSeek chat/completions，返回回复文本。

    messages: OpenAI 格式 [{"role": "system"|"user"|"assistant", "content": str}]
    """
    settings = get_settings()
    api_key = api_key or settings.llm_api_key
    model = model or settings.llm_model
    base_url = (base_url or settings.llm_base_url).rstrip("/")

    if not api_key:
        raise LLMError(
            "未配置 DEEPSEEK_API_KEY：请在项目根目录 .env 写入后重试（示例：DEEPSEEK_API_KEY=sk-xxx）"
        )

    choice = _post(
        messages,
        api_key=api_key,
        model=model,
        base_url=base_url,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    content = choice["message"].get("content")
    if not content:
        raise LLMError(f"DeepSeek 返回为空: {str(choice)[:300]}")
    return content.strip()


def chat_tools(
    messages: list[dict],
    *,
    tools: list[dict],
    tool_choice: str = "auto",
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    timeout: float = 120,
) -> ChatResult:
    """function-calling 版调用：附带 tools schema，返回正文 + 工具调用请求。

    - 仅 `deepseek-chat` 支持 tools/tool_choice；`deepseek-reasoner` 传这些参数会被上游拒绝。
    - 返回的 tool_calls 需在下一轮**原样回放**（含 raw dict），并追加 role=tool 结果消息。
    """
    settings = get_settings()
    api_key = api_key or settings.llm_api_key
    model = model or settings.llm_model
    base_url = (base_url or settings.llm_base_url).rstrip("/")

    if "reasoner" in model:
        raise LLMError(
            "模型 deepseek-reasoner 不支持函数调用（tools/tool_choice），请使用 deepseek-chat"
        )
    if not api_key:
        raise LLMError(
            "未配置 DEEPSEEK_API_KEY：请在项目根目录 .env 写入后重试（示例：DEEPSEEK_API_KEY=sk-xxx）"
        )

    choice = _post(
        messages,
        api_key=api_key,
        model=model,
        base_url=base_url,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        tools=tools,
        tool_choice=tool_choice,
    )
    msg = choice.get("message", {})
    content = (msg.get("content") or "").strip()

    tool_calls: list[ToolCall] = []
    raw_tool_calls: list[dict] = []
    for tc in msg.get("tool_calls") or []:
        raw_tool_calls.append(tc)
        fn = tc.get("function", {})
        name = fn.get("name", "")
        try:
            arguments = json.loads(fn.get("arguments") or "{}")
            if not isinstance(arguments, dict):
                arguments = {"value": arguments}
        except json.JSONDecodeError:
            arguments = {"_raw": fn.get("arguments") or ""}
        tool_calls.append(
            ToolCall(id=tc.get("id", ""), name=name, arguments=arguments, raw=tc)
        )

    return ChatResult(
        content=content,
        tool_calls=tool_calls,
        finish_reason=choice.get("finish_reason", ""),
        reasoning_content=msg.get("reasoning_content"),
        raw_tool_calls=raw_tool_calls,
    )
