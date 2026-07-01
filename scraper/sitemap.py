import xml.etree.ElementTree as ET
import requests
from scraper.config import SITEMAP_URLS, DOCS_ROOT, BASE_URL, CA_BUNDLE


def fetch_sitemap_urls() -> list[str]:
    for sitemap_url in SITEMAP_URLS:
        urls = _try_sitemap(sitemap_url)
        if urls:
            print(f"[SITEMAP] Found {len(urls)} UEFN URLs in {sitemap_url}")
            return urls
    return []


def fetch_wayback_urls() -> list[str]:
    """Get all UEFN URLs from Wayback Machine CDX API (archive.org index)."""
    host = BASE_URL.replace("https://", "").replace("http://", "")
    cdx = (
        "https://web.archive.org/cdx/search/cdx"
        f"?url={host}{DOCS_ROOT}/*"
        "&output=json&fl=original&collapse=urlkey&limit=10000"
    )
    try:
        print(f"[WAYBACK] Fetching URL list from CDX API...")
        r = requests.get(cdx, timeout=60, verify=CA_BUNDLE)
        if r.status_code != 200:
            print(f"[WAYBACK] CDX returned HTTP {r.status_code}")
            return []
        rows = r.json()
        # First row is header ["original"], rest are data rows
        urls = []
        seen = set()
        skip_exts = (".png", ".jpg", ".svg", ".pdf", ".zip", ".gif", ".webp", ".js", ".css")
        for row in rows[1:]:
            u = row[0].split("?")[0].split("#")[0].rstrip("/")
            if DOCS_ROOT not in u:
                continue
            if any(u.endswith(ext) for ext in skip_exts):
                continue
            if not u.startswith("http"):
                u = "https://" + u
            if u not in seen:
                seen.add(u)
                urls.append(u)
        print(f"[WAYBACK] {len(urls)} unique UEFN URLs found")
        return urls
    except Exception as e:
        print(f"[WAYBACK] CDX API failed: {e}")
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
