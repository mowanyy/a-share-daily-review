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
    """LLM 调用错误（缺 key / 鉴权 / 限流 / 网络），错误信息可直接展示给用户。

    retryable=True 表示换个后端（兜底提供商）重试可能成功（限流/5xx/网络抖动）；
    鉴权/参数类错误（401/400/403）与数据类错误保持 retryable=False。
    """

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


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
        raise LLMError(f"网络请求失败: {exc}", retryable=True) from exc

    if resp.status_code == 401:
        raise LLMError("DeepSeek API Key 无效（401），请检查 .env 中的 DEEPSEEK_API_KEY")
    if resp.status_code == 429:
        raise LLMError("DeepSeek 限流（429），请稍后重试", retryable=True)
    if resp.status_code != 200:
        # 5xx（服务端/网关错误）可换后端重试；4xx 属请求/配置问题，不兜底
        raise LLMError(
            f"DeepSeek 返回异常: HTTP {resp.status_code} {resp.text[:300]}",
            retryable=resp.status_code >= 500,
        )

    data = resp.json()
    try:
        return data["choices"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"DeepSeek 返回格式异常: {str(data)[:300]}") from exc


def _post_fallback(
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
    """主后端请求；遇可重试错误（429/5xx/网络）且配置了兜底后端时，自动用兜底重试一次。

    兜底配置（.env，v0.12.1）：`DEEPSEEK_FALLBACK_API_KEY` / `LLM_FALLBACK_BASE_URL` /
    `LLM_FALLBACK_MODEL`。无兜底 key、或兜底与主后端相同 → 原样抛出，不重复请求。
    兜底本身再失败 → 直接抛出（错误信息如实上报，不吞）。
    """
    settings = get_settings()
    try:
        return _post(
            messages, api_key=api_key, model=model, base_url=base_url,
            temperature=temperature, max_tokens=max_tokens, timeout=timeout,
            tools=tools, tool_choice=tool_choice,
        )
    except LLMError as exc:
        if not getattr(exc, "retryable", False):
            raise
        fb_key = settings.llm_fallback_api_key
        if not fb_key or (fb_key == api_key and settings.llm_fallback_base_url == base_url):
            raise
        return _post(
            messages, api_key=fb_key, model=settings.llm_fallback_model,
            base_url=settings.llm_fallback_base_url, temperature=temperature,
            max_tokens=max_tokens, timeout=timeout, tools=tools, tool_choice=tool_choice,
        )


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

    choice = _post_fallback(
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
        # 推理模型（如 SenseNova 托管的 deepseek-v4-flash）把 max_tokens 用在
        # reasoning_content（思考）上时，正文字段会为空——给可行动的提示而非笼统报错。
        if choice["message"].get("reasoning_content") and choice.get("finish_reason") == "length":
            raise LLMError(
                "DeepSeek 返回为空：模型把 max_tokens 全部用于思考（reasoning_content），"
                "正文字段为空；请调大 max_tokens 或改用非推理模型"
            )
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

    choice = _post_fallback(
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
