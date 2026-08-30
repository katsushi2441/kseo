"""判定の回帰テスト。

誤検知は製品として致命的なので、実際に踏んだ誤検知は必ずここに
1件ずつ残す。exbridge.jp の charset 誤検知(http-equiv形式を見落として
「charset宣言なし」と出した)がその第1号。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import audit_rules  # noqa: E402
from app.crawler import parse_page, normalize_url, validate_public_url, CrawlError  # noqa: E402

import pytest  # noqa: E402


def build(html: str, url: str = "https://example.com/page", status: int = 200, elapsed: float = 0.2):
    return parse_page(url, html, status, elapsed, [url])


HEALTHY = """
<!doctype html><html lang="ja"><head>
<meta charset="utf-8">
<title>名古屋のAI開発会社｜受託開発とAI導入支援</title>
<meta name="description" content="名古屋でAIシステムの受託開発と導入支援を行っています。要件整理から運用まで一貫して対応し、業務に合わせて設計します。実績と料金の目安を掲載しています。">
<link rel="canonical" href="https://example.com/page">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta property="og:title" content="名古屋のAI開発会社">
<meta property="og:description" content="AI受託開発">
<meta property="og:image" content="https://example.com/og.png">
<script type="application/ld+json">{"@type":"Organization","name":"X"}</script>
<script type="application/ld+json">{"@type":"BreadcrumbList"}</script>
</head><body>
<h1>名古屋のAI開発</h1><h2>できること</h2>
<p>%s</p>
<a href="https://example.com/other">関連ページ</a>
<img src="a.png" alt="図">
</body></html>
""" % ("これは十分な長さの本文です。" * 40)


def test_healthy_page_has_no_findings():
    """正しく作られたページで何も指摘しないこと。誤検知の総合チェック。"""
    findings = audit_rules.check_page(build(HEALTHY))
    assert findings == [], [f.rule for f in findings]


def test_charset_via_http_equiv_is_not_a_finding():
    """http-equiv形式のcharset宣言を見落とさない(exbridge.jpで出した誤検知)。"""
    html = HEALTHY.replace(
        '<meta charset="utf-8">',
        '<meta http-equiv="Content-Type" content="text/html; charset=utf-8" />',
    )
    page = build(html)
    assert page.charset == "utf-8"
    rules = {f.rule for f in audit_rules.check_page(page)}
    assert "japanese.charset.missing" not in rules


def test_missing_charset_is_reported():
    html = HEALTHY.replace('<meta charset="utf-8">', "")
    rules = {f.rule for f in audit_rules.check_page(build(html))}
    assert "japanese.charset.missing" in rules


def test_visible_length_counts_fullwidth_as_one():
    """日本語は全角1文字、英数字は0.5文字。英語基準の60文字で測らない。"""
    assert audit_rules.visible_length("あいうえお") == 5
    assert audit_rules.visible_length("abcdefghij") == 5
    # 半角A+I=1.0、全角の活+用=2.0 で合計3.0
    assert audit_rules.visible_length("AI活用") == 3


def test_long_japanese_title_is_flagged():
    html = HEALTHY.replace(
        "<title>名古屋のAI開発会社｜受託開発とAI導入支援</title>",
        "<title>" + "あ" * 40 + "</title>",
    )
    findings = audit_rules.check_page(build(html))
    hit = [f for f in findings if f.rule == "meta.title.too_long"]
    assert hit and "40文字" in hit[0].evidence


def test_noindex_is_critical():
    html = HEALTHY.replace("<head>", '<head><meta name="robots" content="noindex">')
    findings = audit_rules.check_page(build(html))
    hit = [f for f in findings if f.rule == "crawl.noindex"]
    assert hit and hit[0].severity == "critical"


def test_canonical_pointing_elsewhere_is_flagged():
    html = HEALTHY.replace(
        'href="https://example.com/page"', 'href="https://example.com/other"'
    )
    rules = {f.rule for f in audit_rules.check_page(build(html))}
    assert "meta.canonical.conflict" in rules


def test_canonical_trailing_slash_is_not_a_conflict():
    """末尾スラッシュ差だけで「別ページを指している」と言わない。"""
    page = build(
        HEALTHY.replace('href="https://example.com/page"', 'href="https://example.com/page/"')
    )
    assert page.canonical_conflict is False


def test_broken_jsonld_is_counted():
    html = HEALTHY.replace('{"@type":"Organization","name":"X"}', '{"@type":,,}')
    page = build(html)
    assert page.jsonld_invalid == 1
    rules = {f.rule for f in audit_rules.check_page(page)}
    assert "structured.invalid" in rules


def test_lang_mismatch_for_japanese_body():
    html = HEALTHY.replace('<html lang="ja">', '<html lang="en">')
    rules = {f.rule for f in audit_rules.check_page(build(html))}
    assert "japanese.lang.mismatch" in rules


def test_english_page_does_not_get_japanese_findings():
    """英語ページに日本語向けの指摘を出さない。"""
    html = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        "<title>An English page about our service</title>"
        '<meta name="description" content="' + ("word " * 30) + '">'
        '<link rel="canonical" href="https://example.com/page">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "</head><body><h1>Hello</h1><p>" + ("content " * 200) + "</p>"
        '<a href="https://example.com/x">next</a></body></html>'
    )
    rules = {f.rule for f in audit_rules.check_page(build(html))}
    assert "japanese.lang.missing" not in rules
    assert "japanese.lang.mismatch" not in rules


def test_thin_content_is_flagged():
    html = HEALTHY.replace("これは十分な長さの本文です。" * 40, "短い。")
    rules = {f.rule for f in audit_rules.check_page(build(html))}
    assert "content.thin" in rules


def test_no_internal_link_is_flagged():
    html = HEALTHY.replace('<a href="https://example.com/other">関連ページ</a>', "")
    rules = {f.rule for f in audit_rules.check_page(build(html))}
    assert "content.no_internal_link" in rules


def test_score_drops_hard_on_critical():
    """noindexのような致命傷で90点台にならないこと。"""
    html = HEALTHY.replace("<head>", '<head><meta name="robots" content="noindex">')
    findings = audit_rules.check_page(build(html))
    result = audit_rules.score(findings, 1)
    assert result["overall"] <= 75, result["overall"]
    assert result["counts"]["critical"] == 1


def test_score_of_healthy_page_is_full():
    result = audit_rules.score(audit_rules.check_page(build(HEALTHY)), 1)
    assert result["overall"] == 100


def test_duplicate_titles_are_detected_across_pages():
    from app.crawler import SiteFacts

    site = SiteFacts(base_url="https://example.com/", robots_txt="User-agent: *\n", sitemap_found=True)
    site.robots_sitemap = "https://example.com/sitemap.xml"
    a = build(HEALTHY, url="https://example.com/a")
    b = build(HEALTHY, url="https://example.com/b")
    rules = {f.rule for f in audit_rules.check_site(site, [a, b])}
    assert "meta.title.duplicate" in rules
    assert "meta.description.duplicate" in rules


def test_robots_disallow_all_is_critical():
    from app.crawler import SiteFacts

    site = SiteFacts(
        base_url="https://example.com/",
        robots_txt="User-agent: *\nDisallow: /\n",
        robots_blocks_all=True,
        sitemap_found=True,
        robots_sitemap="https://example.com/sitemap.xml",
    )
    hit = [f for f in audit_rules.check_site(site, []) if f.rule == "crawl.robots.disallow_all"]
    assert hit and hit[0].severity == "critical"


# --- SSRF ------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/",
        "http://localhost/",
        "http://192.168.0.14:11434/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/",
        "file:///etc/passwd",
        "ftp://example.com/",
    ],
)
def test_internal_and_non_http_urls_are_rejected(url):
    """内部宛て・非HTTPを弾く。ここが緩むと社内サービスを読まれる。"""
    with pytest.raises(CrawlError):
        validate_public_url(url)


def test_public_url_is_accepted():
    assert validate_public_url("example.com").startswith("https://example.com")


def test_normalize_url_removes_fragment_and_trailing_slash():
    assert normalize_url("https://a.jp/x/#top") == "https://a.jp/x"
    assert normalize_url("https://a.jp/") == "https://a.jp/"


# --- 自社LPのドッグフーディングで見つけた誤検知（2026-08-31） --------------


def test_english_page_with_japanese_language_link_is_not_japanese():
    """英語ページの言語切替リンク「日本語」だけで日本語ページ扱いしない。

    kseo.exbridge.jp/ (英語LP) に lang不一致 を出した誤検知の再現。
    """
    html = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        "<title>Kurage SEO for Japanese pages</title>"
        '<meta name="description" content="' + ("word " * 30) + '">'
        '<link rel="canonical" href="https://example.com/page">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta property="og:title" content="t"><meta property="og:description" content="d">'
        '<meta property="og:image" content="https://example.com/o.png">'
        '<script type="application/ld+json">{"@type":"BreadcrumbList"}</script>'
        "</head><body>"
        '<a href="page.html">日本語</a>'  # ← これだけで日本語ページにしない
        "<h1>Hello</h1><p>" + ("content " * 200) + "</p>"
        '<a href="https://example.com/x">next</a></body></html>'
    )
    page = build(html)
    rules = {f.rule for f in audit_rules.check_page(page)}
    assert "japanese.lang.mismatch" not in rules
    assert "japanese.lang.missing" not in rules


def test_real_japanese_page_is_still_detected():
    """割合判定にしても、本物の日本語ページは取りこぼさない。"""
    html = HEALTHY.replace('<html lang="ja">', '<html lang="en">')
    rules = {f.rule for f in audit_rules.check_page(build(html))}
    assert "japanese.lang.mismatch" in rules


def test_canonical_from_index_html_to_directory_is_not_a_conflict():
    """/index.html から / への canonical は正しい作法。別ページ扱いしない。"""
    page = build(
        HEALTHY.replace('href="https://example.com/page"', 'href="https://example.com/"'),
        url="https://example.com/index.html",
    )
    assert page.canonical_conflict is False
    rules = {f.rule for f in audit_rules.check_page(page)}
    assert "meta.canonical.conflict" not in rules


def test_normalize_url_treats_directory_index_as_root():
    assert normalize_url("https://a.jp/index.html") == normalize_url("https://a.jp/")
    assert normalize_url("https://a.jp/sub/index.php") == normalize_url("https://a.jp/sub/")


def test_score_rounds_half_up_to_match_php():
    """0.5は切り上げる。組み込みround()の銀行家丸めだとPHP版と1点ずれる。

    kseo.exbridge.jp で meta=98(Python) 対 99(PHP) の食い違いが実際に出た。
    2ページでinfo1件 → 減点3/2=1.5 → 100-1.5=98.5 が境界。
    """
    assert audit_rules._round_half_up(98.5) == 99
    assert audit_rules._round_half_up(97.5) == 98
    assert audit_rules._round_half_up(98.4) == 98

    page = build(HEALTHY)
    findings = [
        audit_rules.Finding(
            "meta.description.too_long", "meta", "info", page.url, "m", "e", "a"
        )
    ]
    result = audit_rules.score(findings, 2)
    assert result["categories"]["meta"]["score"] == 99
