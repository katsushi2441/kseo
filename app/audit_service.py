"""クロールと判定をつないで、保存できる形にする。

ここにはSEOの知識を置かない。閾値と判定は audit_rules に、取得と解析は
crawler にある。分けているのは、判定を直したいときにクロールを触らずに
済ませるため(誤検知の修正が一番よく起きる)。
"""

from __future__ import annotations

import dataclasses

from . import audit_rules, crawler


def page_summary(page: crawler.PageFacts) -> dict:
    """画面と診断書に出すページ単位の実測値。"""
    return {
        "url": page.url,
        "status": page.status,
        "elapsed": round(page.elapsed, 3),
        "title": page.title,
        "title_length": audit_rules.visible_length(page.title),
        "description_length": audit_rules.visible_length(page.description),
        "h1_count": page.h1_count,
        "text_length": page.text_length,
        "link_count": page.link_count,
        "internal_link_count": page.internal_link_count,
        "images_total": page.images_total,
        "images_without_alt": page.images_without_alt,
        "jsonld_types": sorted(page.jsonld_types),
        "noindex": page.noindex,
        "canonical": page.canonical,
        "lang": page.lang,
        "error": page.error,
    }


async def run_audit(url: str, max_pages: int) -> dict:
    """1サイトを監査して、スコア・指摘・ページ実測値を返す。

    CrawlError はそのまま呼び出し元へ投げる。利用者に見せてよい文面で
    作ってあるので、ここで包み直すと原因が消える。
    """
    engine = crawler.Crawler(url, max_pages)
    site, pages = await engine.run()

    ok_pages = [page for page in pages if not page.error]
    findings: list[audit_rules.Finding] = []
    for page in ok_pages:
        findings.extend(audit_rules.check_page(page))
    findings.extend(audit_rules.check_site(site, ok_pages))

    score = audit_rules.score(findings, len(ok_pages) or 1)
    return {
        "score": score,
        "findings": findings,
        "pages": [page_summary(page) for page in pages],
        "site": {
            "base_url": site.base_url,
            "robots_txt": site.robots_txt is not None,
            "robots_blocks_all": site.robots_blocks_all,
            "sitemap_found": site.sitemap_found,
            "sitemap_urls": len(site.sitemap_urls),
            "broken_links": [{"url": u, "status": s} for u, s in site.broken_links],
        },
    }


def findings_as_dicts(findings: list[audit_rules.Finding]) -> list[dict]:
    return [dataclasses.asdict(item) for item in findings]
