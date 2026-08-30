"""監査結果を、そのまま直せる日本語の改善案に変える。ここだけLLMを使う。

判定(audit_rules)にLLMを入れない代わりに、ここでは実測済みの指摘を
渡して「文章を書かせるだけ」にしている。事実はすべてプロンプトの中に
あるので、モデルが数字を作る余地を減らせる。

無料枠と管理者は自社GPUのGemma、課金された実行だけDeepSeek。
無料枠でDeepSeekを呼ぶとAPI原価がそのまま赤字になり、ローカルGPUを
持っている利点も消える(kgeoで確立した方針をそのまま踏襲)。
"""

from __future__ import annotations

from pathlib import Path

import httpx

from . import config, rqdb4ai_client

SYSTEM_PROMPT = """あなたは日本語サイトのSEOを改善する技術者です。
渡された監査結果だけを根拠に、実際に手を動かせる改善案を書いてください。

守ること:
- 監査結果に無い事実を書かない。検索順位・検索ボリューム・競合の状況は渡されていないので触れない。
- 「重要です」「対策しましょう」のような一般論を書かない。どのファイルの何をどう変えるかを書く。
- titleとmeta descriptionの改善案は、必ず書き換え後の完成文を出す。日本語は全角32文字(title)、
  全角120文字(description)以内。
- 出力はMarkdownの見出しと箇条書き。表は使わない。
- 全体で1200文字程度。

構成:
## 最優先で直す3つ
## title・descriptionの書き換え案
## 次に着手すること
"""


def normalize_owner(owner: str) -> str:
    return (owner or "").strip().lstrip("@").lower()


def provider_for(owner: str, *, paid: bool = True) -> str:
    if normalize_owner(owner) in config.ADMIN_USERS:
        return "ollama"
    return "deepseek" if paid else "ollama"


def _read_key_file(path: str, name: str) -> str:
    if not path:
        return ""
    try:
        for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == name:
                return value.strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def deepseek_api_key() -> str:
    return config.DEEPSEEK_API_KEY or _read_key_file(
        config.DEEPSEEK_API_KEY_FILE, config.DEEPSEEK_API_KEY_NAME
    )


def configured(owner: str, *, paid: bool = True) -> bool:
    if provider_for(owner, paid=paid) == "ollama":
        # rqdb4aiを設定していなければ直叩き(192.168.0.3)で動く。
        return rqdb4ai_client.configured() or bool(config.OLLAMA_BASE_URL and config.OLLAMA_MODEL)
    return bool(config.DEEPSEEK_BASE_URL and config.DEEPSEEK_MODEL and deepseek_api_key())


def build_prompt(site_name: str, url: str, result: dict) -> str:
    """監査の実測値だけを渡す。ページ本文は渡さない(長さと個人情報の両面で)。"""
    score = result["score"]
    lines = [
        f"サイト名: {site_name}",
        f"対象URL: {url}",
        f"総合スコア: {score['overall']}点 / 監査ページ数: {score['pages']}",
        "",
        "カテゴリ別スコア:",
    ]
    for item in score["categories"].values():
        lines.append(f"- {item['label']}: {item['score']}点")

    lines.append("")
    lines.append("検出した指摘(重大度 / 規則 / 内容 / 実測値):")
    order = {"critical": 0, "warning": 1, "info": 2}
    findings = sorted(result["findings"], key=lambda f: order.get(f.severity, 3))
    for finding in findings[:40]:
        lines.append(
            f"- [{finding.severity}] {finding.rule} / {finding.message} / 実測: {finding.evidence[:160]}"
        )

    lines.append("")
    lines.append("ページ実測値:")
    for page in result["pages"][:15]:
        if page.get("error"):
            continue
        lines.append(
            f"- {page['url']} title「{page['title']}」(全角{page['title_length']}字) / "
            f"description 全角{page['description_length']}字 / 本文{page['text_length']}字 / "
            f"H1 {page['h1_count']}個 / 応答{page['elapsed']}秒"
        )
    return "\n".join(lines)


async def _run_ollama_direct(messages: list[dict[str, str]]) -> tuple[str, str, str]:
    """192.168.0.3 のOllamaを直接叩く。ローカル稼働時の既定経路。

    gemma4 は思考型なので `"think": False` を必ず送る。付けないと隠れ推論
    トークンが num_predict を食い潰し、response が空で done_reason=length に
    なる。これをデーモン故障と誤診しないこと。
    """
    payload = {
        "model": config.OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "think": False,
        "options": {"temperature": 0.2, "num_predict": 1600},
    }
    async with httpx.AsyncClient(timeout=config.OLLAMA_TIMEOUT) as client:
        response = await client.post(f"{config.OLLAMA_BASE_URL}/api/chat", json=payload)
        response.raise_for_status()
        body = response.json()
    text = str((body.get("message") or {}).get("content") or "").strip()
    if not text:
        raise RuntimeError(
            f"Ollama returned an empty response (done_reason={body.get('done_reason')})"
        )
    return text, "ollama", config.OLLAMA_MODEL


async def _run_ollama(messages: list[dict[str, str]]) -> tuple[str, str, str]:
    if rqdb4ai_client.configured():
        text, _job_id = await rqdb4ai_client.run_ollama_chat(messages)
        return text, "ollama-rqdb4ai", config.OLLAMA_MODEL
    return await _run_ollama_direct(messages)


async def _run_deepseek(messages: list[dict[str, str]]) -> tuple[str, str, str]:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {deepseek_api_key()}",
    }
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 2048,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=config.DEEPSEEK_TIMEOUT) as client:
        response = await client.post(
            f"{config.DEEPSEEK_BASE_URL}/chat/completions", headers=headers, json=payload
        )
        response.raise_for_status()
        body = response.json()
    choice = (body.get("choices") or [{}])[0]
    text = str((choice.get("message") or {}).get("content") or "").strip()
    if not text:
        raise RuntimeError(
            f"DeepSeek returned an empty response ({choice.get('finish_reason', 'unknown')})"
        )
    return text, "deepseek", config.DEEPSEEK_MODEL


async def generate(
    owner: str, site_name: str, url: str, result: dict, *, paid: bool = True
) -> tuple[str, str, str]:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_prompt(site_name, url, result)},
    ]
    if provider_for(owner, paid=paid) == "ollama":
        return await _run_ollama(messages)
    return await _run_deepseek(messages)
