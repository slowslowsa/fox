import re
from pathlib import Path
from urllib.parse import urlparse
import yaml
from scraper.config import OUTPUT_DIR


def url_to_path(url: str) -> Path:
    path = urlparse(url).path
    path = re.sub(r"^/documentation/en-us", "", path)
    path = path.strip("/")

    parts = [_slugify(p) for p in path.split("/") if p]
    if not parts:
        parts = ["index"]

    output_path = Path(OUTPUT_DIR) / Path(*parts)
    return output_path.with_suffix(".md")


def write_page(url: str, content: dict) -> None:
    path = url_to_path(url)
    path.parent.mkdir(parents=True, exist_ok=True)

    frontmatter = {
        "title": content["title"],
        "url": content["url"],
        "section": content["section"],
        "breadcrumbs": content["breadcrumbs"],
    }

    output = "---\n"
    output += yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True)
    output += "---\n\n"
    output += f"# {content['title']}\n\n"
    output += content["body_markdown"]
    output += "\n"

    path.write_text(output, encoding="utf-8")
    print(f"[WROTE] {path}")


def _slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\-_]", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "index"
