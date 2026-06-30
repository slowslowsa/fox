import asyncio
from urllib.parse import urlparse, urlunparse

from playwright.async_api import async_playwright, BrowserContext, Page
from tenacity import retry, stop_after_attempt, wait_exponential

from scraper.config import (
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


class UEFNCrawler:
    def __init__(self):
        self.visited: set[str] = set()
        self.failed: dict[str, int] = {}
        self.queue: asyncio.Queue = asyncio.Queue()
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_PAGES)

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

    async def _worker(self, browser):
        context = await browser.new_context(
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            },
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
        self.visited.add(url)

        page = await context.new_page()
        try:
            result = await self._fetch_page(page, url)
            if result is None:
                return
            html, discovered_urls = result

            if html:
                content = extract_content(url, html)
                write_page(url, content)

            print(f"[LINKS] {len(discovered_urls)} new links on {url}")
            for link in discovered_urls:
                if link not in self.visited:
                    await self.queue.put(link)
        finally:
            await page.close()

        await asyncio.sleep(REQUEST_DELAY_SECONDS)

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

        # Wait for main article content
        try:
            await page.wait_for_selector(
                "article, main, [class*='content'], [class*='article']",
                timeout=15_000,
            )
        except Exception:
            pass

        # Extra wait for React to render navigation sidebar
        await page.wait_for_timeout(4000)

        # Try to wait for UEFN-specific nav links
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
            "/api/", "/__", "/cdn-cgi/",
        ]
        return not any(b in url for b in blocked)
