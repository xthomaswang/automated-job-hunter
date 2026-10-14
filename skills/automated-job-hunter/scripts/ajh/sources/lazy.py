"""For detail sources: which listed postings get their text this update (see `sources`)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any, TypeVar

from ..model import Job

Extra = TypeVar("Extra")  # what the adapter needs to fetch one posting's text


def budget(source: Mapping[str, Any], default: int) -> int:
    """How many texts a source may fetch per update: its own `max_details`, or the adapter's default."""
    value = source.get("max_details", default)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else default


def complete(
    listed: Mapping[str, tuple[Job, Extra]],
    rank: Callable[[Job], int | None] | None,
    new: int,
    limit: int,
    detail: Callable[[Job, Extra], Job | None],
) -> list[Job]:
    """The postings to return, with text where it was asked for and fetched.

    Texts are fetched in rank order and, within a rank, newest id first, so postings whose text
    keeps failing sink to the end instead of using the budget every update. A known posting
    without its text comes back `detail_skipped`; a new one is left out until it has its text.
    Without `rank` (counting only), nothing is fetched.
    """
    ranks = {jid: rank(job) for jid, (job, _) in listed.items()} if rank else {}
    wanted = sorted(sorted(((r, jid) for jid, r in ranks.items() if r is not None), reverse=True), key=lambda w: w[0])
    detailed: dict[str, Job] = {}
    for _, jid in wanted[:limit]:
        job, extra = listed[jid]
        found = detail(job, extra)
        if found is not None:
            detailed[jid] = found
    jobs = []
    for jid, (job, _) in listed.items():
        if jid in detailed:
            jobs.append(detailed[jid])
        elif ranks.get(jid) != new:  # a new posting waits for its text; nothing knew it, so nothing closes
            jobs.append(replace(job, detail_skipped=True))
    return jobs
