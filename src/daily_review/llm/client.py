"""DeepSeek LLM 客户端（OpenAI 兼容协议，v0.3）。"""

from __future__ import annotations

import requests

from daily_review.config import get_settings


class LLMError(RuntimeError):
    """LLM 调用错误（缺 key / 鉴权 / 限流 / 网络），错误信息可直接展示给用户。"""


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

    url = f"{base_url}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
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
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"DeepSeek 返回格式异常: {str(data)[:300]}") from exc
