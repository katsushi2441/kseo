"""Google検索向けの技術SEO判定。LLMは使わない。

判定にLLMを使わない理由はkgeoと同じで、同じページを2回見て違う答えが
出る道具は監査として売り物にならないため。ここは「測って、閾値と比べて、
根拠の数字を添える」だけに徹し、LLMは advice_service の改善文だけに使う。

日本語サイト向けの閾値を持つのがこの実装の主眼。英語圏のツールは
title 60文字/description 160文字を前提にするが、日本語は全角で数えるので
そのまま当てると「短すぎる」と誤判定する。
"""

from __future__ import annotations

import dataclasses
import math
import re
import unicodedata
from urllib.parse import urljoin, urlparse

# --- 日本語サイト向けの閾値 -------------------------------------------------
# Googleの検索結果は画素幅で切るため文字数は目安。全角を1文字として数えた
# ときの実務値を採る。全角32文字でおよそ切れ始める。
TITLE_MAX = 32
TITLE_MIN = 10
# description は全角120文字前後で切られる。空より短いほうがましなので下限も見る。
DESC_MAX = 120
DESC_MIN = 50
# 本文が薄いページは検索意図を満たせない。日本語は情報密度が高いので英語の
# 300語基準より低くてよいが、300文字を切るとほぼ確実に評価されない。
THIN_CONTENT_CHARS = 300
# 1ページあたりのリンクが多すぎると評価が薄まる。実務上の警戒線。
MAX_LINKS = 200
# 同期スクリプトは描画を止める。3本を超えると体感で分かる差になる。
MAX_BLOCKING_SCRIPTS = 3
# 表示までの実測秒数。1.5秒を超えると離脱が跳ねる。
SLOW_RESPONSE_SEC = 1.5
VERY_SLOW_RESPONSE_SEC = 3.0

SEVERITY_WEIGHT = {"critical": 25, "warning": 10, "info": 3}

CATEGORY_LABELS = {
    "crawl": "クロール・インデックス",
    "meta": "タイトル・説明文",
    "heading": "見出し構造",
    "structured": "構造化データ",
    "social": "SNS表示(OGP)",
    "content": "コンテンツ",
    "mobile": "モバイル対応",
    "speed": "表示速度",
    "security": "HTTPS・安全性",
    "japanese": "日本語サイト固有",
}


@dataclasses.dataclass
class Finding:
    """1件の指摘。root causeと実測値を必ず持たせる。

    message だけだと「で、うちは何文字なの」に答えられないので、
    evidence に実際に測った値を入れることを必須にしている。
    """

    rule: str
    category: str
    severity: str  # critical / warning / info
    url: str
    message: str
    evidence: str
    action: str

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def visible_length(text: str) -> int:
    """検索結果での見え方に近い文字数。

    全角(W/F/A)は1文字、半角は0.5文字として数え、切り上げる。
    「AI活用」のような英数字混じりの日本語タイトルを、半角基準でも
    全角基準でも誤らずに測るための近似。
    """
    if not text:
        return 0
    width = 0.0
    for char in text:
        width += 1.0 if unicodedata.east_asian_width(char) in ("W", "F", "A") else 0.5
    return int(width + 0.999)


# 日本語ページと見なす下限。英語ページの言語切替リンク(「日本語」の3文字)や
# 社名の1語だけで日本語ページ扱いすると、lang="en" が正しいページに
# 「lang不一致」を出してしまう(自社LPで実際に出した)。
JAPANESE_MIN_CHARS = 40
JAPANESE_MIN_RATIO = 0.08


def has_japanese(text: str) -> bool:
    """本文が日本語で書かれていると言えるか。数文字の混在では真としない。"""
    value = text or ""
    if not value:
        return False
    hits = len(re.findall(r"[ぁ-んァ-ヶ一-龥]", value))
    if hits < JAPANESE_MIN_CHARS:
        return False
    return hits / len(value) >= JAPANESE_MIN_RATIO


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


# --- ページ単位の判定 -------------------------------------------------------


def check_meta(page, findings: list[Finding]) -> None:
    url = page.url
    title = _norm(page.title)
    if not title:
        findings.append(Finding(
            "meta.title.missing", "meta", "critical", url,
            "titleタグがありません。検索結果に出す見出しが無い状態です。",
            "title: (空)",
            "そのページ固有の内容を表すtitleを設定してください。",
        ))
    else:
        length = visible_length(title)
        if length > TITLE_MAX:
            findings.append(Finding(
                "meta.title.too_long", "meta", "warning", url,
                f"titleが長く、検索結果で末尾が切れます(全角{TITLE_MAX}文字が目安)。",
                f"全角換算{length}文字: {title}",
                f"重要な語を前half分に寄せ、全角{TITLE_MAX}文字以内に収めてください。",
            ))
        elif length < TITLE_MIN:
            findings.append(Finding(
                "meta.title.too_short", "meta", "warning", url,
                "titleが短く、何のページか検索結果から判断できません。",
                f"全角換算{length}文字: {title}",
                "サービス名だけでなく、そのページで解決することを入れてください。",
            ))

    desc = _norm(page.description)
    if not desc:
        findings.append(Finding(
            "meta.description.missing", "meta", "warning", url,
            "meta descriptionがありません。検索結果の説明文をGoogleに任せている状態です。",
            "description: (空)",
            "ページ内容を要約した説明文を設定してください。クリック率に直結します。",
        ))
    else:
        length = visible_length(desc)
        if length > DESC_MAX:
            findings.append(Finding(
                "meta.description.too_long", "meta", "info", url,
                f"meta descriptionが長く、末尾が切れます(全角{DESC_MAX}文字が目安)。",
                f"全角換算{length}文字",
                f"最も伝えたい一文を先頭に置き、全角{DESC_MAX}文字以内にしてください。",
            ))
        elif length < DESC_MIN:
            findings.append(Finding(
                "meta.description.too_short", "meta", "info", url,
                "meta descriptionが短く、内容を伝えきれていません。",
                f"全角換算{length}文字: {desc}",
                f"全角{DESC_MIN}〜{DESC_MAX}文字で、誰の何を解決するかを書いてください。",
            ))

    if not page.canonical:
        findings.append(Finding(
            "meta.canonical.missing", "meta", "info", url,
            "canonical URLが指定されていません。同じ内容が複数URLで見えると評価が割れます。",
            "link[rel=canonical]: なし",
            "正規URLをcanonicalで明示してください。",
        ))
    elif page.canonical_conflict:
        findings.append(Finding(
            "meta.canonical.conflict", "meta", "warning", url,
            "canonicalが別ページを指しています。このページは検索結果に出ません。",
            f"canonical: {page.canonical}",
            "意図した統合でなければ、canonicalを自分自身のURLに直してください。",
        ))


def check_crawl(page, findings: list[Finding]) -> None:
    url = page.url
    if page.noindex:
        findings.append(Finding(
            "crawl.noindex", "crawl", "critical", url,
            "noindexが指定されています。このページはGoogleの検索結果に一切出ません。",
            f"robots: {page.robots_meta or 'noindex'}",
            "検索に出したいページなら、noindexを外してください。",
        ))
    if page.nofollow_page:
        findings.append(Finding(
            "crawl.nofollow", "crawl", "warning", url,
            "ページ全体にnofollowが指定され、リンク先へ評価が渡りません。",
            f"robots: {page.robots_meta}",
            "意図がなければnofollowを外してください。",
        ))
    if page.status >= 400:
        findings.append(Finding(
            "crawl.error_status", "crawl", "critical", url,
            f"HTTP {page.status} を返しています。",
            f"status: {page.status}",
            "リンク元を直すか、正しいページへ301リダイレクトしてください。",
        ))
    if len(page.redirect_chain) >= 2:
        findings.append(Finding(
            "crawl.redirect_chain", "crawl", "warning", url,
            f"リダイレクトが{len(page.redirect_chain)}回連鎖しています。評価が目減りし表示も遅くなります。",
            " → ".join(page.redirect_chain[:4]),
            "最終URLへ1回で飛ぶよう直してください。",
        ))


def check_heading(page, findings: list[Finding]) -> None:
    url = page.url
    if page.h1_count == 0:
        findings.append(Finding(
            "heading.h1.missing", "heading", "warning", url,
            "H1見出しがありません。ページの主題が機械に伝わりません。",
            "h1: 0個",
            "ページの主題を表すH1を1つ置いてください。",
        ))
    elif page.h1_count > 1:
        findings.append(Finding(
            "heading.h1.multiple", "heading", "info", url,
            f"H1が{page.h1_count}個あります。主題が分散します。",
            f"h1: {page.h1_count}個 / {', '.join(page.h1_texts[:3])}",
            "H1は1つにし、以降はH2以下にしてください。",
        ))
    if page.heading_jumps:
        findings.append(Finding(
            "heading.level_jump", "heading", "info", url,
            "見出しの階層が飛んでいます。読み上げと構造解析の妨げになります。",
            " / ".join(page.heading_jumps[:3]),
            "H2の下はH3、というように1段ずつ下げてください。",
        ))
    if page.h1_texts and _norm(page.title) and _norm(page.h1_texts[0]) == _norm(page.title):
        findings.append(Finding(
            "heading.h1_equals_title", "heading", "info", url,
            "H1とtitleが完全に同一です。検索結果と本文で違う語を拾う機会を失っています。",
            f"共通: {_norm(page.title)[:60]}",
            "titleは検索向け、H1は読み手向けに少し表現を変えてください。",
        ))


def check_structured(page, findings: list[Finding]) -> None:
    url = page.url
    if not page.jsonld_types:
        findings.append(Finding(
            "structured.missing", "structured", "warning", url,
            "構造化データ(JSON-LD)がありません。リッチリザルトの対象外です。",
            "application/ld+json: 0件",
            "Organization / WebSite / BreadcrumbList から始めて追加してください。",
        ))
        return
    if page.jsonld_invalid:
        findings.append(Finding(
            "structured.invalid", "structured", "warning", url,
            "JSON-LDに構文エラーがあり、Googleに読まれません。",
            f"壊れているブロック: {page.jsonld_invalid}個",
            "JSONとして妥当か検証してから設置してください。",
        ))
    if "BreadcrumbList" not in page.jsonld_types:
        findings.append(Finding(
            "structured.breadcrumb", "structured", "info", url,
            "パンくずの構造化データがありません。検索結果に階層が出ません。",
            f"検出した型: {', '.join(sorted(page.jsonld_types)) or 'なし'}",
            "BreadcrumbListを追加してください。",
        ))


def check_social(page, findings: list[Finding]) -> None:
    url = page.url
    missing = [key for key in ("og:title", "og:description", "og:image") if key not in page.og]
    if len(missing) == 3:
        findings.append(Finding(
            "social.og.missing", "social", "warning", url,
            "OGPがありません。SNSやチャットに貼っても内容が表示されません。",
            "og:title / og:description / og:image いずれも未設定",
            "og:title・og:description・og:image(1200×630)を設定してください。",
        ))
    elif missing:
        findings.append(Finding(
            "social.og.partial", "social", "info", url,
            "OGPの一部が欠けています。",
            f"不足: {', '.join(missing)}",
            "不足しているOGPを補ってください。",
        ))


def check_content(page, findings: list[Finding]) -> None:
    url = page.url
    if page.text_length < THIN_CONTENT_CHARS:
        findings.append(Finding(
            "content.thin", "content", "warning", url,
            f"本文が{THIN_CONTENT_CHARS}文字未満で、検索意図を満たせません。",
            f"本文: {page.text_length}文字",
            "具体例・数字・手順を足して、読み手の疑問が解ける量にしてください。",
        ))
    if page.images_without_alt:
        findings.append(Finding(
            "content.img_alt", "content", "info", url,
            f"alt属性の無い画像が{page.images_without_alt}個あります。",
            f"alt無し {page.images_without_alt}個 / 画像 {page.images_total}個",
            "内容を説明するaltを入れてください(装飾画像はalt=\"\")。",
        ))
    if page.link_count > MAX_LINKS:
        findings.append(Finding(
            "content.too_many_links", "content", "info", url,
            f"リンクが{page.link_count}本あり、1本あたりの評価が薄まります。",
            f"リンク: {page.link_count}本",
            f"主要な導線に絞り、{MAX_LINKS}本以内を目安にしてください。",
        ))
    if page.internal_link_count == 0:
        findings.append(Finding(
            "content.no_internal_link", "content", "warning", url,
            "内部リンクが1本もありません。孤立ページはクロールも評価も届きません。",
            "内部リンク: 0本",
            "関連ページへの導線を本文中に置いてください。",
        ))


def check_mobile(page, findings: list[Finding]) -> None:
    url = page.url
    if not page.viewport:
        findings.append(Finding(
            "mobile.viewport.missing", "mobile", "critical", url,
            "viewport指定がありません。スマートフォンで拡大縮小が必要な表示になります。",
            "meta[name=viewport]: なし",
            '<meta name="viewport" content="width=device-width, initial-scale=1"> を入れてください。',
        ))
    elif "user-scalable=no" in page.viewport.replace(" ", "") or "maximum-scale=1" in page.viewport.replace(" ", ""):
        findings.append(Finding(
            "mobile.viewport.no_zoom", "mobile", "info", url,
            "拡大を禁止しています。読みづらさとアクセシビリティの問題になります。",
            f"viewport: {page.viewport}",
            "user-scalable=no と maximum-scale の指定を外してください。",
        ))


def check_speed(page, findings: list[Finding]) -> None:
    url = page.url
    if page.elapsed >= VERY_SLOW_RESPONSE_SEC:
        findings.append(Finding(
            "speed.very_slow", "speed", "critical", url,
            f"応答が{page.elapsed:.1f}秒かかっています。離脱の主因になります。",
            f"応答時間: {page.elapsed:.2f}秒 / HTML {page.html_bytes:,}バイト",
            "サーバー応答・画像・キャッシュを順に見直してください。",
        ))
    elif page.elapsed >= SLOW_RESPONSE_SEC:
        findings.append(Finding(
            "speed.slow", "speed", "warning", url,
            f"応答が{page.elapsed:.1f}秒かかっています。",
            f"応答時間: {page.elapsed:.2f}秒 / HTML {page.html_bytes:,}バイト",
            "キャッシュとgzip/brotli圧縮の有無を確認してください。",
        ))
    if page.blocking_scripts > MAX_BLOCKING_SCRIPTS:
        findings.append(Finding(
            "speed.blocking_scripts", "speed", "info", url,
            f"描画を止めるスクリプトが{page.blocking_scripts}本あります。",
            f"head内の同期script: {page.blocking_scripts}本",
            "async / defer を付けるか、body末尾へ移してください。",
        ))


def check_security(page, findings: list[Finding]) -> None:
    url = page.url
    if urlparse(url).scheme != "https":
        findings.append(Finding(
            "security.no_https", "security", "critical", url,
            "HTTPSではありません。ブラウザに警告が出て、検索でも不利になります。",
            f"scheme: {urlparse(url).scheme}",
            "証明書を導入し、HTTPからHTTPSへ301リダイレクトしてください。",
        ))
    elif page.mixed_content:
        findings.append(Finding(
            "security.mixed_content", "security", "warning", url,
            f"HTTPSページ内にhttp://の読み込みが{page.mixed_content}件あります。",
            f"mixed content: {page.mixed_content}件",
            "参照先をhttps://に直してください。",
        ))


def check_japanese(page, findings: list[Finding]) -> None:
    """日本語サイトでだけ効く判定。英語圏のツールが見ない部分。"""
    url = page.url
    body_ja = has_japanese(page.text_sample)
    if body_ja and not page.lang:
        findings.append(Finding(
            "japanese.lang.missing", "japanese", "warning", url,
            "日本語のページなのに html の lang 属性がありません。",
            "html[lang]: なし",
            '<html lang="ja"> を指定してください。',
        ))
    elif body_ja and page.lang and not page.lang.lower().startswith("ja"):
        findings.append(Finding(
            "japanese.lang.mismatch", "japanese", "warning", url,
            f"本文は日本語ですが lang=\"{page.lang}\" と宣言されています。",
            f"html[lang]: {page.lang}",
            'lang="ja" に直してください。誤った言語で配信対象がずれます。',
        ))
    if page.mojibake:
        findings.append(Finding(
            "japanese.mojibake", "japanese", "critical", url,
            "文字化けを検出しました。文字コード宣言と実体が食い違っています。",
            f"検出箇所: {page.mojibake}",
            "meta charset と実際のエンコーディングをUTF-8で揃えてください。",
        ))
    if page.fullwidth_alnum_in_title:
        findings.append(Finding(
            "japanese.fullwidth_alnum", "japanese", "info", url,
            "titleに全角の英数字が含まれています。半角で検索する人と一致しにくくなります。",
            f"該当: {page.fullwidth_alnum_in_title}",
            "英数字は半角に統一してください。",
        ))
    if not page.charset:
        findings.append(Finding(
            "japanese.charset.missing", "japanese", "info", url,
            "meta charset の宣言がありません。環境によって文字化けします。",
            "meta[charset]: なし",
            '<meta charset="utf-8"> を head の先頭付近に置いてください。',
        ))


PAGE_CHECKS = (
    check_crawl,
    check_meta,
    check_heading,
    check_structured,
    check_social,
    check_content,
    check_mobile,
    check_speed,
    check_security,
    check_japanese,
)


def check_page(page) -> list[Finding]:
    findings: list[Finding] = []
    for check in PAGE_CHECKS:
        check(page, findings)
    return findings


# --- サイト単位の判定 -------------------------------------------------------


def check_site(site, pages) -> list[Finding]:
    """複数ページを見て初めて分かること(重複・robots・sitemap)。"""
    findings: list[Finding] = []
    home = site.base_url

    if site.robots_txt is None:
        findings.append(Finding(
            "crawl.robots.missing", "crawl", "info", home,
            "robots.txt がありません。",
            f"{urljoin(home, '/robots.txt')}: 取得できず",
            "robots.txt を置き、sitemapの場所を記載してください。",
        ))
    else:
        if site.robots_blocks_all:
            findings.append(Finding(
                "crawl.robots.disallow_all", "crawl", "critical", home,
                "robots.txt が全クローラーを拒否しています。サイト全体が検索に出ません。",
                "Disallow: /",
                "検索に出したいなら Disallow: / を外してください。",
            ))
        if not site.robots_sitemap:
            findings.append(Finding(
                "crawl.robots.no_sitemap", "crawl", "info", home,
                "robots.txt に Sitemap の記載がありません。",
                "Sitemap行: なし",
                "Sitemap: https://example.com/sitemap.xml を追記してください。",
            ))

    if not site.sitemap_found:
        findings.append(Finding(
            "crawl.sitemap.missing", "crawl", "warning", home,
            "sitemap.xml が見つかりません。新しいページの発見が遅れます。",
            f"{urljoin(home, '/sitemap.xml')}: 取得できず",
            "sitemap.xml を生成し、Search Consoleに送信してください。",
        ))

    # 重複title / 重複description は複数ページを突き合わせて初めて分かる
    for label, attr, rule, category in (
        ("title", "title", "meta.title.duplicate", "meta"),
        ("meta description", "description", "meta.description.duplicate", "meta"),
    ):
        seen: dict[str, list[str]] = {}
        for page in pages:
            value = _norm(getattr(page, attr))
            if value:
                seen.setdefault(value, []).append(page.url)
        for value, urls in seen.items():
            if len(urls) > 1:
                findings.append(Finding(
                    rule, category, "warning", urls[0],
                    f"同じ{label}が{len(urls)}ページで使われています。どれを出すかGoogleが選べません。",
                    f"{value[:50]} — {', '.join(urls[:3])}" + (" ほか" if len(urls) > 3 else ""),
                    f"ページごとに固有の{label}を書いてください。",
                ))

    if site.broken_links:
        sample = ", ".join(f"{u}({s})" for u, s in site.broken_links[:3])
        findings.append(Finding(
            "content.broken_link", "content", "warning", home,
            f"リンク切れが{len(site.broken_links)}件あります。",
            sample,
            "リンク先を修正するか、リンクを外してください。",
        ))

    return findings


# --- 採点 -------------------------------------------------------------------


def _round_half_up(value: float) -> int:
    """0.5は切り上げる。

    組み込みの round() は銀行家丸め(round-half-to-even)で、round(98.5)=98 に
    なる。点数としては直感に反するうえ、PHP版の round() は半数切り上げなので
    同じサイトで1点ずれる(実際に kseo.exbridge.jp で98対99の食い違いが出た)。
    両実装を揃えるため、ここで丸め方を固定する。スコアは常に正の値。
    """
    return int(math.floor(value + 0.5))


def score(findings: list[Finding], page_count: int = 1) -> dict:
    """カテゴリ別と総合のスコア。100点から減点する。

    ページ数で割るのは、50ページ監査したときに件数だけで真っ赤になるのを
    防ぐため。1ページあたり平均どれだけ問題があるかで見る。
    """
    pages = max(1, page_count)
    per_category: dict[str, float] = {key: 0.0 for key in CATEGORY_LABELS}
    counts = {"critical": 0, "warning": 0, "info": 0}
    for finding in findings:
        weight = SEVERITY_WEIGHT.get(finding.severity, 5)
        per_category[finding.category] = per_category.get(finding.category, 0.0) + weight
        if finding.severity in counts:
            counts[finding.severity] += 1

    categories = {}
    for key, penalty in per_category.items():
        value = max(0, min(100, _round_half_up(100 - penalty / pages)))
        categories[key] = {"label": CATEGORY_LABELS.get(key, key), "score": value}

    # 総合はカテゴリの平均にしない。10カテゴリの平均だと、1カテゴリが壊滅
    # していても総合は90点台に見えてしまい、noindexで全ページ検索圏外という
    # 最悪の状態を「おおむね良好」と伝えることになる。減点の総量で出す。
    overall = max(0, min(100, _round_half_up(100 - sum(
        SEVERITY_WEIGHT.get(f.severity, 5) for f in findings
    ) / pages)))
    return {
        "overall": overall,
        "categories": categories,
        "counts": counts,
        "pages": pages,
        "total": len(findings),
    }
