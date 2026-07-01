import asyncio
import functools
from urllib.parse import urlparse, urlunparse, urljoin

import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, BrowserContext, Page
from tenacity import retry, stop_after_attempt, wait_exponential

from scraper.config import (
    CA_BUNDLE,
    DOCS_ROOT,
    MAX_CONCURRENT_PAGES,
    MAX_RETRIES,
    NAVIGATION_WAIT,
    PAGE_LOAD_TIMEOUT_MS,
    PROXY_CONFIG,
    REQUEST_DELAY_SECONDS,
    RETRY_BACKOFF_MULTIPLIER,
)
from scraper.extractor import extract_content
from scraper.writer import write_page

NAV_SELECTORS = [
    "nav a[href]",
    "[class*='sidebar'] a[href]",
    "[class*='Sidebar'] a[href]",
    "[class*='navigation'] a[href]",
    "[class*='Navigation'] a[href]",
    "a[href*='/documentation/en-us/uefn/']",
]

ERROR_URL_PATTERNS = ["/403", "/404", "/error", "/access-denied"]

_BROWSER_HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


class UEFNCrawler:
    def __init__(self, limit: int = 0):
        self.visited: set[str] = set()
        self.failed: dict[str, int] = {}
        self.queue: asyncio.Queue = asyncio.Queue()
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_PAGES)
        self._limit = limit  # 0 = no limit
        self._written = 0
        self._session = requests.Session()
        self._session.headers.update({
            **_BROWSER_HEADERS,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        })

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

        self._session.close()

    async def _worker(self, browser):
        context = await browser.new_context(extra_http_headers=_BROWSER_HEADERS)
        # Mask headless automation fingerprint
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        try:
            while True:
                url = await self.queue.get()
                try:
                    async with self.semaphore:
                        await self._process_page(context, url)
                except Exception as e:
                    print(f"[ERROR] {url}: {e}")
                    self.failed[url] = self.failed.get(url, 0) + 1
                finally:
                    self.queue.task_done()
        except asyncio.CancelledError:
            pass
        finally:
            await context.close()

    async def _process_page(self, context: BrowserContext, url: str):
        if url in self.visited:
            return
        if self._limit and self._written >= self._limit:
            return
        self.visited.add(url)

        # Try requests first — avoids headless browser detection
        html, links = await self._try_requests_fetch(url)
        if html:
            content = extract_content(url, html)
            # If extraction got real content, write it
            if content["title"] and len(content["body_markdown"]) > 100:
                write_page(url, content)
                self._written += 1
                print(f"[PROGRESS] {self._written} pages written")
                print(f"[LINKS] {len(links)} new links on {url}")
                for link in links:
                    if link not in self.visited:
                        await self.queue.put(link)
                await asyncio.sleep(REQUEST_DELAY_SECONDS)
                return
            print(f"[REQUESTS] {url} -> extracted empty content (CSR?), falling back to Playwright")

        # Fall back to Playwright
        page = await context.new_page()
        try:
            result = await self._fetch_page(page, url)
            if result is None:
                return
            html, discovered_urls = result

            if html:
                content = extract_content(url, html)
                write_page(url, content)
                self._written += 1
                print(f"[PROGRESS] {self._written} pages written")

            print(f"[LINKS] {len(discovered_urls)} new links on {url}")
            for link in discovered_urls:
                if link not in self.visited:
                    await self.queue.put(link)
        finally:
            await page.close()

        await asyncio.sleep(REQUEST_DELAY_SECONDS)

    async def _try_requests_fetch(self, url: str):
        """Fetch page with requests (faster, no headless fingerprint)."""
        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                functools.partial(
                    self._session.get,
                    url,
                    timeout=10,
                    verify=CA_BUNDLE,
                    allow_redirects=True,
                ),
            )
            final_url = resp.url
            if any(p in final_url for p in ERROR_URL_PATTERNS):
                print(f"[SKIP] {url} -> error page ({final_url})")
                return None, []
            # Hard fail on 4xx/5xx — don't waste time on Playwright for dead URLs
            if resp.status_code >= 400:
                print(f"[SKIP] {url} -> HTTP {resp.status_code}")
                return None, []
            if resp.status_code != 200:
                print(f"[REQUESTS] {url} -> HTTP {resp.status_code}, trying Playwright")
                return None, []

            html = resp.text
            if len(html) < 500:
                print(f"[REQUESTS] {url} -> only {len(html)} bytes (empty), skipping")
                return None, []

            # Detect binary/compressed garbage (not valid HTML)
            stripped = html.lstrip()
            if not stripped.startswith(("<", "!")):
                print(f"[REQUESTS] {url} -> response is not HTML (starts with {repr(stripped[:20])}), skipping")
                return None, []

            print(f"[REQUESTS] OK {url} ({len(html)} bytes)")
            links = self._extract_links_from_html(html, url)
            return html, links
        except Exception as e:
            print(f"[REQUESTS] {url} failed: {e}, trying Playwright")
            return None, []

    def _extract_links_from_html(self, html: str, base_url: str) -> list[str]:
        soup = BeautifulSoup(html, "lxml")
        seen: set[str] = set()
        result = []
        for a in soup.find_all("a", href=True):
            full = urljoin(base_url, a["href"])
            if DOCS_ROOT not in full:
                continue
            if not self._is_uefn_doc_url(full):
                continue
            norm = self._normalize_url(full)
            if norm and norm not in seen:
                seen.add(norm)
                result.append(norm)
        return result

    @retry(
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=RETRY_BACKOFF_MULTIPLIER, min=2, max=30),
        reraise=True,
    )
    async def _fetch_page(self, page: Page, url: str):
        try:
            await page.goto(url, wait_until=NAVIGATION_WAIT, timeout=PAGE_LOAD_TIMEOUT_MS)
        except Exception as e:
            print(f"[WARN] goto {url}: {e}")
            raise

        final_url = page.url
        if any(p in final_url for p in ERROR_URL_PATTERNS):
            print(f"[SKIP] {url} -> blocked by Epic ({final_url})")
            return None

        if final_url != url:
            print(f"[REDIRECT] {url} -> {final_url}")
        title = await page.title()
        print(f"[PAGE] url={final_url} title={title!r}")

        try:
            await page.wait_for_selector(
                "article, main, [class*='content'], [class*='article']",
                timeout=15_000,
            )
        except Exception:
            pass

        await page.wait_for_timeout(4000)

        for sel in NAV_SELECTORS:
            try:
                await page.wait_for_selector(sel, timeout=5_000)
                break
            except Exception:
                continue

        html = await page.content()
        links = await self._extract_nav_links(page)
        return html, links

    async def _extract_nav_links(self, page: Page) -> list[str]:
        try:
            hrefs = await page.eval_on_selector_all(
                "a[href]",
                "els => els.map(e => e.href)",
            )
        except Exception as e:
            print(f"[WARN] link eval failed: {e}")
            return []

        print(f"[DEBUG] {len(hrefs)} total <a href> on {page.url}")
        uefn_raw = [h for h in hrefs if isinstance(h, str) and DOCS_ROOT in h]
        print(f"[DEBUG] {len(uefn_raw)} match DOCS_ROOT='{DOCS_ROOT}'")
        for sample in uefn_raw[:5]:
            print(f"[DEBUG]   {sample}")
        if not uefn_raw and hrefs:
            for sample in hrefs[:5]:
                print(f"[DEBUG] non-uefn sample: {sample}")

        seen: set[str] = set()
        result = []
        for h in hrefs:
            if not h or not isinstance(h, str):
                continue
            if DOCS_ROOT not in h:
                continue
            if not self._is_uefn_doc_url(h):
                continue
            normalized = self._normalize_url(h)
            if normalized and normalized not in seen:
                seen.add(normalized)
                result.append(normalized)

        return result

    def _normalize_url(self, url: str) -> str:
        try:
            p = urlparse(url)
            return urlunparse((p.scheme, p.netloc, p.path.rstrip("/"), "", "", ""))
        except Exception:
            return ""

    def _is_uefn_doc_url(self, url: str) -> bool:
        blocked = [
            "/_search", "/login", "javascript:", "mailto:",
            ".png", ".jpg", ".svg", ".pdf", ".zip", ".gif", ".webp",
            ".woff", ".woff2", ".js", ".css", ".ico", ".ttf", ".eot",
            "/api/", "/__", "/cdn-cgi/",
        ]
        return not any(b in url for b in blocked)
