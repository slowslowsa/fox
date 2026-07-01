"""
UEFN Documentation Crawler

Fetch strategy (in order):
  1. Wayback Machine (regular mode) — properly decoded UTF-8 HTML, no Epic blocking
  2. Live site via requests          — fast, uses brotli for SSR content
  3. Playwright                      — full JS rendering, last resort
"""
import asyncio
import functools
import re
from urllib.parse import urlparse, urlunparse, urljoin

import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, BrowserContext, Page
from tenacity import retry, stop_after_attempt, wait_exponential

from scraper.config import (
    CA_BUNDLE,
    DOCS_ROOTS_ALLOWED,
    MAX_CONCURRENT_PAGES,
    MAX_RETRIES,
    NAVIGATION_WAIT,
    PAGE_LOAD_TIMEOUT_MS,
    PROXY_CONFIG,
    REQUEST_DELAY_SECONDS,
    RETRY_BACKOFF_MULTIPLIER,
    WAYBACK_DELAY_SECONDS,
    WAYBACK_FETCH_BASE,
    WAYBACK_TIMESTAMP,
)
from scraper.extractor import extract_content, has_real_content
from scraper.writer import write_page

ERROR_URL_PATTERNS = ["/403", "/404", "/error", "/access-denied", "/login"]

BLOCKED_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".svg", ".pdf", ".zip", ".gif", ".webp",
    ".woff", ".woff2", ".js", ".css", ".ico", ".ttf", ".eot", ".otf",
    ".mp4", ".mp3", ".avi", ".mov", ".json", ".xml",
)

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

_LIVE_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}

_WAYBACK_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}

_PLAYWRIGHT_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept-Language": "en-US,en;q=0.9",
}


class UEFNCrawler:
    def __init__(self, limit: int = 0, url_timestamps: dict[str, str] | None = None, live_first: bool = False):
        self.visited: set[str] = set()
        self.failed: dict[str, int] = {}
        self.queue: asyncio.Queue = asyncio.Queue()
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_PAGES)
        self._limit = limit
        self._written = 0
        self._url_timestamps = url_timestamps or {}
        self._live_first = live_first

        self._live_session = requests.Session()
        self._live_session.headers.update(_LIVE_HEADERS)

        self._wayback_session = requests.Session()
        self._wayback_session.headers.update(_WAYBACK_HEADERS)

    async def run(self, seed_urls: list[str]):
        for url in seed_urls:
            if url not in self.visited:
                await self.queue.put(url)

        async with async_playwright() as pw:
            launch_kwargs = dict(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            if PROXY_CONFIG:
                launch_kwargs["proxy"] = PROXY_CONFIG

            browser = await pw.chromium.launch(**launch_kwargs)
            workers = [
                asyncio.create_task(self._worker(browser))
                for _ in range(MAX_CONCURRENT_PAGES)
            ]
            await self.queue.join()
            for w in workers:
                w.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            await browser.close()

        self._live_session.close()
        self._wayback_session.close()

    async def _worker(self, browser):
        context = await browser.new_context(extra_http_headers=_PLAYWRIGHT_HEADERS)
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        try:
            while True:
                url = await self.queue.get()
                try:
                    async with self.semaphore:
                        await self._process_url(context, url)
                except Exception as e:
                    print(f"[ERROR] {url}: {e}")
                    self.failed[url] = self.failed.get(url, 0) + 1
                finally:
                    self.queue.task_done()
        except asyncio.CancelledError:
            pass
        finally:
            await context.close()

    async def _process_url(self, context: BrowserContext, url: str):
        if url in self.visited:
            return
        self.visited.add(url)  # mark before limit check to prevent re-queuing
        if self._limit and self._written >= self._limit:
            return

        if self._live_first:
            await self._process_live_first(context, url)
        else:
            await self._process_wayback_first(context, url)

    async def _process_wayback_first(self, context: BrowserContext, url: str):
        # ── Strategy 1: Wayback Machine id_ (archived HTML, no JS needed) ──
        ts = self._url_timestamps.get(url, WAYBACK_TIMESTAMP)
        html = await self._fetch_wayback(url, ts)
        if html:
            content = extract_content(url, html)
            if has_real_content(content):
                links = self._extract_links_from_html(html, url)
                self._save(url, content, links)
                await asyncio.sleep(WAYBACK_DELAY_SECONDS)
                return
            print(f"[WAYBACK] {url} -> empty content (title={content['title']!r:.40}, body={len(content['body_markdown'])}chars), trying live site")

        # ── Strategy 2: Live site via requests (with Brotli support) ──
        html, links = await self._fetch_live_requests(url)
        if html:
            content = extract_content(url, html)
            if has_real_content(content):
                self._save(url, content, links)
                await asyncio.sleep(REQUEST_DELAY_SECONDS)
                return
            print(f"[REQUESTS] {url} -> empty content (CSR?), trying Playwright")

        # ── Strategy 3: Playwright (full JS rendering + all links) ──
        page = await context.new_page()
        try:
            result = await self._fetch_playwright(page, url)
            if result:
                html, links = result
                content = extract_content(url, html)
                if has_real_content(content):
                    self._save(url, content, links)
                else:
                    print(f"[PLAYWRIGHT] {url} -> still empty after JS render, skipping")
        finally:
            await page.close()

        await asyncio.sleep(REQUEST_DELAY_SECONDS)

    async def _process_live_first(self, context: BrowserContext, url: str):
        # ── Strategy 1: Playwright (live site, full JS, all clickable links) ──
        page = await context.new_page()
        try:
            result = await self._fetch_playwright(page, url)
            if result:
                html, links = result
                content = extract_content(url, html)
                if has_real_content(content):
                    self._save(url, content, links)
                    await asyncio.sleep(REQUEST_DELAY_SECONDS)
                    return
                print(f"[PLAYWRIGHT] {url} -> empty content, trying Wayback")
        finally:
            await page.close()

        # ── Strategy 2: Wayback Machine id_ (fallback for blocked/missing pages) ──
        ts = self._url_timestamps.get(url, WAYBACK_TIMESTAMP)
        html = await self._fetch_wayback(url, ts)
        if html:
            content = extract_content(url, html)
            if has_real_content(content):
                links = self._extract_links_from_html(html, url)
                self._save(url, content, links)
                await asyncio.sleep(WAYBACK_DELAY_SECONDS)
                return
            print(f"[WAYBACK] {url} -> empty content, skipping")

        await asyncio.sleep(REQUEST_DELAY_SECONDS)

    def _save(self, url: str, content: dict, links: list[str]):
        write_page(url, content)
        self._written += 1
        print(f"[OK] #{self._written} {url} | title={content['title']!r} | {len(links)} links")
        for link in links:
            if link not in self.visited:
                self.queue.put_nowait(link)

    # ────────────────────────────────────────────────
    # Fetch methods
    # ────────────────────────────────────────────────

    async def _fetch_wayback(self, url: str, timestamp: str) -> str | None:
        wayback_url = WAYBACK_FETCH_BASE.format(timestamp=timestamp, url=url)
        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                functools.partial(
                    self._wayback_session.get,
                    wayback_url,
                    timeout=20,
                    verify=CA_BUNDLE,
                    allow_redirects=True,
                ),
            )
            if resp.status_code == 404:
                print(f"[WAYBACK] {url} -> not in archive")
                return None
            if resp.status_code != 200:
                print(f"[WAYBACK] {url} -> HTTP {resp.status_code}")
                return None
            enc = resp.headers.get("Content-Encoding", "none")
            html = resp.text
            if len(html) < 500:
                return None
            if not html.lstrip().startswith(("<", "!")):
                print(f"[WAYBACK] {url} -> not HTML (encoding={enc}, binary?)")
                return None
            print(f"[WAYBACK] {url} -> {len(html)} chars (encoding={enc})")
            return html
        except Exception as e:
            print(f"[WAYBACK] {url} failed: {e}")
            return None

    async def _fetch_live_requests(self, url: str) -> tuple[str | None, list[str]]:
        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                functools.partial(
                    self._live_session.get,
                    url,
                    timeout=15,
                    verify=CA_BUNDLE,
                    allow_redirects=True,
                ),
            )
            final_url = resp.url
            if any(p in final_url for p in ERROR_URL_PATTERNS):
                print(f"[REQUESTS] {url} -> error page ({final_url})")
                return None, []
            if resp.status_code >= 400:
                print(f"[REQUESTS] {url} -> HTTP {resp.status_code}")
                return None, []
            if resp.status_code != 200:
                return None, []

            html = resp.text
            if len(html) < 500:
                return None, []
            if not html.lstrip().startswith(("<", "!")):
                return None, []

            print(f"[REQUESTS] {url} -> {len(html)} bytes")
            links = self._extract_links_from_html(html, url)
            return html, links
        except Exception as e:
            print(f"[REQUESTS] {url} failed: {e}")
            return None, []

    @retry(
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=RETRY_BACKOFF_MULTIPLIER, min=2, max=20),
        reraise=True,
    )
    async def _fetch_playwright(self, page: Page, url: str) -> tuple[str, list[str]] | None:
        try:
            await page.goto(url, wait_until=NAVIGATION_WAIT, timeout=PAGE_LOAD_TIMEOUT_MS)
        except Exception as e:
            print(f"[PLAYWRIGHT] goto {url}: {e}")
            raise

        final_url = page.url
        if any(p in final_url for p in ERROR_URL_PATTERNS):
            print(f"[PLAYWRIGHT] {url} -> blocked ({final_url})")
            return None

        if final_url != url:
            print(f"[PLAYWRIGHT] redirect: {url} -> {final_url}")

        # Wait for content to render
        try:
            await page.wait_for_selector("h1, article, main", timeout=10_000)
        except Exception:
            pass
        await page.wait_for_timeout(3000)

        title = await page.title()
        print(f"[PLAYWRIGHT] {final_url} title={title!r}")

        html = await page.content()
        links = await self._extract_playwright_links(page, final_url)
        return html, links

    # ────────────────────────────────────────────────
    # Link extraction
    # ────────────────────────────────────────────────

    def _extract_links_from_html(self, html: str, base_url: str) -> list[str]:
        soup = BeautifulSoup(html, "lxml")
        seen: set[str] = set()
        result = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            full = urljoin(base_url, href)
            # Strip Wayback URL wrapper if present (regular Wayback mode modifies links)
            full = self._unwayback_url(full)
            norm = self._normalize_url(full)
            if norm and self._is_uefn_doc_url(norm) and norm not in seen:
                seen.add(norm)
                result.append(norm)
        return result

    async def _extract_playwright_links(self, page: Page, base_url: str) -> list[str]:
        try:
            hrefs = await page.eval_on_selector_all(
                "a[href]", "els => els.map(e => e.href)"
            )
        except Exception as e:
            print(f"[PLAYWRIGHT] link eval failed: {e}")
            return []

        seen: set[str] = set()
        result = []
        for h in hrefs:
            if not isinstance(h, str):
                continue
            norm = self._normalize_url(h)
            if norm and self._is_uefn_doc_url(norm) and norm not in seen:
                seen.add(norm)
                result.append(norm)
        print(f"[PLAYWRIGHT] {len(result)} UEFN links found on {base_url}")
        return result

    # ────────────────────────────────────────────────
    # Helpers
    # ────────────────────────────────────────────────

    def _normalize_url(self, url: str) -> str:
        try:
            p = urlparse(url)
            # Strip query params and fragments, normalize trailing slash
            return urlunparse((p.scheme, p.netloc, p.path.rstrip("/"), "", "", ""))
        except Exception:
            return ""

    def _unwayback_url(self, url: str) -> str:
        """Extract original URL from a Wayback-wrapped link."""
        m = re.match(r'https?://web\.archive\.org/web/\d+[^/]*/(https?://.*)', url)
        return m.group(1) if m else url

    def _is_uefn_doc_url(self, url: str) -> bool:
        if not any(root in url for root in DOCS_ROOTS_ALLOWED):
            return False
        if url.endswith(BLOCKED_EXTENSIONS):
            return False
        blocked_parts = [
            "/_search", "/login", "javascript:", "mailto:",
            "/__", "/cdn-cgi/", "?", "#",
        ]
        return not any(b in url for b in blocked_parts)
