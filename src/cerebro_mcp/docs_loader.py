import hashlib
import json
import os
import re
import time
from typing import Optional

import requests

from cerebro_mcp.config import settings


class DocsLoader:
    """Loads and indexes MkDocs search_index.json for external docs integration."""

    def __init__(self):
        self._docs: list[dict[str, str]] = []
        self._loaded = False

        # Conditional GET state
        self._etag: str | None = None
        self._last_modified_header: str | None = None
        self._content_hash: str | None = None
        self._last_load_time: float = 0.0
        self._last_refresh_error: str | None = None

    def load(self) -> None:
        """Load docs index from URL or local file."""
        data = self._fetch_index()
        if data:
            self._apply_index(data)
            self._content_hash = self._hash_bytes(
                json.dumps(data, sort_keys=True).encode()
            )
            self._last_load_time = time.time()
            self._loaded = True

    def _fetch_index(self, conditional: bool = False) -> Optional[dict]:
        """Fetch index from URL with local file fallback."""
        if settings.DOCS_SEARCH_INDEX_URL:
            try:
                headers = {}
                timeout = 30
                if conditional:
                    timeout = 5
                    if self._etag:
                        headers["If-None-Match"] = self._etag
                    if self._last_modified_header:
                        headers["If-Modified-Since"] = self._last_modified_header

                resp = requests.get(
                    settings.DOCS_SEARCH_INDEX_URL,
                    timeout=timeout,
                    headers=headers,
                )

                if resp.status_code == 304:
                    return None  # Not modified

                if resp.status_code == 200:
                    self._etag = resp.headers.get("ETag")
                    self._last_modified_header = resp.headers.get("Last-Modified")
                    self._last_refresh_error = None
                    if not conditional:
                        print(
                            f"Loaded docs index from {settings.DOCS_SEARCH_INDEX_URL}"
                        )
                    return resp.json()

                error_msg = (
                    f"Failed to fetch docs index: HTTP {resp.status_code}"
                )
                if conditional:
                    self._last_refresh_error = error_msg
                    return None
                print(error_msg)
            except Exception as e:
                error_msg = f"Error fetching docs index URL: {e}"
                if conditional:
                    self._last_refresh_error = error_msg
                    return None
                print(error_msg)

        if conditional:
            return None

        # Fallback to local file
        if settings.DOCS_SEARCH_INDEX_PATH and os.path.exists(
            settings.DOCS_SEARCH_INDEX_PATH
        ):
            try:
                with open(settings.DOCS_SEARCH_INDEX_PATH, "r") as f:
                    data = json.load(f)
                print(
                    f"Loaded docs index from {settings.DOCS_SEARCH_INDEX_PATH}"
                )
                return data
            except Exception as e:
                print(f"Error loading local docs index: {e}")

        print(
            "Warning: No docs index loaded. "
            "External docs search will be unavailable."
        )
        return None

    @staticmethod
    def _hash_bytes(data: bytes) -> str:
        return hashlib.md5(data).hexdigest()

    def _apply_index(self, data: dict) -> None:
        """Parse MkDocs search_index format, filter Dune queries, strip HTML."""
        raw_docs = data.get("docs", [])
        processed = []
        for doc in raw_docs:
            location = doc.get("location", "")

            # Skip dune-queries (handled by native MCP tools)
            if location.startswith("reference/dune-queries"):
                continue

            # Strip HTML tags
            text = re.sub(r"<[^>]+>", " ", doc.get("text", ""))

            processed.append(
                {
                    "location": location,
                    "title": doc.get("title", ""),
                    "text": text,
                }
            )
        self._docs = processed

    def reload_if_changed(self) -> tuple[bool, str | None]:
        """Conditional GET; rebuild index only if content changed."""
        if not settings.DOCS_SEARCH_INDEX_URL:
            return False, None

        data = self._fetch_index(conditional=True)
        if data is None:
            return False, self._last_refresh_error

        new_hash = self._hash_bytes(
            json.dumps(data, sort_keys=True).encode()
        )
        if new_hash == self._content_hash:
            return False, None

        self._apply_index(data)
        self._content_hash = new_hash
        self._last_load_time = time.time()
        self._last_refresh_error = None
        return True, None

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """Search the docs index using token-based scoring."""
        if not self._loaded:
            return []

        # Tokenize query
        raw_tokens = re.split(r"\s+", query.lower())
        tokens = [t for t in raw_tokens if len(t) >= 3]
        if not tokens:
            tokens = raw_tokens

        scored_results = []
        for doc in self._docs:
            title = doc["title"]
            text = doc["text"]

            searchable = f"{title.lower()} {text.lower()}"

            hits = 0
            # Title matches weighted heavily
            if any(t in title.lower() for t in tokens):
                hits += 3

            # Body matches
            hits += sum(1 for t in tokens if t in text.lower())

            # Exact phrase match boost
            if query.lower() in searchable:
                hits += 5

            if hits > 0:
                snippet = text.strip()[:600]
                if len(text.strip()) > 600:
                    snippet += "\n...(truncated)"

                scored_results.append(
                    {
                        "score": hits,
                        "title": title,
                        "location": doc["location"],
                        "snippet": snippet,
                    }
                )

        scored_results.sort(key=lambda x: -x["score"])
        return scored_results[:limit]

    def get_chunk(self, location: str, max_chars: int = 6000) -> str:
        """Retrieve full text of a documentation page by its location."""
        if not self._loaded:
            return "Error: Documentation index not loaded."

        for doc in self._docs:
            if doc["location"] == location:
                text = doc["text"].strip()
                if len(text) > max_chars:
                    return (
                        text[:max_chars]
                        + f"\n\n...[Truncated at {max_chars} chars]"
                    )
                return text

        return f"Error: Document location '{location}' not found."

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def entry_count(self) -> int:
        return len(self._docs)

    @property
    def last_load_time(self) -> float:
        return self._last_load_time

    @property
    def last_refresh_error(self) -> str | None:
        return self._last_refresh_error


# Singleton instance
docs_index = DocsLoader()
