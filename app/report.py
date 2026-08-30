"""顧客に渡せる診断書をMarkdownで組む。LLMは使わない。

スコアだけの紙は受け取った側が動けないので、指摘ごとに「実測値」と
「どう直すか」を必ず並べる。順番は重大度で、上から順に潰せばよい形にする。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
SEVERITY_LABEL = {"critical": "重大", "warning": "警告", "info": "改善余地"}


def to_jst(value: str | None) -> str:
    """表示は必ず日本時間。UTCのまま出すと運用時に読み違える。"""
    if not value:
        return "-"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(JST).strftime("%Y-%m-%d %H:%M JST")


def verdict(score: int) -> str:
    if score >= 90:
        return "大きな問題は見つかりませんでした。"
    if score >= 70:
        return "検索結果での見え方に直接効く改善点が残っています。"
    if score >= 50:
        return "検索評価を損なう問題が複数あります。上から順に対処してください。"
    return "検索結果に出ない、または大きく損をしている状態です。至急対応してください。"


def build_markdown(site: dict, audit: dict, advice: str | None = None) -> str:
    lines: list[str] = []
    name = site.get("name") or "サイト"
    lines.append(f"# {name} SEO診断書")
    lines.append("")
    lines.append(f"- 対象: {site.get('url', '')}")
    lines.append(f"- 実施: {to_jst(audit.get('finished_at') or audit.get('created_at'))}")
    lines.append(f"- 監査ページ数: {audit.get('page_count', 0)}")
    lines.append(f"- 総合スコア: **{audit.get('overall', 0)}点** / 100")
    lines.append("")
    lines.append(verdict(int(audit.get("overall", 0))))
    lines.append("")

    counts = audit.get("counts") or {}
    lines.append("## 指摘の内訳")
    lines.append("")
    lines.append(f"- 重大: {counts.get('critical', 0)}件")
    lines.append(f"- 警告: {counts.get('warning', 0)}件")
    lines.append(f"- 改善余地: {counts.get('info', 0)}件")
    lines.append("")

    categories = audit.get("categories") or {}
    if categories:
        lines.append("## カテゴリ別スコア")
        lines.append("")
        for item in categories.values():
            lines.append(f"- {item.get('label')}: {item.get('score')}点")
        lines.append("")

    findings = audit.get("findings") or []
    if findings:
        lines.append("## 指摘の詳細")
        lines.append("")
        for index, finding in enumerate(findings, 1):
            label = SEVERITY_LABEL.get(finding.get("severity", ""), finding.get("severity", ""))
            lines.append(f"### {index}. [{label}] {finding.get('message')}")
            lines.append("")
            lines.append(f"- 対象: {finding.get('url')}")
            lines.append(f"- 実測: {finding.get('evidence')}")
            lines.append(f"- 対応: {finding.get('action')}")
            lines.append(f"- 規則ID: `{finding.get('rule')}`")
            lines.append("")

    pages = [page for page in (audit.get("pages") or []) if not page.get("error")]
    if pages:
        lines.append("## ページ別の実測値")
        lines.append("")
        for page in pages:
            lines.append(f"### {page.get('url')}")
            lines.append("")
            lines.append(
                f"- title: 全角{page.get('title_length', 0)}文字 / "
                f"description: 全角{page.get('description_length', 0)}文字"
            )
            lines.append(
                f"- 本文: {page.get('text_length', 0)}文字 / H1: {page.get('h1_count', 0)}個 / "
                f"内部リンク: {page.get('internal_link_count', 0)}本"
            )
            lines.append(f"- 応答時間: {page.get('elapsed', 0)}秒 / HTTP {page.get('status', 0)}")
            types = page.get("jsonld_types") or []
            lines.append(f"- 構造化データ: {', '.join(types) if types else 'なし'}")
            lines.append("")

    if advice:
        lines.append("## 改善案")
        lines.append("")
        lines.append(advice.strip())
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("この診断書は Kurage SEO が自動生成しました。")
    lines.append("判定はすべて実測値にもとづく決定論的なチェックで、推測は含みません。")
    return "\n".join(lines)
