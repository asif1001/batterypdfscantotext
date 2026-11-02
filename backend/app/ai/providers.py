"""One VisionExtractor interface and three adapters.

DeepSeek uses the OpenAI-compatible chat completions API. Images are base64 data URLs.
Claude uses the Messages API with image blocks. Timeouts, model names and prices come from Settings.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass

import httpx


@dataclass
class ProviderResponse:
    text: str
    input_tokens: int
    output_tokens: int
    provider: str
    model: str


class VisionExtractor:
    name = "base"

    def __init__(self, *, api_key: str, base_url: str, model: str, timeout: float):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def chat(self, system: str, user_text: str, images: list[bytes]) -> ProviderResponse:
        raise NotImplementedError

    def _post_with_retry(self, url: str, headers: dict, payload: dict) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = httpx.post(url, headers=headers, json=payload, timeout=self.timeout)
            except httpx.HTTPError as exc:
                last_error = exc
                time.sleep(2**attempt)
                continue
            if response.status_code in {429, 500, 502, 503, 504}:
                last_error = httpx.HTTPStatusError(
                    f"{self.name} returned {response.status_code}",
                    request=response.request,
                    response=response,
                )
                time.sleep(2**attempt)
                continue
            response.raise_for_status()
            return response
        raise RuntimeError(f"{self.name} failed after retries: {last_error}")


class OpenAIExtractor(VisionExtractor):
    name = "openai"

    def chat(self, system: str, user_text: str, images: list[bytes]) -> ProviderResponse:
        content: list[dict] = [{"type": "text", "text": user_text}]
        for image in images:
            encoded = base64.b64encode(image).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}})
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
        }
        response = self._post_with_retry(
            f"{self.base_url}/chat/completions",
            {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            payload,
        )
        body = response.json()
        usage = body.get("usage") or {}
        text = body["choices"][0]["message"]["content"]
        return ProviderResponse(
            text=text or "",
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            provider=self.name,
            model=self.model,
        )


class DeepSeekExtractor(OpenAIExtractor):
    """DeepSeek's chat completions API is OpenAI-compatible. The vision model is deepseek-flash."""

    name = "deepseek"


class ClaudeExtractor(VisionExtractor):
    name = "anthropic"

    def chat(self, system: str, user_text: str, images: list[bytes]) -> ProviderResponse:
        content: list[dict] = []
        for image in images:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": base64.b64encode(image).decode("ascii"),
                    },
                }
            )
        content.append({"type": "text", "text": user_text})
        payload = {
            "model": self.model,
            "max_tokens": 4096,
            "temperature": 0,
            "system": system,
            "messages": [{"role": "user", "content": content}],
        }
        response = self._post_with_retry(
            f"{self.base_url}/v1/messages",
            {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            payload,
        )
        body = response.json()
        usage = body.get("usage") or {}
        parts = body.get("content") or []
        text = "\n".join(part.get("text", "") for part in parts if part.get("type") == "text")
        return ProviderResponse(
            text=text,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            provider=self.name,
            model=self.model,
        )


def build_extractor(name: str, provider_config: dict, api_key: str) -> VisionExtractor:
    kwargs = {
        "api_key": api_key,
        "base_url": provider_config.get("base_url") or "",
        "model": provider_config.get("model") or "",
        "timeout": float(provider_config.get("timeout") or 90),
    }
    if name == "anthropic":
        return ClaudeExtractor(**kwargs)
    if name == "deepseek":
        return DeepSeekExtractor(**kwargs)
    return OpenAIExtractor(**kwargs)
