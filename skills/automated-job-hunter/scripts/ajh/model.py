"""The normalized job record and the identity helpers shared by every module."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, fields
from typing import Any
from urllib.parse import urlparse

SOURCE_KINDS = ("official", "aggregator")


class SourceError(ValueError):
    """A source could not produce a complete, trustworthy snapshot.

    Adapters raise this instead of returning a partial board, so an outage or a format change
    keeps the previous snapshot in force and never closes live jobs.
    """


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def public_https(url: Any) -> bool:
    if not isinstance(url, str):
        return False
    try:
        p = urlparse(url)
    except ValueError:
        return False
    return (
        p.scheme == "https"
        and bool(p.hostname)
        and not p.username
        and not p.password
        and p.hostname not in {"localhost", "127.0.0.1", "::1"}
    )


_PATH_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def path_id(source_id: str, limit: int = 80) -> str:
    """File-name-safe form of a source id.

    Ids that had to be altered or shortened get a hash suffix, so two distinct ids never map to
    the same file name.
    """
    safe = _PATH_UNSAFE.sub("-", source_id).strip("-.")
    if safe == source_id and 0 < len(safe) <= limit:
        return safe
    return (safe[: limit - 9] or "id") + "-" + hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:8]


@dataclass(frozen=True)
class Job:
    key: str  # "<source>:<source_id>", the stable identity
    source: str
    source_id: str
    company: str
    title: str
    location: str
    url: str
    description: str = ""  # plain text; empty for listing-only sources until the detail is fetched
    posted_at: str | None = None  # the source's own value, unparsed
    employment_type: str = "unknown"
    department: str = ""
    source_kind: str = "official"
    source_label: str | None = None
    apply_url: str | None = None
    # The source has this posting's text, but it was not fetched this update (see `sources.fetch`),
    # so nothing judges it or rewrites its brief until it is.
    detail_skipped: bool = False

    def __post_init__(self) -> None:
        if not self.source or not self.source_id or self.key != f"{self.source}:{self.source_id}":
            raise SourceError(f"Inconsistent job identity: {self.key!r}")
        if not self.source_id.isprintable() or any(c.isspace() for c in self.source_id):
            # ids are stored in tab-separated state files
            raise SourceError(f"Job id {self.source_id!r} contains whitespace or control characters")
        if not isinstance(self.title, str) or not self.title.strip():
            raise SourceError(f"Job {self.key} has no title")
        if not public_https(self.url):
            raise SourceError(f"Job {self.key} has no public https URL")
        if self.source_kind not in SOURCE_KINDS:
            raise SourceError(f"Job {self.key} has unknown source kind {self.source_kind!r}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Job:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})
