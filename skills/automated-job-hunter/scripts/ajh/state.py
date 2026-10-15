"""Committed engine state: small, sorted text files that survive a fresh clone.

Nothing here carries a per-run timestamp, so a tick that changes nothing leaves every file
byte-identical. Posting text is never stored; only what is needed to recognize a posting again.

Row status:
  P  pending triage          D  dropped at triage        K  kept, pending assessment
  A  assessed and published  R  assessed, below the publishing threshold
  U  published unscored after repeated invalid verdicts
  V  variant of another posting (`note` holds its key), never judged on its own
  S  parked: older than the backfill window when its source was first read, or (with a `place` tag)
     put outside the wanted regions by the posting's own record, which the listing did not show
  X  tombstone: gone from its source (`note` holds "<previous status>@<day closed>")
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__

# 2: a `blk` tag holds several facts (`years=7+seniority=senior`), which an engine reading 1 misreads.
SCHEMA_VERSION = 2
STATUSES = "PDKARUVSX"
COLUMNS = ("id", "status", "first_seen", "gk", "tfp", "dsh", "fit", "tg", "ag", "attempts", "miss", "note")
SOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
_INT_COLUMNS = ("tg", "ag", "attempts", "miss")


class StateError(ValueError):
    """A state file is unreadable. Nothing is guessed; a person has to look."""


@dataclass
class Row:
    id: str  # the posting's id within its source
    status: str
    first_seen: str  # day this posting first passed stage 0
    gk: str  # hash of company and title: postings that may be the same opening
    tfp: str  # hash of title, company and location
    dsh: str = "-"  # simhash of the description, frozen when the posting was judged
    fit: str = "-"
    tg: int = 0  # triage generation of the current verdict; 0 means none
    ag: int = 0  # assess generation of the current verdict; 0 means none
    attempts: int = 0  # invalid verdicts so far in the current stage
    miss: int = 0  # consecutive snapshots this posting was absent from
    note: str = "-"

    def line(self) -> str:
        return "\t".join(str(getattr(self, name)) for name in COLUMNS)


def tag(row: Row, name: str) -> str | None:
    """A tag from a pending row's note, which holds `;`-separated `name` or `name:value` entries.

    Tags: `was:<status>` (a published posting being re-judged), `fb:<codes>` (why the last answer was
    rejected), `canary` (a triage drop sent to assessment to measure what triage misses),
    `blk:<facts>` (left out by a hard requirement; the facts it was checked on, see `feed.hard_limit`),
    `unmet:<kinds>` (an assessed posting's unmet requirements, listed or not), `place:<countries>`
    (parked: the countries the posting's own record names, none of them wanted; see `collect.unpark`).
    """
    for part in row.note.split(";"):
        key, _, value = part.partition(":")
        if key == name:
            return value
    return None


def set_tag(row: Row, name: str, value: str | None) -> None:
    """Set a tag, or remove it when `value` is None."""
    parts = [p for p in row.note.split(";") if p and p != "-" and p.partition(":")[0] != name]
    if value is not None:
        parts.append(f"{name}:{value}" if value else name)
    row.note = ";".join(sorted(parts)) or "-"


def write_text(path: Path, text: str) -> None:
    """Write through a temporary file so a crash never leaves a half-written state file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def dump_json(value: Any) -> str:
    return json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except ValueError as error:
        raise StateError(f"{path} is not valid JSON: {error}") from None


def read_rows(path: Path) -> dict[str, Row]:
    try:
        lines = path.read_text(encoding="utf-8").split("\n")
    except FileNotFoundError:
        return {}
    if lines[0].split("\t") != list(COLUMNS):
        raise StateError(f"{path} has an unexpected header")
    rows: dict[str, Row] = {}
    for number, line in enumerate(lines[1:], 2):
        if not line:
            continue
        cells = line.split("\t")
        if len(cells) != len(COLUMNS):
            raise StateError(f"{path}:{number} has {len(cells)} columns, expected {len(COLUMNS)}")
        values: dict[str, Any] = dict(zip(COLUMNS, cells))
        try:
            for name in _INT_COLUMNS:
                values[name] = int(values[name])
        except ValueError:
            raise StateError(f"{path}:{number} has a non-numeric counter") from None
        if values["status"] not in STATUSES or values["id"] in rows:
            raise StateError(f"{path}:{number} has an unknown status or a repeated id")
        rows[values["id"]] = Row(**values)
    return rows


def rows_text(rows: dict[str, Row]) -> str:
    return "\n".join(["\t".join(COLUMNS), *(rows[key].line() for key in sorted(rows))]) + "\n"


class State:
    """All state under one directory. Reads are lazy; `save` writes a complete copy elsewhere."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.meta: dict[str, Any] = read_json(self.root / "meta.json", {})
        version = self.meta.get("schema_version")
        if self.meta and not (isinstance(version, int) and 1 <= version <= SCHEMA_VERSION):
            raise StateError(
                f"The feed's state is schema {version!r}, written by engine {self.meta.get('engine_version')!r}; "
                f"this engine ({__version__}) reads up to schema {SCHEMA_VERSION}. Run `hunter upgrade` from a newer "
                "copy of the skill."
            )
        self.sources: dict[str, dict[str, Any]] = read_json(self.root / "sources.json", {})
        self.profile: dict[str, Any] = read_json(self.root / "profile.json", {})
        self._rows: dict[str, dict[str, Row]] = {}
        self._titles: dict[str, int] | None = None

    def source_ids(self) -> list[str]:
        """Every source that has rows on disk or was loaded, whether or not it is still configured."""
        folder = self.root / "jobs"
        on_disk = {p.stem for p in folder.glob("*.tsv")} if folder.is_dir() else set()
        return sorted(on_disk | set(self._rows))

    def rows(self, source: str) -> dict[str, Row]:
        if not SOURCE_ID.match(source):
            raise StateError(f"Unsafe source id {source!r}")
        if source not in self._rows:
            self._rows[source] = read_rows(self.root / "jobs" / f"{source}.tsv")
        return self._rows[source]

    @property
    def titles(self) -> dict[str, int]:
        """Title keys judged unrelated whatever the duties, with the triage generation that said so."""
        if self._titles is None:
            self._titles = {}
            path = self.root / "titles.tsv"
            if path.exists():
                for line in path.read_text(encoding="utf-8").split("\n"):
                    if line:
                        key, _, generation = line.rpartition("\t")
                        self._titles[key] = int(generation)
        return self._titles

    def save(self, root: Path, as_of: str) -> None:
        """Write the whole state to `root`. Sources with no rows left get no file."""
        root = Path(root)
        for source in self.source_ids():
            rows = self.rows(source)
            if rows:
                write_text(root / "jobs" / f"{source}.tsv", rows_text(rows))
        write_text(root / "titles.tsv", "".join(f"{key}\t{gen}\n" for key, gen in sorted(self.titles.items())))
        write_text(root / "sources.json", dump_json(self.sources))
        write_text(root / "profile.json", dump_json(self.profile))
        write_text(
            root / "meta.json",
            dump_json({"schema_version": SCHEMA_VERSION, "engine_version": __version__, "as_of": as_of}),
        )
