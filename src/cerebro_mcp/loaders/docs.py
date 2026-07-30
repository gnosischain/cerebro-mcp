import hashlib
import json
import logging
import os
import re
import threading
import time
from typing import Optional
from urllib.parse import quote, urlparse

import requests

from cerebro_mcp.config import settings

logger = logging.getLogger(__name__)
LLMS_ENTRY_RE = re.compile(
    r"^- \[(?P<title>.+?)\]\((?P<url>.+?)\)(?:: (?P<description>.*))?$"
)
GNOSIS_CHAIN_FILE_SPLIT_RE = re.compile(r"(?:^|\n)---\s*// File:\s*")
FIRST_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)


def normalize_docs_base_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/" if base_url else ""


def location_to_page_url(base_url: str, location: str) -> str:
    normalized_base = normalize_docs_base_url(base_url)
    normalized_location = location.lstrip("/")
    if not normalized_base:
        return normalized_location
    return normalized_base if not normalized_location else f"{normalized_base}{normalized_location}"


def location_to_markdown_path(location: str) -> str:
    normalized_location = location.lstrip("/")
    if not normalized_location:
        return "index.html.md"
    if normalized_location.endswith("/"):
        return f"{normalized_location}index.html.md"
    if normalized_location.endswith(".html"):
        return f"{normalized_location}.md"
    return f"{normalized_location.rstrip('/')}/index.html.md"


def location_to_markdown_url(base_url: str, location: str) -> str:
    normalized_base = normalize_docs_base_url(base_url)
    markdown_path = location_to_markdown_path(location)
    if not normalized_base:
        return markdown_path
    return f"{normalized_base}{markdown_path}"


def markdown_url_to_location(base_url: str, markdown_url: str) -> Optional[str]:
    if not markdown_url:
        return None

    parsed_url = urlparse(markdown_url)
    path = parsed_url.path or markdown_url
    base_path = urlparse(normalize_docs_base_url(base_url)).path.rstrip("/")

    if base_path and path.startswith(base_path):
        path = path[len(base_path) :]

    normalized_path = path.lstrip("/")
    if normalized_path == "index.html.md":
        return ""
    if normalized_path.endswith("/index.html.md"):
        return normalized_path[: -len("index.html.md")]
    if normalized_path.endswith(".html.md"):
        return normalized_path[: -len(".md")]
    return None


def parse_llms_index(text: str, base_url: str) -> dict[str, dict[str, str]]:
    entries: dict[str, dict[str, str]] = {}
    section = ""

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            section = stripped[3:].strip()
            continue

        match = LLMS_ENTRY_RE.match(stripped)
        if not match:
            continue

        location = markdown_url_to_location(base_url, match.group("url"))
        if location is None:
            continue

        entries[location] = {
            "title": match.group("title").strip(),
            "section": section,
            "description": (match.group("description") or "").strip(),
            "markdown_url": match.group("url").strip(),
        }

    return entries


def gnosis_chain_path_to_page_path(file_path: str) -> str:
    normalized = file_path.strip().lstrip("/")
    is_directory_index = False
    if normalized.endswith(".md"):
        normalized = normalized[: -len(".md")]
    if normalized.endswith("/README"):
        normalized = normalized[: -len("README")]
        is_directory_index = True
    elif normalized == "README":
        normalized = ""
    segments = [quote(part) for part in normalized.split("/") if part]
    quoted = "/".join(segments)
    if is_directory_index and quoted:
        return f"{quoted}/"
    return quoted


def gnosis_chain_path_to_page_url(base_url: str, file_path: str) -> str:
    normalized_base = normalize_docs_base_url(base_url)
    page_path = gnosis_chain_path_to_page_path(file_path)
    if not normalized_base:
        return page_path
    return normalized_base if not page_path else f"{normalized_base}{page_path}"


def extract_first_heading(text: str) -> str:
    match = FIRST_HEADING_RE.search(text)
    if match:
        return match.group(1).strip()
    return ""


def extract_first_paragraph(text: str) -> str:
    saw_heading = False
    for block in re.split(r"\n\s*\n", text):
        stripped = block.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            saw_heading = True
            continue
        if saw_heading:
            return " ".join(stripped.split())
    return ""


def parse_gnosis_chain_llms(text: str, base_url: str) -> list[dict[str, str]]:
    normalized = text.strip()
    if normalized.startswith("// File: "):
        normalized = "--- " + normalized

    entries: list[dict[str, str]] = []
    for chunk in GNOSIS_CHAIN_FILE_SPLIT_RE.split(normalized):
        stripped_chunk = chunk.strip()
        if not stripped_chunk:
            continue

        first_line, _, remainder = stripped_chunk.partition("\n")
        file_path = first_line.strip()
        body = remainder.strip()
        if not file_path:
            continue

        title = extract_first_heading(body) or file_path
        description = extract_first_paragraph(body)
        entries.append(
            {
                "file_path": file_path,
                "title": title,
                "description": description,
                "text": body,
                "page_url": gnosis_chain_path_to_page_url(base_url, file_path),
            }
        )

    return entries


class DocsLoader:
    """Loads and indexes MkDocs search_index.json for external docs integration."""

    def __init__(self):
        self._docs: list[dict[str, str]] = []
        self._loaded = False
        self._raw_index_data: dict | None = None
        self._llms_entries: dict[str, dict[str, str]] = {}
        self._llms_hash: str | None = None
        self._gnosis_chain_docs: list[dict[str, str]] = []
        self._gnosis_chain_hash: str | None = None
        self._artifact_cache: dict[str, str] = {}

        # Conditional GET state
        self._etag: str | None = None
        self._last_modified_header: str | None = None
        self._content_hash: str | None = None
        self._last_load_time: float = 0.0
        self._last_refresh_error: str | None = None

        # Serializes refresh-and-publish. Tool bodies now run concurrently on
        # worker threads (runtime/offload.py), so two callers could otherwise
        # both cross the TTL and each re-fetch the ~5MB index plus two llms.txt
        # artifacts, and `_apply_index` could publish a torn `_docs` list.
        self._lock = threading.RLock()

        # Timestamp of the last refresh ATTEMPT, as opposed to the last
        # attempt that actually changed content. The TTL gate must read this:
        # `_last_load_time` only advances when the hash changes, so on an
        # unchanged (304) index it never moves and every subsequent call
        # re-fires all three HTTP fetches for the life of the process. That is
        # the `search_docs` stall — a tool that looks trivial but performs
        # three unbounded network round-trips per invocation.
        self._last_refresh_attempt: float = 0.0

    def load(self) -> None:
        """Load docs index from URL or local file."""
        result = self._fetch_index()
        if result:
            data, content_hash = result
            self._raw_index_data = data
            self._content_hash = content_hash
            self._last_load_time = time.time()
            self._loaded = True
        self._load_llms_index(log_errors=not self._loaded)
        self._load_gnosis_chain_llms(log_errors=False)
        if self._raw_index_data:
            self._apply_index(self._raw_index_data)

    def _fetch_index(
        self, conditional: bool = False
    ) -> Optional[tuple[dict, str]]:
        """Fetch index from URL with local file fallback.

        Returns:
            Tuple of (parsed_data, content_hash) or None if unchanged/unavailable.
        """
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
                    timeout=(settings.HTTP_CONNECT_TIMEOUT_SECONDS, timeout),
                    headers=headers,
                )

                if resp.status_code == 304:
                    return None  # Not modified

                if resp.status_code == 200:
                    self._etag = resp.headers.get("ETag")
                    self._last_modified_header = resp.headers.get("Last-Modified")
                    self._last_refresh_error = None
                    content_hash = self._hash_bytes(resp.content)
                    if not conditional:
                        logger.info(
                            "Loaded docs index from %s",
                            settings.DOCS_SEARCH_INDEX_URL,
                        )
                    return resp.json(), content_hash

                error_msg = (
                    f"Failed to fetch docs index: HTTP {resp.status_code}"
                )
                if conditional:
                    self._last_refresh_error = error_msg
                    return None
                logger.warning(error_msg)
            except Exception as e:
                error_msg = f"Error fetching docs index URL: {e}"
                if conditional:
                    self._last_refresh_error = error_msg
                    return None
                logger.warning(error_msg)

        if conditional:
            return None

        # Fallback to local file
        if settings.DOCS_SEARCH_INDEX_PATH and os.path.exists(
            settings.DOCS_SEARCH_INDEX_PATH
        ):
            try:
                with open(settings.DOCS_SEARCH_INDEX_PATH, "rb") as f:
                    raw = f.read()
                content_hash = self._hash_bytes(raw)
                data = json.loads(raw)
                logger.info(
                    "Loaded docs index from %s",
                    settings.DOCS_SEARCH_INDEX_PATH,
                )
                return data, content_hash
            except Exception as e:
                logger.warning("Error loading local docs index: %s", e)

        logger.warning(
            "No docs index loaded. External docs search will be unavailable."
        )
        return None

    @staticmethod
    def _hash_bytes(data: bytes) -> str:
        return hashlib.md5(data).hexdigest()

    def _docs_artifact_url(self, name: str) -> str:
        base_url = self.docs_base_url
        return f"{base_url}{name}" if base_url else ""

    def _fetch_artifact_text(self, url: str, cache: bool = True) -> Optional[str]:
        if not url:
            return None
        if cache and url in self._artifact_cache:
            return self._artifact_cache[url]

        try:
            resp = requests.get(
                url,
                timeout=(settings.HTTP_CONNECT_TIMEOUT_SECONDS, 30),
            )
        except Exception:
            return None

        if resp.status_code != 200:
            return None

        self._artifact_cache[url] = resp.text
        return resp.text

    def _load_llms_index(self, log_errors: bool) -> bool:
        overview_url = self._docs_artifact_url("llms.txt")
        if not overview_url:
            return False

        try:
            resp = requests.get(
                overview_url,
                timeout=(settings.HTTP_CONNECT_TIMEOUT_SECONDS, 30),
            )
        except Exception as exc:
            if log_errors:
                logger.warning("Error fetching docs overview URL: %s", exc)
            return False

        if resp.status_code != 200:
            if log_errors:
                logger.warning("Failed to fetch docs overview: HTTP %s", resp.status_code)
            return False

        llms_hash = self._hash_bytes(resp.content)
        changed = llms_hash != self._llms_hash
        self._llms_hash = llms_hash
        self._llms_entries = parse_llms_index(resp.text, self.docs_base_url)
        if changed:
            self._artifact_cache = {overview_url: resp.text}
        else:
            self._artifact_cache[overview_url] = resp.text
        return changed

    def _load_gnosis_chain_llms(self, log_errors: bool) -> bool:
        if not settings.GNOSIS_CHAIN_DOCS_LLM_URL:
            return False

        try:
            resp = requests.get(
                settings.GNOSIS_CHAIN_DOCS_LLM_URL,
                timeout=(settings.HTTP_CONNECT_TIMEOUT_SECONDS, 30),
            )
        except Exception as exc:
            if log_errors:
                logger.warning("Error fetching Gnosis Chain docs llms URL: %s", exc)
            return False

        if resp.status_code != 200:
            if log_errors:
                logger.warning(
                    "Failed to fetch Gnosis Chain docs llms artifact: HTTP %s",
                    resp.status_code,
                )
            return False

        llms_hash = self._hash_bytes(resp.content)
        changed = llms_hash != self._gnosis_chain_hash
        self._gnosis_chain_hash = llms_hash
        self._gnosis_chain_docs = parse_gnosis_chain_llms(
            resp.text,
            "https://docs.gnosischain.com/",
        )
        self._artifact_cache[settings.GNOSIS_CHAIN_DOCS_LLM_URL] = resp.text
        return changed

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
            llms_entry = self._llms_entries.get(location, {})

            processed.append(
                {
                    "location": location,
                    "title": doc.get("title", ""),
                    "text": text,
                    "section": llms_entry.get("section", ""),
                    "description": llms_entry.get("description", ""),
                    "markdown_url": llms_entry.get(
                        "markdown_url",
                        location_to_markdown_url(self.docs_base_url, location),
                    ),
                    "page_url": location_to_page_url(self.docs_base_url, location),
                }
            )
        self._docs = processed

    def reload_if_changed(self) -> tuple[bool, str | None]:
        """Conditional GET; rebuild index only if content changed."""
        changed = False
        error = None

        with self._lock:
            # Stamped even when nothing changed and even on failure, so the
            # TTL gate rate-limits ATTEMPTS. Set before the fetches so a
            # concurrent caller that is already past the interval does not
            # pile a second set of network round-trips onto this one.
            self._last_refresh_attempt = time.time()

            if settings.DOCS_SEARCH_INDEX_URL:
                result = self._fetch_index(conditional=True)
                if result is None:
                    error = self._last_refresh_error
                else:
                    data, new_hash = result
                    if new_hash != self._content_hash:
                        self._raw_index_data = data
                        self._content_hash = new_hash
                        self._last_load_time = time.time()
                        self._last_refresh_error = None
                        changed = True

            if self._load_llms_index(log_errors=False):
                changed = True
            if self._load_gnosis_chain_llms(log_errors=False):
                changed = True

            if self._raw_index_data:
                self._apply_index(self._raw_index_data)

        return changed, error

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """Search the docs index using token-based scoring."""
        if not self._loaded and not self._gnosis_chain_docs:
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
            description = doc.get("description", "")
            section = doc.get("section", "")

            searchable = f"{title.lower()} {description.lower()} {section.lower()} {text.lower()}"

            hits = 0
            # Title matches weighted heavily
            if any(t in title.lower() for t in tokens):
                hits += 3

            # Body matches
            hits += sum(1 for t in tokens if t in text.lower())
            hits += sum(1 for t in tokens if t in description.lower())
            hits += sum(1 for t in tokens if t in section.lower())

            # Exact phrase match boost
            if query.lower() in searchable:
                hits += 5

            if hits > 0:
                snippet_source = description or text.strip()
                snippet = snippet_source[:600]
                if len(snippet_source) > 600:
                    snippet += "\n...(truncated)"

                scored_results.append(
                    {
                        "score": hits,
                        "title": title,
                        "location": doc["location"],
                        "snippet": snippet,
                        "section": section,
                        "description": description,
                        "page_url": doc.get("page_url", ""),
                        "markdown_url": doc.get("markdown_url", ""),
                        "source": "platform",
                    }
                )

        for doc in self._gnosis_chain_docs:
            title = doc["title"]
            text = doc["text"]
            description = doc.get("description", "")
            searchable = f"{title.lower()} {doc['file_path'].lower()} {description.lower()} {text.lower()}"

            hits = 0
            if any(t in title.lower() for t in tokens):
                hits += 3
            hits += sum(1 for t in tokens if t in description.lower())
            hits += sum(1 for t in tokens if t in text.lower())
            hits += sum(1 for t in tokens if t in doc["file_path"].lower())
            if query.lower() in searchable:
                hits += 5

            if hits > 0:
                snippet_source = description or text.strip()
                snippet = snippet_source[:600]
                if len(snippet_source) > 600:
                    snippet += "\n...(truncated)"
                scored_results.append(
                    {
                        "score": hits,
                        "title": title,
                        "location": "",
                        "file_path": doc["file_path"],
                        "snippet": snippet,
                        "section": "Gnosis Chain Docs",
                        "description": description,
                        "page_url": doc["page_url"],
                        "markdown_url": "",
                        "source": "gnosis_chain",
                    }
                )

        scored_results.sort(key=lambda x: -x["score"])
        return scored_results[:limit]

    @staticmethod
    def _truncate_text(text: str, max_chars: int) -> str:
        stripped = text.strip()
        if len(stripped) <= max_chars:
            return stripped
        return stripped[:max_chars] + f"\n\n...[Truncated at {max_chars} chars]"

    def get_chunk(self, location: str, max_chars: int = 6000) -> str:
        """Retrieve full text of a documentation page by its location."""
        markdown_url = location_to_markdown_url(self.docs_base_url, location)
        markdown_text = self._fetch_artifact_text(markdown_url)
        if markdown_text is not None:
            return self._truncate_text(markdown_text, max_chars)

        for doc in self._docs:
            if doc["location"] == location:
                return self._truncate_text(doc["text"], max_chars)

        return f"Error: Document location '{location}' not found."

    def get_overview(self, max_chars: int = 6000) -> str:
        overview_url = self._docs_artifact_url("llms.txt")
        overview_text = self._fetch_artifact_text(overview_url)
        if overview_text is None:
            return "Error: Docs overview not available."
        return self._truncate_text(overview_text, max_chars)

    def get_context(self, full: bool = False, max_chars: int = 12000) -> str:
        artifact_name = "llms-ctx-full.txt" if full else "llms-ctx.txt"
        artifact_url = self._docs_artifact_url(artifact_name)
        context_text = self._fetch_artifact_text(artifact_url)
        if context_text is None:
            return f"Error: Docs context artifact '{artifact_name}' not available."
        return self._truncate_text(context_text, max_chars)

    def get_gnosis_chain_context(self, max_chars: int = 12000) -> str:
        artifact_url = settings.GNOSIS_CHAIN_DOCS_LLM_URL or ""
        context_text = self._fetch_artifact_text(artifact_url)
        if context_text is None:
            return "Error: Gnosis Chain docs llms artifact not available."
        return self._truncate_text(context_text, max_chars)

    def get_gnosis_chain_chunk(self, file_path: str, max_chars: int = 6000) -> str:
        normalized = file_path.strip().lstrip("/")
        requested_url = gnosis_chain_path_to_page_url("https://docs.gnosischain.com/", normalized)
        for doc in self._gnosis_chain_docs:
            doc_path = doc["file_path"].strip().lstrip("/")
            if normalized in {doc_path, gnosis_chain_path_to_page_path(doc_path)}:
                return self._truncate_text(doc["text"], max_chars)
            if file_path.strip() == doc["page_url"]:
                return self._truncate_text(doc["text"], max_chars)
            if requested_url == doc["page_url"]:
                return self._truncate_text(doc["text"], max_chars)
        return f"Error: Gnosis Chain document '{file_path}' not found."

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def last_refresh_attempt(self) -> float:
        """When a refresh was last ATTEMPTED (changed or not).

        TTL gates must use this, not :attr:`last_load_time` — that one only
        advances on a content change, so an unchanged index leaves it frozen
        and every later call re-fetches. Falls back to ``last_load_time`` so a
        process that only ever called ``load()`` still reports sensibly.
        """
        return self._last_refresh_attempt or self._last_load_time

    @property
    def entry_count(self) -> int:
        return len(self._docs)

    @property
    def last_load_time(self) -> float:
        return self._last_load_time

    @property
    def last_refresh_error(self) -> str | None:
        return self._last_refresh_error

    @property
    def docs_base_url(self) -> str:
        return normalize_docs_base_url(settings.DOCS_BASE_URL)

    @property
    def llms_entry_count(self) -> int:
        return len(self._llms_entries)

    @property
    def gnosis_chain_entry_count(self) -> int:
        return len(self._gnosis_chain_docs)


# Singleton instance
docs_index = DocsLoader()
