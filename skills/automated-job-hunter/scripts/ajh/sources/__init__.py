"""Source adapters. Each returns a complete snapshot of a board or raises SourceError.

Most boards return every posting with its text in one response. A detail source (Workday,
SmartRecruiters, a GitHub job list) lists postings without their text and needs one more request
per posting for it. Workday and SmartRecruiters are read through searches; a list names its
postings itself. Their text is fetched only where `collect` needs it. `collect` passes a rank
function for that: it returns None for a posting whose text would not be used this update, or
one of the ranks below, and the adapter fetches text in rank order within its per-update budget.
A posting it did not fetch text for comes back with `detail_skipped`. `collect` keeps a new one out
of the state until an update brings its text (it was never known, so nothing closes) and counts it
as waiting for its text.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Optional

from ..http import Client
from ..model import Job, SourceError
from . import aggregators, ats, eightfold, lists, own, smartrecruiters, successfactors, workday

KINDS = (*ats.ATS, "aggregator", *workday.KINDS, *smartrecruiters.KINDS, *lists.KINDS, *successfactors.KINDS)
KINDS += eightfold.KINDS
QUERY_SCOPED = (
    *workday.KINDS,
    *smartrecruiters.KINDS,
    *eightfold.KINDS,
    *successfactors.KINDS,
)  # read through their own search, by default for the wanted roles
DETAILED = (*QUERY_SCOPED, *lists.KINDS)  # list postings without their text; see `fetch`

# Text first for a posting about to be judged, then for a new one, then for one whose listing changed
# (it is judged again once its text comes), and last for a listed one, which needs it only to rewrite
# its brief and otherwise keeps the last one. So listed postings can never crowd new ones out.
PENDING, NEW, CHANGED, LISTED = 0, 1, 2, 3
Rank = Callable[[Job], Optional[int]]


def load_own(root: Path | str) -> own.Loaded:
    """Load the feed's own readers from its `readers/` folder, once per process (see `own`).

    Only the commands that read sources call this. Until then, and while postings are judged, no
    code from the feed runs.
    """
    return own.load(root, KINDS)


def known(kind: Any) -> bool:
    """A type a source may name: a built-in one, or the feed's own readers'. The type of a module
    that failed to load counts too: each of its sources then fails, with the reason."""
    return kind in KINDS or own.current.claims(kind)


def query_scoped(kind: Any) -> bool:
    """Read through its own search, by default for the wanted and related roles (see `scoped`)."""
    return kind in QUERY_SCOPED or own.current.query_scoped(kind)


def detailed(kind: Any) -> bool:
    """Lists postings without their text, so `collect` passes a rank function (see `fetch`)."""
    return kind in DETAILED or own.current.detailed(kind)


def scoped(source: Mapping[str, Any], criteria: Mapping[str, Any]) -> Mapping[str, Any]:
    """A searched source with its queries: its own, or else the wanted and related roles."""
    if not query_scoped(source.get("ats")) or source.get("queries"):
        return source
    targets = criteria["targets"]
    return {**source, "queries": list(dict.fromkeys([*targets["roles"], *targets["adjacent"]]))}


def fetch(client: Client, source: Mapping[str, Any], rank: Rank | None = None) -> list[Job]:
    """A board's postings. Without `rank`, a detail source fetches no text (enough to count them)."""
    kind = source.get("ats")
    if own.current.claims(kind):  # the feed's own readers, and the types they declared but cannot read
        return own.current.fetch(client, source, rank)
    if kind in ats.ATS:
        return ats.fetch_board(client, source)
    if kind == "aggregator":
        return aggregators.fetch_aggregator(client, source)
    if kind in workday.KINDS:
        return workday.fetch(client, source, rank)
    if kind in smartrecruiters.KINDS:
        return smartrecruiters.fetch(client, source, rank)
    if kind in lists.KINDS:
        return lists.fetch(client, source, rank)
    if kind in eightfold.KINDS:
        return eightfold.fetch(client, source, rank)
    if kind in successfactors.KINDS:
        return successfactors.fetch(client, source, rank)
    raise SourceError(f"Unknown source type {kind!r} for {source.get('id')!r}")
