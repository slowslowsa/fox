import xml.etree.ElementTree as ET
import requests
from scraper.config import SITEMAP_URLS, DOCS_ROOT, CA_BUNDLE


def fetch_sitemap_urls() -> list[str]:
    for sitemap_url in SITEMAP_URLS:
        urls = _try_sitemap(sitemap_url)
        if urls:
            print(f"[SITEMAP] Found {len(urls)} UEFN URLs in {sitemap_url}")
            return urls
    return []


def _try_sitemap(url: str) -> list[str]:
    try:
        r = requests.get(url, timeout=15, verify=CA_BUNDLE)
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.content)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

        if root.tag.endswith("sitemapindex"):
            all_urls = []
            for loc in root.findall(".//sm:loc", ns):
                sub = _try_sitemap(loc.text.strip())
                all_urls.extend(sub)
            return all_urls

        urls = []
        for loc in root.findall(".//sm:loc", ns):
            u = loc.text.strip()
            if DOCS_ROOT in u:
                urls.append(u)
        return urls
    except Exception as e:
        print(f"[SITEMAP] Could not fetch {url}: {e}")
        return []
