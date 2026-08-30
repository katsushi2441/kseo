from __future__ import annotations

import json
import os
from typing import Any

import requests


def ollama_chat_job(
    messages: list[dict[str, str]],
    model: str = "gemma4:12b-it-qat",
    temperature: float = 0.2,
    num_predict: int = 1600,
    request_timeout: int = 180,
    source: str = "kseo",
    **_: Any,
) -> dict[str, Any]:
    """RQDB4AI worker entrypoint for KGeo's grounded Gemma 4 evaluation."""

    if not isinstance(messages, list) or not messages:
        raise RuntimeError("messages are required")
    serialized = json.dumps(messages, ensure_ascii=False)
    if len(serialized) > 120_000:
        raise RuntimeError("messages are too large")
    ollama_url = os.environ.get("KSEO_OLLAMA_BASE_URL", "http://192.168.0.14:11434").rstrip("/")
    payload = {
        "model": str(model),
        "messages": messages,
        "stream": False,
        "think": False,
        "options": {
            "temperature": float(temperature),
            "num_predict": int(num_predict),
        },
    }
    response = requests.post(
        f"{ollama_url}/api/chat",
        json=payload,
        timeout=max(30, int(request_timeout)),
    )
    response.raise_for_status()
    body = response.json()
    text = str((body.get("message") or {}).get("content") or "").strip()
    if not text:
        raise RuntimeError(
            f"Ollama returned an empty response ({body.get('done_reason', 'unknown')})"
        )
    return {
        "ok": True,
        "status": "completed",
        "completion_scope": "business_result",
        "business_terminal": True,
        "items": 1,
        "response": text,
        "response_chars": len(text),
        "model": str(model),
        "source": source,
        "ollama_host": "192.168.0.14",
        "note": "KGeo grounded evaluation completed through the 0.14 Ollama queue",
    }
