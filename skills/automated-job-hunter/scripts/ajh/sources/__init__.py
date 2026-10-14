"""Source adapters. Each returns a complete snapshot of a board or raises SourceError."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..http import Client
from ..model import Job, SourceError
from . import aggregators, ats

KINDS = (*ats.ATS, "aggregator")


def fetch(client: Client, source: Mapping[str, Any]) -> list[Job]:
    kind = source.get("ats")
    if kind in ats.ATS:
        return ats.fetch_board(client, source)
    if kind == "aggregator":
        return aggregators.fetch_aggregator(client, source)
    raise SourceError(f"Unknown source type {kind!r} for {source.get('id')!r}")
