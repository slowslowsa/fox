import os

BASE_URL = "https://dev.epicgames.com"
DOCS_ROOT = "/documentation/en-us/uefn"
DOCS_BASE_URL = BASE_URL + DOCS_ROOT

SITEMAP_URLS = [
    "https://dev.epicgames.com/sitemap.xml",
    "https://dev.epicgames.com/documentation/sitemap.xml",
    "https://dev.epicgames.com/documentation/en-us/uefn/sitemap.xml",
]

REQUEST_DELAY_SECONDS = 1.5
MAX_CONCURRENT_PAGES = 2
MAX_RETRIES = 3
RETRY_BACKOFF_MULTIPLIER = 2.0
PAGE_LOAD_TIMEOUT_MS = 60_000
NAVIGATION_WAIT = "networkidle"

OUTPUT_DIR = "docs"
STATE_FILE = "scraper_state.json"

# Use system proxy if set
_proxy_server = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
PROXY_CONFIG = {"server": _proxy_server} if _proxy_server else None

# CA bundle: use sandbox bundle if available, else certifi, else system default
_ca_path = os.environ.get("NODE_EXTRA_CA_CERTS", "/root/.ccr/ca-bundle.crt")
if os.path.exists(_ca_path):
    CA_BUNDLE = _ca_path
else:
    try:
        import certifi
        CA_BUNDLE = certifi.where()
    except ImportError:
        CA_BUNDLE = True  # Use system default certs
