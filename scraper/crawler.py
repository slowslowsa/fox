import asyncio
from urllib.parse import urlparse, urlunparse

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
            ignore_https_errors=False,
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
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
            print(f"[WARN] goto failed for {url}: {e}")
            raise

        try:
            await page.wait_for_selector(
                "article, main, [class*='content'], [class*='article']",
                timeout=PAGE_LOAD_TIMEOUT_MS,
            )
        except Exception:
            pass  # Proceed even if selector not found

        html = await page.content()
        links = await self._extract_nav_links(page)
        return html, links

    async def _extract_nav_links(self, page: Page) -> list[str]:
        try:
            hrefs = await page.eval_on_selector_all(
                "a[href]",
                "els => els.map(e => e.href)",
            )
        except Exception:
            return []

        result = []
        for h in hrefs:
            if DOCS_ROOT in h and self._is_uefn_doc_url(h):
                normalized = self._normalize_url(h)
                if normalized:
                    result.append(normalized)
        return result

    def _normalize_url(self, url: str) -> str:
        p = urlparse(url)
        return urlunparse((p.scheme, p.netloc, p.path.rstrip("/"), "", "", ""))

    def _is_uefn_doc_url(self, url: str) -> bool:
        blocked = [
            "/_search", "/login", "javascript:", "mailto:", "#",
            ".png", ".jpg", ".svg", ".pdf", ".zip", ".gif", ".webp",
        ]
        return not any(b in url for b in blocked)
