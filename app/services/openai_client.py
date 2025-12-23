from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from typing import Any


class OpenAIClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenAIChatResult:
    content_text: str
    json_obj: dict | list | None
    model: str


def _load_json_strict(s: str) -> dict | list:
    try:
        obj = json.loads(s)
    except Exception as e:  # noqa: BLE001
        raise OpenAIClientError(f"Failed to parse JSON from LLM response: {e}") from e
    if not isinstance(obj, (dict, list)):
        raise OpenAIClientError(f"Expected JSON object/array, got: {type(obj).__name__}")
    return obj


class OpenAIChat:
    """
    Small wrapper around the official `openai` SDK.
    - Enforces JSON output (via response_format) where possible.
    - Retries transient failures.
    """

    def __init__(self, *, api_key: str, timeout_s: float = 60.0, max_retries: int = 4) -> None:
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._client_instance = None

    def _client(self):
        try:
            from openai import OpenAI  # type: ignore
        except Exception as e:  # noqa: BLE001
            raise OpenAIClientError(
                "openai package is not installed. Add it to dependencies and reinstall."
            ) from e
        if self._client_instance is not None:
            return self._client_instance
        # openai SDK uses httpx under the hood; ensure a sane timeout.
        try:
            import httpx  # type: ignore
        except Exception:
            httpx = None  # type: ignore
        timeout = httpx.Timeout(self._timeout_s) if httpx else self._timeout_s
        # Keep SDK retries low; we implement our own backoff.
        self._client_instance = OpenAI(api_key=self._api_key, timeout=timeout, max_retries=0)
        return self._client_instance

    @staticmethod
    def _is_retryable(e: Exception) -> bool:
        name = e.__class__.__name__.lower()
        if "timeout" in name:
            return True
        if "ratelimit" in name or "rate_limit" in name:
            return True
        if "apiconnection" in name or "connection" in name:
            return True
        if "serviceunavailable" in name or "internalservererror" in name:
            return True
        # OpenAI SDK sometimes wraps httpx exceptions; check common attrs.
        status = getattr(e, "status_code", None)
        if isinstance(status, int) and status in (408, 429, 500, 502, 503, 504):
            return True
        return False

    def chat_json(self, *, model: str, system: str, user: str) -> OpenAIChatResult:
        """
        Ask the model to return a JSON object/array.
        Returns parsed JSON if possible, plus raw content.
        """
        if not self._api_key:
            raise OpenAIClientError("OPENAI_API_KEY is missing")

        last_err: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                client = self._client()
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=1,
                    response_format={"type": "json_object"},
                )
                text = (resp.choices[0].message.content or "").strip()
                # Some models wrap JSON in whitespace; keep strict.
                json_obj = _load_json_strict(text)
                return OpenAIChatResult(content_text=text, json_obj=json_obj, model=model)
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt >= self._max_retries:
                    break
                if not self._is_retryable(e):
                    break
                # Exponential backoff + jitter
                base = 0.8 * (2**attempt)
                delay = min(12.0, base) * (0.7 + 0.6 * random.random())
                time.sleep(delay)

        raise OpenAIClientError(f"OpenAI request failed: {last_err}")


