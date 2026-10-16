"""OpenAI-compatible chat completions (Kimi by default) for the processing layer.

Every failure raises: a processing run must never publish a half-translated
result as if it were complete.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.moonshot.cn/v1"
DEFAULT_API_KEY_ENV = "KIMI_API_KEY"


class LlmError(RuntimeError):
    pass


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def to_dict(self) -> dict[str, int]:
        return {"calls": self.calls, "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens}


@dataclass
class ChatClient:
    model: str
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    # Extra request fields, e.g. {"thinking": {"type": "disabled"}} for Kimi K2.x.
    extra_body: dict[str, Any] = field(default_factory=dict)
    timeout: float = 120.0
    max_attempts: int = 3
    transport: httpx.BaseTransport | None = None
    usage: Usage = field(default_factory=Usage)

    @classmethod
    def from_config(cls, llm: dict[str, Any], *, transport: httpx.BaseTransport | None = None) -> "ChatClient":
        key_env = str(llm.get("api_key_env") or DEFAULT_API_KEY_ENV)
        api_key = os.environ.get(key_env, "").strip()
        if not api_key:
            raise LlmError(f"{key_env} is not set")
        model = str(llm.get("model") or "").strip()
        if not model:
            raise LlmError("llm.model is required")
        return cls(
            model=model,
            base_url=str(llm.get("base_url") or DEFAULT_BASE_URL).rstrip("/"),
            api_key=api_key,
            extra_body=dict(llm.get("extra_body") or {}),
            transport=transport,
        )

    def complete(self, system: str, user: str, *, max_tokens: int = 2000) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            **self.extra_body,
        }
        last: Exception | None = None
        with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
            for attempt in range(self.max_attempts):
                try:
                    response = client.post(
                        f"{self.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json=payload,
                    )
                except httpx.TransportError as exc:
                    last = exc
                else:
                    if response.status_code == 200:
                        return self._content(response.json())
                    if response.status_code not in (429, 500, 502, 503, 504):
                        raise LlmError(f"LLM HTTP {response.status_code}: {response.text[:300]}")
                    last = LlmError(f"LLM HTTP {response.status_code}: {response.text[:300]}")
                if attempt < self.max_attempts - 1:
                    time.sleep(2 ** (attempt + 1))
        raise LlmError(f"LLM request failed after {self.max_attempts} attempts: {last}")

    def _content(self, body: Any) -> str:
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError(f"unexpected LLM response: {str(body)[:300]}") from exc
        usage = body.get("usage") or {}
        self.usage.calls += 1
        self.usage.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.usage.completion_tokens += int(usage.get("completion_tokens") or 0)
        text = str(content or "").strip()
        if not text:
            raise LlmError("LLM returned an empty answer")
        return text
