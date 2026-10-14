"""Choosing sources: the bundled list of known boards, and probing for a company's board."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import sources as S
from .http import Client
from .layout import Layout
from .sources import ats
from .sources.aggregators import PROVIDERS
from .state import read_json, write_text


def bundled() -> dict[str, dict[str, Any]]:
    """Known public boards and feeds by id."""
    data = json.loads((Path(__file__).with_name("data") / "registry.json").read_text(encoding="utf-8"))
    return {entry["id"]: entry for entry in [*data["boards"], *data["feeds"]]}


def listing() -> dict[str, Any]:
    known = bundled()
    return {
        "boards": [{"id": e["id"], "company": e["company"], "ats": e["ats"]} for e in known.values() if "company" in e],
        "feeds": [e["id"] for e in known.values() if e["ats"] == "aggregator"],
    }


def _write(layout: Layout, entries: list[dict[str, Any]]) -> None:
    body = ",\n".join(" " + json.dumps(entry, ensure_ascii=False) for entry in sorted(entries, key=lambda e: e["id"]))
    write_text(layout.sources, f"[\n{body}\n]\n" if entries else "[]\n")


def add(root: Path | str, items: list[str]) -> dict[str, Any]:
    """Add sources to config/sources.json: registry ids, or `ats:slug:Company Name` for a board found by probing."""
    layout = Layout(root)
    current = {entry["id"]: entry for entry in read_json(layout.sources, [])}
    known = bundled()
    added, unknown = [], []
    for item in items:
        parts = item.split(":", 2)
        if len(parts) == 3 and parts[0] in ats.ATS:
            kind, slug, company = parts
            entry = {
                "id": re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-"),
                "company": company,
                "ats": kind,
                "slug": slug,
            }
        elif item in known:
            entry = dict(known[item])
        else:
            unknown.append(item)
            continue
        ats.feed_url(entry) if entry["ats"] in ats.ATS else None  # rejects a slug that is not a plain token
        if entry["id"] not in current:
            added.append(entry["id"])
        current[entry["id"]] = entry
    _write(layout, list(current.values()))
    return {"added": added, "unknown": unknown, "configured": len(current)}


def remove(root: Path | str, ids: list[str]) -> dict[str, Any]:
    layout = Layout(root)
    current = [entry for entry in read_json(layout.sources, [])]
    kept = [entry for entry in current if entry["id"] not in ids]
    _write(layout, kept)
    return {"removed": len(current) - len(kept), "configured": len(kept)}


def slug_candidates(company: str) -> list[str]:
    """Board slugs a company plausibly uses: its name run together, hyphenated, and its first word."""
    words = re.findall(r"[a-z0-9]+", company.lower())
    words = [w for w in words if w not in ("inc", "llc", "ltd", "corp", "co", "the")] or words
    forms = ["".join(words), "-".join(words), words[0] if words else ""]
    return list(dict.fromkeys(form for form in forms if form))


def probe(company: str, client: Client | None = None) -> dict[str, Any]:
    """Try a company's likely slugs on each board type. A match lists how many postings the board has."""
    client = client or Client(retries=0, timeout=20)
    matches = []
    for slug in slug_candidates(company):
        for kind in ats.ATS:
            source = {"id": "probe", "company": company, "ats": kind, "slug": slug}
            try:
                jobs = ats.fetch_board(client, source)
            except Exception:  # not there, blocked, or not a board: simply not a match
                continue
            matches.append(
                {
                    "ats": kind,
                    "slug": slug,
                    "postings": len(jobs),
                    "sample": [job.title for job in jobs[:3]],
                    "add": f"{kind}:{slug}:{company}",
                }
            )
    return {"company": company, "tried": slug_candidates(company), "matches": matches}


def hosts(root: Path | str) -> dict[str, Any]:
    """The hosts a tick contacts, for a network allowlist."""
    found = set()
    for source in read_json(Layout(root).sources, []):
        if source["ats"] in ats.ATS:
            found.add(urlsplit(ats.feed_url(source)).hostname)
        elif source.get("provider") in PROVIDERS:
            found.add(urlsplit(PROVIDERS[source["provider"]][2]).hostname)
    return {"hosts": sorted(found)}


def test(root: Path | str, client: Client | None = None) -> dict[str, Any]:
    """Read every configured source once and report what each returned. Changes nothing."""
    client = client or Client(retries=0)
    report = {}
    for source in read_json(Layout(root).sources, []):
        try:
            report[source["id"]] = {"postings": len(S.fetch(client, source))}
        except Exception as error:
            report[source["id"]] = {"error": f"{type(error).__name__}: {str(error)[:160]}"}
    failing = sorted(sid for sid, result in report.items() if "error" in result)
    return {"sources": report, "failing": failing}
