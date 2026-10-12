from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from app.config import Settings


class DeepSeekError(Exception):
    pass


class DeepSeekClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def stream(
        self, messages: list[dict], temperature: float
    ) -> AsyncIterator[tuple[str, str]]:
        api_key = self.settings.api_key
        if not api_key:
            raise DeepSeekError("DeepSeek API key is not configured")
        payload = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self.settings.max_tokens,
            "stream": True,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.settings.base_url}/chat/completions"
        timeout = httpx.Timeout(connect=20, read=180, write=30, pool=20)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                if response.status_code >= 400:
                    raw = (await response.aread()).decode("utf-8", "replace")[:800]
                    raise DeepSeekError(f"DeepSeek API {response.status_code}: {raw}")
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        if data == "[DONE]":
                            break
                        continue
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("error"):
                        raise DeepSeekError(str(obj["error"]))
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    reasoning = delta.get("reasoning_content") or ""
                    content = delta.get("content") or ""
                    if reasoning:
                        yield "reasoning", reasoning
                    if content:
                        yield "content", content

    async def complete(self, messages: list[dict], temperature: float = 0.3) -> str:
        api_key = self.settings.api_key
        if not api_key:
            raise DeepSeekError("DeepSeek API key is not configured")
        payload = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self.settings.max_tokens,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.settings.base_url}/chat/completions"
        timeout = httpx.Timeout(connect=20, read=180, write=30, pool=20)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.RequestError as exc:
            raise DeepSeekError("Could not reach the DeepSeek API") from exc
        if response.status_code >= 400:
            raw = response.text[:800]
            raise DeepSeekError(f"DeepSeek API {response.status_code}: {raw}")
        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise DeepSeekError("DeepSeek returned an invalid completion") from exc
        if not isinstance(content, str) or not content.strip():
            raise DeepSeekError("DeepSeek returned an empty prompt")
        return content.strip()
