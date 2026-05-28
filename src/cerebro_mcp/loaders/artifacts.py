from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any
from collections.abc import Callable

import requests

from cerebro_mcp.runtime.observability import log_event


logger = logging.getLogger(__name__)


@dataclass
class ArtifactPayload:
    body: Any
    content_hash: str
    etag: str | None
    last_modified: str | None
    source: str


def local_artifact_candidates(filename: str, *source_paths: str) -> list[str]:
    """Derive stable local artifact candidates from related file paths."""
    seen: set[str] = set()
    candidates: list[str] = []
    for raw_path in source_paths:
        if not raw_path or not isinstance(raw_path, (str, os.PathLike)):
            continue
        expanded = os.path.abspath(os.path.expanduser(raw_path))
        candidate = (
            expanded
            if os.path.basename(expanded) == filename
            else os.path.join(os.path.dirname(expanded), filename)
        )
        if candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)
    return candidates


class ArtifactLoader:
    """Shared JSON artifact loader with local-first, URL-fallback behavior."""

    def __init__(
        self,
        *,
        url: str | None,
        path: str = "",
        label: str,
        path_resolver: Callable[[], list[str]] | None = None,
        validator: Callable[[Any], bool] | None = None,
    ):
        self._url = url
        self._path = path
        self._label = label
        self._path_resolver = path_resolver
        self._validator = validator
        self._etag: str | None = None
        self._last_modified_header: str | None = None
        self._content_hash: str | None = None
        self._last_load_time: float = 0.0
        self._last_refresh_error: str | None = None
        self._payload: ArtifactPayload | None = None
        self._loaded = False

    def load(self) -> ArtifactPayload | None:
        payload = self._load_local_payload() or self._fetch_remote_json()
        if payload is not None:
            self._payload = payload
            self._content_hash = payload.content_hash
            self._last_load_time = time.time()
            self._last_refresh_error = None
            self._loaded = True
            log_event(
                logger,
                "artifact_reload",
                label=self._label,
                source="local" if not payload.source.startswith("http") else "remote",
                content_hash=payload.content_hash,
                etag=payload.etag or "",
                last_modified=payload.last_modified or "",
                changed=True,
            )
        return payload

    def reload_if_changed(self) -> tuple[bool, str | None]:
        payload = self._load_local_payload()
        if payload is None:
            if not self._url:
                return False, None
            payload = self._fetch_remote_json(conditional=True)
            if payload is None:
                return False, self._last_refresh_error

        if payload.content_hash == self._content_hash:
            return False, None

        self._payload = payload
        self._content_hash = payload.content_hash
        self._last_load_time = time.time()
        self._last_refresh_error = None
        self._loaded = True
        log_event(
            logger,
            "artifact_reload",
            label=self._label,
            source="local" if not payload.source.startswith("http") else "remote",
            content_hash=payload.content_hash,
            etag=payload.etag or "",
            last_modified=payload.last_modified or "",
            changed=True,
        )
        return True, None

    def force_reload(self) -> tuple[bool, str | None]:
        """Unconditional reload — bypasses the ETag / Last-Modified check.

        Use sparingly: it defeats the ``If-None-Match`` polling
        optimisation, so we re-parse the payload even when the upstream
        artifact hasn't changed. Intended for the
        ``reload_semantic_registry`` admin tool used during semantic-
        layer authoring loops, where the 5-minute TTL or a stale-ETag
        upstream would otherwise leave the runtime out-of-sync with a
        just-published registry. Behaves like ``reload_if_changed`` when
        the local path is available (no HTTP at all).
        """
        payload = self._load_local_payload()
        if payload is None:
            if not self._url:
                return False, None
            payload = self._fetch_remote_json(conditional=False)
            if payload is None:
                return False, self._last_refresh_error

        changed = payload.content_hash != self._content_hash
        self._payload = payload
        self._content_hash = payload.content_hash
        self._last_load_time = time.time()
        self._last_refresh_error = None
        self._loaded = True
        log_event(
            logger,
            "artifact_force_reload",
            label=self._label,
            source="local" if not payload.source.startswith("http") else "remote",
            content_hash=payload.content_hash,
            etag=payload.etag or "",
            last_modified=payload.last_modified or "",
            changed=changed,
        )
        return changed, None

    def _candidate_paths(self) -> list[str]:
        if self._path_resolver is not None:
            paths = self._path_resolver()
        elif self._path:
            paths = [self._path]
        else:
            paths = []

        seen: set[str] = set()
        candidates: list[str] = []
        for path in paths:
            if not path:
                continue
            expanded = os.path.abspath(os.path.expanduser(path))
            if expanded not in seen:
                seen.add(expanded)
                candidates.append(expanded)
        return candidates

    def _load_local_payload(self) -> ArtifactPayload | None:
        for candidate in self._candidate_paths():
            if not os.path.exists(candidate):
                continue
            try:
                with open(candidate, "rb") as fh:
                    raw = fh.read()
                body = json.loads(raw)
                if self._validator is not None and not self._validator(body):
                    logger.warning(
                        "Ignoring local %s candidate %s because it does not match the expected structure.",
                        self._label,
                        candidate,
                    )
                    continue
                return ArtifactPayload(
                    body=body,
                    content_hash=self._hash_bytes(raw),
                    etag=None,
                    last_modified=None,
                    source=candidate,
                )
            except Exception as exc:
                logger.warning("Error loading local %s from %s: %s", self._label, candidate, exc)
        return None

    def _fetch_remote_json(self, conditional: bool = False) -> ArtifactPayload | None:
        if self._url:
            try:
                headers = {}
                timeout = 30 if not conditional else 5
                if conditional and self._etag:
                    headers["If-None-Match"] = self._etag
                if conditional and self._last_modified_header:
                    headers["If-Modified-Since"] = self._last_modified_header
                response = requests.get(self._url, timeout=timeout, headers=headers)
                if response.status_code == 304:
                    return None
                if response.status_code == 200:
                    self._etag = response.headers.get("ETag")
                    self._last_modified_header = response.headers.get("Last-Modified")
                    self._last_refresh_error = None
                    return ArtifactPayload(
                        body=response.json(),
                        content_hash=self._hash_bytes(response.content),
                        etag=self._etag,
                        last_modified=self._last_modified_header,
                        source=self._url,
                    )
                error = f"Failed to fetch {self._label}: HTTP {response.status_code}"
                if conditional:
                    self._last_refresh_error = error
                    return None
                logger.warning(error)
            except Exception as exc:
                error = f"Error fetching {self._label}: {exc}"
                if conditional:
                    self._last_refresh_error = error
                    return None
                logger.warning(error)

        logger.warning("No %s loaded.", self._label)
        return None

    @staticmethod
    def _hash_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @property
    def payload(self) -> ArtifactPayload | None:
        return self._payload

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def content_hash(self) -> str | None:
        return self._content_hash

    @property
    def last_load_time(self) -> float:
        return self._last_load_time

    @property
    def last_refresh_error(self) -> str | None:
        return self._last_refresh_error
