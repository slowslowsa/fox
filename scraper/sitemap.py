import xml.etree.ElementTree as ET
import requests
from scraper.config import SITEMAP_URLS, DOCS_ROOT, BASE_URL, CA_BUNDLE, WAYBACK_CDX_URL

_SKIP_EXTS = (".png", ".jpg", ".svg", ".pdf", ".zip", ".gif", ".webp", ".js", ".css",
               ".woff", ".woff2", ".ico", ".ttf", ".eot")


def fetch_sitemap_urls() -> list[str]:
    for sitemap_url in SITEMAP_URLS:
        urls = _try_sitemap(sitemap_url)
        if urls:
            print(f"[SITEMAP] Found {len(urls)} UEFN URLs in {sitemap_url}")
            return urls
    return []


def fetch_wayback_urls() -> list[str]:
    """Get all UEFN URLs from Wayback Machine CDX API."""
    url_map = fetch_wayback_url_map()
    return list(url_map.keys())


def fetch_wayback_url_map() -> dict[str, str]:
    """Return {original_url: latest_timestamp} for all archived UEFN pages."""
    host = BASE_URL.replace("https://", "").replace("http://", "")
    valid_prefix = f"https://{host}{DOCS_ROOT}/"

    try:
        print("[WAYBACK] Fetching URL+timestamp list from CDX API...")
        r = requests.get(WAYBACK_CDX_URL, timeout=60, verify=CA_BUNDLE)
        if r.status_code != 200:
            print(f"[WAYBACK] CDX returned HTTP {r.status_code}")
            return {}
        rows = r.json()
        url_map: dict[str, str] = {}
        for row in rows[1:]:
            if len(row) < 2:
                continue
            ts, u = row[0], row[1]
            u = u.split("?")[0].split("#")[0].rstrip("/")
            if u.startswith("http://"):
                u = "https://" + u[7:]
            if not u.startswith(valid_prefix):
                continue
            if any(u.endswith(ext) for ext in _SKIP_EXTS):
                continue
            # Keep the latest timestamp for each URL
            if u not in url_map or ts > url_map[u]:
                url_map[u] = ts
        print(f"[WAYBACK] {len(url_map)} unique UEFN URLs found")
        return url_map
    except Exception as e:
        print(f"[WAYBACK] CDX API failed: {e}")
        return {}


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
