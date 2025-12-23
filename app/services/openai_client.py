from __future__ import annotations

import json
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

    def __init__(self, *, api_key: str, timeout_s: float = 25.0, max_retries: int = 2) -> None:
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._max_retries = max_retries

    def _client(self):
        try:
            from openai import OpenAI  # type: ignore
        except Exception as e:  # noqa: BLE001
            raise OpenAIClientError(
                "openai package is not installed. Add it to dependencies and reinstall."
            ) from e
        return OpenAI(api_key=self._api_key, timeout=self._timeout_s)

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
                time.sleep(0.6 * (attempt + 1))

        raise OpenAIClientError(f"OpenAI request failed: {last_err}")


