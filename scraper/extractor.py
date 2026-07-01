import re
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from markdownify import markdownify as md

# Ordered from most specific to least specific
CONTENT_SELECTORS = [
    "article.documentation-article",
    "[class*='DocumentationArticle']",
    "[class*='documentation-content']",
    "[class*='ArticleContent']",
    "[class*='article-content']",
    "[class*='page-content']",
    "[class*='content-body']",
    "[class*='main-content']",
    "[class*='doc-content']",
    "main article",
    "[class*='DocumentationPage'] article",
    "article",
    "main",
    "[role='main']",
]

TITLE_SELECTORS = [
    "h1",
    "[class*='page-title']",
    "[class*='PageTitle']",
    "[class*='article-title']",
    "[class*='ArticleTitle']",
    "[class*='doc-title']",
    "[class*='heading-1']",
]

JUNK_SELECTORS = [
    # Wayback Machine toolbar
    "#wm-ipp-base", "#wm-ipp", ".wb-autocomplete-suggestion",
    "[id^='wm-']",
    # Navigation / chrome
    "nav", "header", "footer",
    "[class*='sidebar']", "[class*='Sidebar']",
    "[class*='toc']", "[class*='TableOfContents']", "[class*='OnThisPage']",
    "[class*='feedback']", "[class*='Feedback']",
    "[class*='cookie']", "[class*='Cookie']",
    "[class*='SearchBar']", "[class*='search-bar']",
    "[class*='NavigationBar']", "[class*='NavBar']",
    "[class*='BreadcrumbNav']", "[class*='breadcrumb']",
    "[class*='banner']", "[class*='Banner']",
    "[class*='alert']", "[class*='Alert']",
    "[class*='pagination']", "[class*='Pagination']",
    "[class*='RelatedLinks']", "[class*='related-links']",
    # Technical noise
    "script", "style", "noscript", "iframe",
    "[aria-hidden='true']",
    "[class*='skip-link']",
]

BREADCRUMB_SELECTORS = [
    "[class*='breadcrumb'] a",
    "[class*='Breadcrumb'] a",
    "nav[aria-label*='breadcrumb'] a",
    "[aria-label*='breadcrumb'] a",
    "[class*='breadcrumb'] span",
    "[class*='Breadcrumb'] span",
]


def extract_content(url: str, html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    _strip_junk(soup)

    title = _extract_title(soup)
    breadcrumbs = _extract_breadcrumbs(soup)
    section = _infer_section(url)
    body_md = _extract_body(soup)

    return {
        "url": url,
        "title": title,
        "breadcrumbs": breadcrumbs,
        "section": section,
        "body_markdown": body_md,
    }


def has_real_content(content: dict) -> bool:
    """Return True if the extracted content is meaningful (not an empty CSR shell)."""
    return bool(content["title"]) and len(content["body_markdown"].strip()) > 150


def _strip_junk(soup: BeautifulSoup) -> None:
    for sel in JUNK_SELECTORS:
        for el in soup.select(sel):
            el.decompose()


def _extract_title(soup: BeautifulSoup) -> str:
    for sel in TITLE_SELECTORS:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(strip=True)
            if text:
                return text
    tag = soup.find("title")
    if tag:
        raw = tag.get_text(strip=True)
        # Strip "| Epic Games Developer" and similar suffixes
        cleaned = re.sub(r"\s*[\|\-]\s*(Epic|Developer|Fortnite).*$", "", raw, flags=re.IGNORECASE).strip()
        if cleaned:
            return cleaned
    return ""


def _extract_breadcrumbs(soup: BeautifulSoup) -> list[str]:
    for sel in BREADCRUMB_SELECTORS:
        els = soup.select(sel)
        if els:
            texts = [e.get_text(strip=True) for e in els if e.get_text(strip=True)]
            if texts:
                return texts
    return []


def _extract_body(soup: BeautifulSoup) -> str:
    content_el = None
    for sel in CONTENT_SELECTORS:
        content_el = soup.select_one(sel)
        if content_el:
            break

    if not content_el:
        content_el = soup.find("body") or soup

    raw_md = md(
        str(content_el),
        heading_style="ATX",
        bullets="-",
        strip=["script", "style", "noscript", "iframe"],
        convert_links=False,
    )

    # Collapse excessive blank lines
    raw_md = re.sub(r"\n{3,}", "\n\n", raw_md).strip()
    # Remove lines that are just whitespace
    raw_md = "\n".join(line for line in raw_md.splitlines() if line.strip() or not line)
    return raw_md.strip()


def _infer_section(url: str) -> str:
    path = urlparse(url).path
    parts = [p for p in path.split("/") if p]
    for marker in ("uefn", "fortnite"):
        try:
            idx = parts.index(marker)
            return parts[idx + 1] if idx + 1 < len(parts) else "root"
        except ValueError:
            continue
    return "unknown"
