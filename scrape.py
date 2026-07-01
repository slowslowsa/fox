#!/usr/bin/env python3
"""
UEFN Documentation Scraper

Usage:
  python3 scrape.py --local             # Local PC: live 2026 content, all links (RECOMMENDED for local use)
  python3 scrape.py --local --limit 5   # Quick local test (5 pages)
  python3 scrape.py                     # Full crawl via Wayback (for CI/GitHub Actions)
  python3 scrape.py --crawl-only        # Skip URL discovery, use seeds only
  python3 scrape.py --resume            # Resume from saved state
  python3 scrape.py --url <URL>         # Scrape a single URL for testing
"""
import argparse
import asyncio

from scraper.config import DOCS_BASE_URL
from scraper.crawler import UEFNCrawler
from scraper.sitemap import fetch_sitemap_urls, fetch_wayback_url_map
from scraper.state import CrawlState

ROOT_URL = DOCS_BASE_URL + "/unreal-editor-for-fortnite-documentation"

KNOWN_SECTION_URLS = [
    DOCS_BASE_URL + "/unreal-editor-for-fortnite-documentation",
    DOCS_BASE_URL + "/verse-language-reference",
    DOCS_BASE_URL + "/scripting-with-verse",
    DOCS_BASE_URL + "/learning-about-programming-with-verse",
    DOCS_BASE_URL + "/verse-api",
    DOCS_BASE_URL + "/building-in-fortnite",
    DOCS_BASE_URL + "/devices",
    DOCS_BASE_URL + "/tutorials",
    DOCS_BASE_URL + "/working-with-assets",
    DOCS_BASE_URL + "/animation",
    DOCS_BASE_URL + "/audio",
    DOCS_BASE_URL + "/cinematics",
    DOCS_BASE_URL + "/landscape",
    DOCS_BASE_URL + "/lighting",
    DOCS_BASE_URL + "/materials",
    DOCS_BASE_URL + "/niagara",
    DOCS_BASE_URL + "/physics",
    DOCS_BASE_URL + "/sequencer",
    DOCS_BASE_URL + "/terrain",
    DOCS_BASE_URL + "/user-interface",
    DOCS_BASE_URL + "/fortnite-creative",
    DOCS_BASE_URL + "/character",
    DOCS_BASE_URL + "/game-features",
    DOCS_BASE_URL + "/islands",
    DOCS_BASE_URL + "/uefn-glossary",
    DOCS_BASE_URL + "/what-s-new-in-uefn",
    DOCS_BASE_URL + "/using-creator-services",
    DOCS_BASE_URL + "/debugging-in-verse",
    DOCS_BASE_URL + "/verse-concurrency",
    DOCS_BASE_URL + "/verse-types",
]


def parse_args():
    p = argparse.ArgumentParser(description="Scrape the complete UEFN documentation")
    p.add_argument("--crawl-only", action="store_true", help="Skip URL discovery, use seeds only")
    p.add_argument("--resume", action="store_true", help="Resume from saved state")
    p.add_argument("--url", type=str, help="Scrape a single URL for testing")
    p.add_argument("--limit", type=int, default=0, help="Stop after N pages (0 = no limit)")
    p.add_argument("--local", action="store_true",
                   help="Local mode: Playwright first (live 2026 content + all links), Wayback as fallback")
    return p.parse_args()


async def main():
    args = parse_args()
    state = CrawlState()

    url_timestamps: dict[str, str] = {}
    seed_urls: list[str] = []

    if args.url:
        seed_urls = [args.url]
        print(f"[SINGLE] Testing single URL: {args.url}")
    elif args.resume and state.pending:
        seed_urls = list(state.pending)
        print(f"[RESUME] {len(seed_urls)} pending URLs from state")
    else:
        if not args.crawl_only:
            # Wayback CDX: primary source — gives URLs + timestamps for id_ fetch
            print("[WAYBACK] Fetching archived URL list...")
            url_timestamps = fetch_wayback_url_map()
            if url_timestamps:
                seed_urls = list(url_timestamps.keys())
            else:
                # Fallback: try live sitemap (no timestamps, Wayback will use default)
                print("[SITEMAP] CDX failed, trying sitemap...")
                sitemap_urls = fetch_sitemap_urls()
                if sitemap_urls:
                    seed_urls = sitemap_urls

        # Always add hardcoded seeds to fill gaps
        seed_set = set(seed_urls)
        for u in KNOWN_SECTION_URLS:
            if u not in seed_set:
                seed_urls.append(u)

    if args.resume:
        done = state.visited
        seed_urls = [u for u in seed_urls if u not in done]

    if args.limit:
        print(f"[LIMIT] Quick test mode: stopping after {args.limit} pages")

    if args.local:
        print("[MODE] Local: Playwright-first (live 2026 content, all clickable links captured)")
    print(f"[START] {len(seed_urls)} seed URL(s) | {len(url_timestamps)} with Wayback timestamps")

    crawler = UEFNCrawler(limit=args.limit, url_timestamps=url_timestamps, live_first=args.local)
    if args.resume:
        crawler.visited = state.visited.copy()

    try:
        await crawler.run(seed_urls)
    except KeyboardInterrupt:
        print("\n[INTERRUPT] Saving state...")
    finally:
        state.visited = crawler.visited
        try:
            pending = list(crawler.queue._queue)
        except Exception:
            pending = []
        state.pending = pending
        state.failed = crawler.failed
        state.save()

        total = len(crawler.visited)
        failed = len(crawler.failed)
        print(f"\n[DONE] Visited {total} pages, {crawler._written} written, {failed} failed")
        if crawler.failed:
            print("[FAILED URLS]")
            for url, count in list(crawler.failed.items())[:20]:
                print(f"  {url} ({count} attempts)")


if __name__ == "__main__":
    asyncio.run(main())
