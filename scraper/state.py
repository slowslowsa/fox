import json
from pathlib import Path
from scraper.config import STATE_FILE


class CrawlState:
    def __init__(self):
        self.visited: set[str] = set()
        self.failed: dict[str, int] = {}
        self.pending: list[str] = []
        self._load()

    def _load(self):
        p = Path(STATE_FILE)
        if p.exists():
            data = json.loads(p.read_text())
            self.visited = set(data.get("visited", []))
            self.failed = data.get("failed", {})
            self.pending = data.get("pending", [])

    def save(self):
        Path(STATE_FILE).write_text(
            json.dumps(
                {
                    "visited": list(self.visited),
                    "failed": self.failed,
                    "pending": self.pending,
                },
                indent=2,
            )
        )

    def mark_visited(self, url: str):
        self.visited.add(url)
        self.failed.pop(url, None)

    def mark_failed(self, url: str):
        self.failed[url] = self.failed.get(url, 0) + 1

    def is_done(self, url: str) -> bool:
        return url in self.visited

    def should_retry(self, url: str, max_retries: int = 3) -> bool:
        return self.failed.get(url, 0) < max_retries
