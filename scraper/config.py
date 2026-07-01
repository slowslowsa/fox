import os

BASE_URL = "https://dev.epicgames.com"
DOCS_ROOT = "/documentation/en-us/uefn"
DOCS_BASE_URL = BASE_URL + DOCS_ROOT

# Epic moved URLs to /documentation/fortnite/ — support both in link discovery
DOCS_ROOTS_ALLOWED = [
    "/documentation/en-us/uefn",
    "/documentation/fortnite",
]

SITEMAP_URLS = [
    "https://dev.epicgames.com/sitemap.xml",
    "https://dev.epicgames.com/documentation/sitemap.xml",
    "https://dev.epicgames.com/documentation/en-us/uefn/sitemap.xml",
]

# Wayback Machine: fetch archived HTML directly (no JS, no blocking)
WAYBACK_CDX_URL = (
    "https://web.archive.org/cdx/search/cdx"
    "?url=dev.epicgames.com/documentation/en-us/uefn/*"
    "&output=json&fl=timestamp,original&collapse=urlkey&limit=10000"
)
WAYBACK_FETCH_BASE = "https://web.archive.org/web/{timestamp}/{url}"
WAYBACK_TIMESTAMP = "20250101000000"  # use snapshot nearest to this date

REQUEST_DELAY_SECONDS = 0.5
WAYBACK_DELAY_SECONDS = 1.0   # be respectful to archive.org
MAX_CONCURRENT_PAGES = 4
MAX_RETRIES = 2
RETRY_BACKOFF_MULTIPLIER = 2.0
PAGE_LOAD_TIMEOUT_MS = 30_000
NAVIGATION_WAIT = "domcontentloaded"

OUTPUT_DIR = "docs"
STATE_FILE = "scraper_state.json"

_proxy_server = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
PROXY_CONFIG = {"server": _proxy_server} if _proxy_server else None

_ca_path = os.environ.get("NODE_EXTRA_CA_CERTS", "/root/.ccr/ca-bundle.crt")
if os.path.exists(_ca_path):
    CA_BUNDLE = _ca_path
else:
    try:
        import certifi
        CA_BUNDLE = certifi.where()
    except ImportError:
        CA_BUNDLE = True
