"""Where things live inside a feed repo."""

from __future__ import annotations

from pathlib import Path


class Layout:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.criteria = self.root / "profile" / "criteria.json"
        self.profile = self.root / "profile" / "profile.md"
        self.sources = self.root / "config" / "sources.json"
        self.state = self.root / "state"
        # The published feed.
        self.readme = self.root / "README.md"
        self.positions = self.root / "positions.jsonl"
        self.feed = self.root / "feed"
        self.feed_meta = self.feed / "meta.json"
        self.latest = self.feed / "latest.md"
        # Untracked scratch space. Everything a tick produces before `apply` lives here.
        self.work = self.root / ".hunter" / "work"
        self.snapshot = self.work / "snapshot"
        self.state_next = self.work / "state-next"
        self.plan = self.work / "plan.json"
        self.seal = self.work / "seal"
        self.packets = self.work / "packets"
        self.packet_index = self.packets / "index.json"
        self.manifest = self.work / "manifest.md"  # what the judging orchestrator reads
        self.results = self.work / "results"  # the only place a judging subagent may write
