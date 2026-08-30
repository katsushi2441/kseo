from __future__ import annotations

import asyncio
from typing import Any

from urllib.parse import urlparse

import httpx

from . import config


def _ollama_host() -> str:
    """キュー名に使うホスト。rqdb4aiはホスト別に直列化してGPU競合を防ぐので、
    ここを固定値にすると設定を変えても同じキューへ流れてしまう。"""
    return urlparse(config.OLLAMA_BASE_URL).hostname or "localhost"


def configured() -> bool:
    return bool(
        config.RQDB4AI_URL
        and config.RQDB4AI_TOKEN
        and config.RQDB4AI_FUNCTION
        and config.OLLAMA_MODEL
    )


def enqueue_payload(messages: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "queue": "auto",
        "function": config.RQDB4AI_FUNCTION,
        "kwargs": {
            "messages": messages,
            "model": config.OLLAMA_MODEL,
            "temperature": 0.2,
            "num_predict": 1600,
            "request_timeout": int(config.OLLAMA_TIMEOUT),
            "source": "kseo",
        },
        "meta": {
            "project": "kseo",
            "app": "kseo",
            "kind": "ollama_chat",
            "resource": "ollama",
            "resource_key": f"ollama:{_ollama_host()}:{config.OLLAMA_MODEL}",
            "ollama_host": _ollama_host(),
            "ollama_endpoint": config.OLLAMA_BASE_URL,
            "ollama_model": config.OLLAMA_MODEL,
            "source": "web_online",
            "queue_class": "web",
            "priority_class": "interactive",
        },
        "timeout": int(config.OLLAMA_TIMEOUT) + 60,
        "result_ttl": 3600,
        "failure_ttl": 604800,
    }


def _headers() -> dict[str, str]:
    if not configured():
        raise RuntimeError("RQDB4AI Ollama queue is not configured")
    return {
        "Authorization": f"Bearer {config.RQDB4AI_TOKEN}",
        "Content-Type": "application/json",
    }


async def run_ollama_chat(messages: list[dict[str, str]]) -> tuple[str, str]:
    """Enqueue Gemma 4 on the 0.14 web queue and wait for its real result."""

    timeout = httpx.Timeout(30.0, connect=10.0)
    headers = _headers()
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{config.RQDB4AI_URL}/api/enqueue",
            headers=headers,
            json=enqueue_payload(messages),
        )
        response.raise_for_status()
        queued = response.json()
        job_id = str((queued.get("job") or {}).get("id") or "")
        if not job_id:
            raise RuntimeError("RQDB4AI enqueue returned no job id")

        deadline = asyncio.get_running_loop().time() + config.RQDB4AI_WAIT_TIMEOUT
        status = "queued"
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(config.RQDB4AI_POLL_INTERVAL)
            detail_response = await client.get(
                f"{config.RQDB4AI_URL}/api/jobs/{job_id}", headers=headers
            )
            detail_response.raise_for_status()
            detail = detail_response.json().get("job") or {}
            status = str(detail.get("status") or "unknown")
            if status == "finished":
                result_response = await client.get(
                    f"{config.RQDB4AI_URL}/api/jobs/{job_id}/result", headers=headers
                )
                result_response.raise_for_status()
                result = result_response.json().get("result")
                text = str((result or {}).get("response") or "").strip()
                if not isinstance(result, dict) or not result.get("ok") or not text:
                    raise RuntimeError(f"RQDB4AI returned an invalid Ollama result ({job_id})")
                return text, job_id
            if status in {"failed", "stopped", "canceled"}:
                error = detail.get("exc_info") or detail.get("error") or status
                raise RuntimeError(f"RQDB4AI Ollama job {job_id} {status}: {str(error)[:500]}")
    raise RuntimeError(f"RQDB4AI Ollama job timed out ({job_id}, status={status})")
