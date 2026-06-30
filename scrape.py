#!/usr/bin/env python3
"""
UEFN Documentation Scraper

Usage:
  python3 scrape.py                     # Full crawl (sitemap + recursive)
  python3 scrape.py --sitemap-only      # Sitemap only
  python3 scrape.py --crawl-only        # Skip sitemap, start from root URL
  python3 scrape.py --resume            # Resume from saved state
  python3 scrape.py --url <URL>         # Scrape a single URL (for testing)
"""
import argparse
import asyncio
import sys

from scraper.config import DOCS_BASE_URL
from scraper.crawler import UEFNCrawler
from scraper.sitemap import fetch_sitemap_urls
from scraper.state import CrawlState

ROOT_URL = DOCS_BASE_URL + "/unreal-editor-for-fortnite-documentation"


def parse_args():
    p = argparse.ArgumentParser(description="Scrape the complete UEFN documentation")
    p.add_argument("--sitemap-only", action="store_true", help="Only use sitemap URLs")
    p.add_argument("--crawl-only", action="store_true", help="Skip sitemap, crawl from root")
    p.add_argument("--resume", action="store_true", help="Resume from saved state")
    p.add_argument("--url", type=str, help="Scrape a single URL for testing")
    return p.parse_args()


async def main():
    args = parse_args()
    state = CrawlState()

    if args.url:
        seed_urls = [args.url]
        print(f"[SINGLE] Testing single URL: {args.url}")
    elif args.resume and state.pending:
        seed_urls = state.pending
        print(f"[RESUME] {len(seed_urls)} pending URLs from state")
    else:
        seed_urls = []

        if not args.crawl_only:
            print("[SITEMAP] Trying sitemap discovery...")
            sitemap_urls = fetch_sitemap_urls()
            if sitemap_urls:
                seed_urls = sitemap_urls
            else:
                print("[SITEMAP] No sitemap found, falling back to recursive crawl")

        if not seed_urls or not args.sitemap_only:
            if ROOT_URL not in seed_urls:
                seed_urls.insert(0, ROOT_URL)

    if args.resume:
        seed_urls = [u for u in seed_urls if not state.is_done(u)]

    print(f"[START] {len(seed_urls)} seed URL(s) to process")

    crawler = UEFNCrawler()
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
        print(f"\n[DONE] Visited {total} pages, {failed} failed")
        if crawler.failed:
            print("[FAILED URLS]")
            for url, count in crawler.failed.items():
                print(f"  {url} ({count} attempts)")


if __name__ == "__main__":
    asyncio.run(main())
