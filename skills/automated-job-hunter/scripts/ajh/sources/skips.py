"""Rows of a listing that a reader left out, counted so that an update can show them.

A reader returns a complete snapshot or raises SourceError. The exception is a row that is plainly
no posting a person could open, such as a Workday row with neither a link nor a title. A reader
skips such a row and counts it here, and `collect` and `sources test` show the count by source, as
`collect` shows 429 retries (`Client.tally`).
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

_local = threading.local()  # the counts open in each thread (`counting`)


@contextmanager
def counting() -> Iterator[dict[str, int]]:
    """Count, by reason, the rows readers skip in this thread until the block ends.

    `collect` reads each source in one thread of its pool, start to end, so the count is that
    source's alone.
    """
    counts: dict[str, int] = {}
    if not hasattr(_local, "open"):
        _local.open = []
    _local.open.append(counts)
    try:
        yield counts
    finally:
        _local.open.pop()


def add(reason: str, rows: int = 1) -> None:
    """Count `rows` rows of a listing that a reader skipped for `reason`, in this thread's open counts."""
    for counts in getattr(_local, "open", ()):
        counts[reason] = counts.get(reason, 0) + rows
