"""対象サイトを取得して、判定に必要な事実だけを取り出す。

利用者が任意のURLを投げてくるサービスなので、取得の前に必ず
validate_public_url を通す。これが無いと `http://192.168.0.14:11434` や
`http://169.254.169.254/` を渡されて、社内のOllamaやクラウドのメタデータを
このサーバー経由で読まれる(SSRF)。ワークスペースには 0.3/0.11/0.14 の
内部サービスが多数あるため、ここは緩められない。
"""

from __future__ import annotations

import asyncio
import dataclasses
import ipaddress
import json
import re
import socket
import time
from urllib.parse import urljoin, urlparse, urlunparse
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup

from . import config

# 文字化けの典型パターン。UTF-8をLatin-1として読んだときに出る並び。
MOJIBAKE_RE = re.compile("[ÃÂ][-¿]|�{2,}")
FULLWIDTH_ALNUM_RE = re.compile("[Ａ-Ｚａ-ｚ０-９]+")
BLOCKED_PORTS = {22, 23, 25, 445, 3306, 5432, 6379, 11211, 11434}


class CrawlError(Exception):
    """利用者にそのまま見せてよいエラー。"""


def validate_public_url(raw: str) -> str:
    """公開されたhttp(s) URLだけを通す。内部宛ては名前解決の結果で弾く。

    ホスト名で書かれた内部宛て(例: `router.local`)を防ぐため、文字列では
    なく解決後のIPを見る。解決した全アドレスを検査するのは、1つでも
    プライベートに落ちる名前を通すと迂回されるため。
    """
    value = (raw or "").strip()
    if not value:
        raise CrawlError("URLを入力してください。")
    if "://" not in value:
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https"):
        raise CrawlError("http:// または https:// のURLを指定してください。")
    host = parsed.hostname
    if not host:
        raise CrawlError("URLのホスト名を読み取れませんでした。")
    if parsed.port and parsed.port in BLOCKED_PORTS:
        raise CrawlError("そのポートへの接続は許可していません。")

    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise CrawlError(f"ホスト名を解決できませんでした: {host}") from exc

    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            raise CrawlError(
                f"内部ネットワークのアドレス({address})に解決されるURLは監査できません。"
            )
    return urlunparse(parsed)


def normalize_url(url: str) -> str:
    """フラグメントと末尾スラッシュの揺れを吸収して同一ページを重複させない。"""
    parsed = urlparse(url)
    path = parsed.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse((parsed.scheme, parsed.netloc, path, "", parsed.query, ""))


@dataclasses.dataclass
class PageFacts:
    url: str
    status: int = 0
    elapsed: float = 0.0
    html_bytes: int = 0
    redirect_chain: list[str] = dataclasses.field(default_factory=list)
    title: str = ""
    description: str = ""
    canonical: str = ""
    canonical_conflict: bool = False
    robots_meta: str = ""
    noindex: bool = False
    nofollow_page: bool = False
    lang: str = ""
    charset: str = ""
    viewport: str = ""
    h1_count: int = 0
    h1_texts: list[str] = dataclasses.field(default_factory=list)
    heading_jumps: list[str] = dataclasses.field(default_factory=list)
    jsonld_types: set[str] = dataclasses.field(default_factory=set)
    jsonld_invalid: int = 0
    og: dict[str, str] = dataclasses.field(default_factory=dict)
    text_length: int = 0
    text_sample: str = ""
    images_total: int = 0
    images_without_alt: int = 0
    link_count: int = 0
    internal_link_count: int = 0
    links: list[str] = dataclasses.field(default_factory=list)
    blocking_scripts: int = 0
    mixed_content: int = 0
    mojibake: str = ""
    fullwidth_alnum_in_title: str = ""
    error: str = ""


@dataclasses.dataclass
class SiteFacts:
    base_url: str
    robots_txt: str | None = None
    robots_blocks_all: bool = False
    robots_sitemap: str = ""
    sitemap_found: bool = False
    sitemap_urls: list[str] = dataclasses.field(default_factory=list)
    broken_links: list[tuple[str, int]] = dataclasses.field(default_factory=list)


def _canonical_href(soup) -> str:
    for tag in soup.find_all("link"):
        rel = tag.get("rel") or []
        values = rel if isinstance(rel, list) else [rel]
        if any(str(value).lower() == "canonical" for value in values) and tag.get("href"):
            return str(tag["href"]).strip()
    return ""


def parse_page(
    url: str, html: str, status: int, elapsed: float, redirect_chain: list[str]
) -> PageFacts:
    page = PageFacts(
        url=url,
        status=status,
        elapsed=elapsed,
        html_bytes=len(html.encode("utf-8", "ignore")),
        redirect_chain=redirect_chain,
    )
    soup = BeautifulSoup(html, "html.parser")

    if soup.title and soup.title.string:
        page.title = soup.title.string.strip()
    matched = FULLWIDTH_ALNUM_RE.findall(page.title)
    page.fullwidth_alnum_in_title = ", ".join(matched[:3])

    for meta in soup.find_all("meta"):
        name = (meta.get("name") or "").lower()
        prop = (meta.get("property") or "").lower()
        equiv = (meta.get("http-equiv") or "").lower()
        content = (meta.get("content") or "").strip()
        if name == "description":
            page.description = content
        elif name == "robots":
            page.robots_meta = content
        elif name == "viewport":
            page.viewport = content
        elif prop.startswith("og:"):
            page.og[prop] = content
        if meta.get("charset"):
            page.charset = str(meta.get("charset")).strip()
        elif (equiv == "content-type" or name == "content-type") and "charset=" in content.lower():
            page.charset = content.lower().split("charset=", 1)[1].strip().strip('"\'')

    robots_value = page.robots_meta.lower()
    page.noindex = "noindex" in robots_value
    page.nofollow_page = "nofollow" in robots_value

    href = _canonical_href(soup)
    if href:
        page.canonical = urljoin(url, href)
        page.canonical_conflict = normalize_url(page.canonical) != normalize_url(url)

    html_tag = soup.find("html")
    if html_tag and html_tag.get("lang"):
        page.lang = str(html_tag["lang"]).strip()

    levels: list[int] = []
    for tag in soup.find_all(re.compile("^h[1-6]$")):
        level = int(tag.name[1])
        levels.append(level)
        if level == 1:
            page.h1_count += 1
            text = tag.get_text(" ", strip=True)
            if text:
                page.h1_texts.append(text)
    for previous, current in zip(levels, levels[1:]):
        if current - previous >= 2:
            page.heading_jumps.append(f"H{previous} -> H{current}")

    for script in soup.find_all("script", type=lambda v: v and "ld+json" in v.lower()):
        raw = script.string or script.get_text() or ""
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            page.jsonld_invalid += 1
            continue
        for entry in data if isinstance(data, list) else [data]:
            if not isinstance(entry, dict):
                continue
            value = entry.get("@type")
            for item in value if isinstance(value, list) else [value]:
                if item:
                    page.jsonld_types.add(str(item))
            for nested in entry.get("@graph") or []:
                if isinstance(nested, dict) and nested.get("@type"):
                    page.jsonld_types.add(str(nested["@type"]))

    for tag in soup.find_all(["script", "style", "noscript", "template"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    page.text_length = len(re.sub(r"\s+", "", text))
    page.text_sample = text[:4000]
    found = MOJIBAKE_RE.search(page.text_sample)
    if found:
        page.mojibake = found.group(0)[:20]

    images = soup.find_all("img")
    page.images_total = len(images)
    page.images_without_alt = sum(1 for img in images if img.get("alt") is None)

    host = urlparse(url).hostname or ""
    for anchor in soup.find_all("a", href=True):
        value = str(anchor["href"]).strip()
        if value.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urljoin(url, value)
        if urlparse(absolute).scheme not in ("http", "https"):
            continue
        page.link_count += 1
        if (urlparse(absolute).hostname or "") == host:
            page.internal_link_count += 1
            page.links.append(normalize_url(absolute))

    head = soup.find("head")
    if head:
        page.blocking_scripts = sum(
            1
            for script in head.find_all("script", src=True)
            if not script.has_attr("async") and not script.has_attr("defer")
        )
    if urlparse(url).scheme == "https":
        page.mixed_content = len(re.findall(r'(?:src|href)="http://', html))

    return page


class Crawler:
    def __init__(self, base_url: str, max_pages: int):
        self.base_url = validate_public_url(base_url)
        self.max_pages = max(1, max_pages)
        self.host = urlparse(self.base_url).hostname or ""
        self.site = SiteFacts(base_url=self.base_url)

    async def _get(
        self, client: httpx.AsyncClient, url: str
    ) -> tuple[httpx.Response | None, float, str]:
        started = time.monotonic()
        try:
            response = await client.get(url)
        except httpx.HTTPError as exc:
            return None, time.monotonic() - started, str(exc)
        return response, time.monotonic() - started, ""

    async def fetch_robots(self, client: httpx.AsyncClient) -> None:
        response, _, _ = await self._get(client, urljoin(self.base_url, "/robots.txt"))
        if not response or response.status_code >= 400:
            return
        body = response.text
        self.site.robots_txt = body
        agent_all = False
        for raw_line in body.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "user-agent":
                agent_all = value == "*"
            elif key == "disallow" and agent_all and value == "/":
                self.site.robots_blocks_all = True
            elif key == "sitemap" and not self.site.robots_sitemap:
                self.site.robots_sitemap = value

    async def fetch_sitemap(self, client: httpx.AsyncClient) -> None:
        candidates = [self.site.robots_sitemap] if self.site.robots_sitemap else []
        candidates.append(urljoin(self.base_url, "/sitemap.xml"))
        for candidate in candidates:
            if not candidate:
                continue
            response, _, _ = await self._get(client, candidate)
            if not response or response.status_code >= 400:
                continue
            try:
                root = ElementTree.fromstring(response.content)
            except ElementTree.ParseError:
                continue
            self.site.sitemap_found = True
            for element in root.iter():
                if (element.tag.endswith("}loc") or element.tag == "loc") and element.text:
                    self.site.sitemap_urls.append(normalize_url(element.text.strip()))
            if self.site.sitemap_urls:
                return

    async def check_broken_links(self, client: httpx.AsyncClient, urls: list[str]) -> None:
        """内部リンクの死活を見る。全部叩くと相手に迷惑なので上限を切る。"""
        for url in urls[:20]:
            await asyncio.sleep(config.CRAWL_DELAY)
            try:
                response = await client.head(url)
                if response.status_code == 405:
                    response = await client.get(url)
            except httpx.HTTPError:
                self.site.broken_links.append((url, 0))
                continue
            if response.status_code >= 400:
                self.site.broken_links.append((url, response.status_code))

    async def run(self) -> tuple[SiteFacts, list[PageFacts]]:
        headers = {"User-Agent": config.USER_AGENT, "Accept-Language": "ja,en;q=0.8"}
        pages: list[PageFacts] = []
        seen: set[str] = set()
        async with httpx.AsyncClient(
            headers=headers,
            timeout=config.CRAWL_TIMEOUT,
            follow_redirects=True,
            max_redirects=5,
        ) as client:
            await self.fetch_robots(client)
            await self.fetch_sitemap(client)

            queue = [normalize_url(self.base_url)]
            # sitemapがあれば同一ホスト分を優先的に候補へ入れる。
            for url in self.site.sitemap_urls:
                if (urlparse(url).hostname or "") == self.host and url not in queue:
                    queue.append(url)

            while queue and len(pages) < self.max_pages:
                url = queue.pop(0)
                if url in seen:
                    continue
                seen.add(url)
                if pages:
                    await asyncio.sleep(config.CRAWL_DELAY)
                response, elapsed, error = await self._get(client, url)
                if response is None:
                    pages.append(PageFacts(url=url, error=error or "取得できませんでした"))
                    continue
                content_type = response.headers.get("content-type", "")
                if "html" not in content_type.lower():
                    continue
                body = response.content[: config.MAX_HTML_BYTES].decode(
                    response.encoding or "utf-8", "replace"
                )
                chain = [str(item.url) for item in response.history] + [str(response.url)]
                page = parse_page(url, body, response.status_code, elapsed, chain)
                pages.append(page)
                if not self.site.sitemap_urls:
                    for link in page.links:
                        if link not in seen and link not in queue:
                            queue.append(link)

            all_internal: list[str] = []
            for page in pages:
                for link in page.links:
                    if link not in all_internal:
                        all_internal.append(link)
            await self.check_broken_links(client, [u for u in all_internal if u not in seen])

        if not pages or all(page.error for page in pages):
            raise CrawlError(
                "対象サイトのページを1件も取得できませんでした。URLを確認してください。"
            )
        return self.site, pages
