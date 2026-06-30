import re
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from markdownify import markdownify as md

CONTENT_SELECTORS = [
    "article.documentation-article",
    "main article",
    "[class*='DocumentationPage'] article",
    "[class*='article-content']",
    "[class*='page-content']",
    "[class*='content-body']",
    "article",
    "main",
]

JUNK_SELECTORS = [
    "nav",
    "header",
    "footer",
    "[class*='sidebar']",
    "[class*='Sidebar']",
    "[class*='toc']",
    "[class*='TableOfContents']",
    "[class*='feedback']",
    "[class*='cookie']",
    "[class*='Cookie']",
    "[class*='SearchBar']",
    "[class*='NavigationBar']",
    "[class*='BreadcrumbNav']",
    "script",
    "style",
    "noscript",
    "[aria-hidden='true']",
]

BREADCRUMB_SELECTORS = [
    "[class*='breadcrumb'] a",
    "[class*='Breadcrumb'] a",
    "nav[aria-label*='breadcrumb'] a",
    "[class*='breadcrumb'] span",
]


def extract_content(url: str, html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")

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


def _extract_title(soup: BeautifulSoup) -> str:
    for sel in ["h1", "[class*='page-title']", "[class*='article-title']", "[class*='PageTitle']"]:
        el = soup.select_one(sel)
        if el:
            return el.get_text(strip=True)
    tag = soup.find("title")
    if tag:
        return re.sub(r"\s*\|\s*Epic.*$", "", tag.get_text(strip=True)).strip()
    return "Untitled"


def _extract_breadcrumbs(soup: BeautifulSoup) -> list[str]:
    for sel in BREADCRUMB_SELECTORS:
        els = soup.select(sel)
        if els:
            return [e.get_text(strip=True) for e in els if e.get_text(strip=True)]
    return []


def _extract_body(soup: BeautifulSoup) -> str:
    for sel in JUNK_SELECTORS:
        for el in soup.select(sel):
            el.decompose()

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
        strip=["script", "style"],
    )

    raw_md = re.sub(r"\n{3,}", "\n\n", raw_md).strip()
    return raw_md


def _infer_section(url: str) -> str:
    path = urlparse(url).path
    parts = [p for p in path.split("/") if p]
    try:
        idx = parts.index("uefn")
        return parts[idx + 1] if idx + 1 < len(parts) else "root"
    except ValueError:
        return "unknown"
