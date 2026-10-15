"""Shared test scaffolding: fake job boards and a throwaway feed repo."""

import json
import random
import re
import shutil
from datetime import datetime, timezone

from ajh import collect
from ajh.http import Client
from ajh.layout import Layout
from ajh.state import read_rows, rows_text
from ajh.textutil import html_to_text, simhash64

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
ACME = {"id": "acme", "company": "Acme", "ats": "greenhouse", "slug": "acme"}
GLOBEX = {"id": "globex", "company": "Globex", "ats": "greenhouse", "slug": "globex"}
REMOTIVE = {"id": "remotive", "ats": "aggregator", "provider": "remotive"}
INITECH = {
    "id": "initech",
    "company": "Initech",
    "ats": "workday",
    "host": "initech.wd1.myworkdayjobs.com",
    "site": "External",
}
CRITERIA = {"schema_version": 1, "targets": {"roles": ["UX Researcher"]}}


def prose(seed, words=90):
    rng = random.Random(seed)
    vocab = (
        "research design product team users data analysis collaborate lead build insights strategy qualitative "
        "quantitative methods stakeholders roadmap interviews surveys experiments metrics impact synthesis"
    ).split()
    return " ".join(rng.choice(vocab) for _ in range(words))


def posting(jid, title="UX Researcher", location="Remote, US", text=None, published="2026-10-10T00:00:00Z"):
    return {
        "id": jid,
        "title": title,
        "absolute_url": f"https://job-boards.greenhouse.io/x/jobs/{jid}",
        "content": f"<p>{text or prose(jid)}</p>",
        "location": {"name": location},
        "first_published": published,
    }


def wd_posting(jid, title="UX Researcher", places=("US, CA, San Jose",), text=None, category="Research"):
    """A posting on the fake Workday site: its listing row, and the text its detail returns."""
    where = places[0] if len(places) == 1 else f"{len(places)} Locations"
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")
    return {
        "row": {
            "title": title,
            "externalPath": f"/job/{re.sub(r'[^A-Za-z0-9]+', '-', places[0]).strip('-')}/{slug}_{jid}",
            "locationsText": where,
            "postedOn": "Posted Today",
            "bulletFields": [jid],
        },
        "places": list(places),
        "text": text or prose(sum(map(ord, jid))),
        "category": category,
    }


class Boards:
    """Fake job boards. Tests change `jobs` and `status` between ticks.

    `workday` holds the fake Workday site's postings by id. Its search matches a title containing
    the query, within the job categories asked for, and `workday_down` lists ids whose detail
    request fails.
    """

    def __init__(self, **boards):
        self.jobs = {slug: {p["id"]: p for p in postings} for slug, postings in boards.items()}
        self.status, self.remotive, self.requests = {}, [], []
        self.workday, self.workday_down = {}, set()

    def _workday(self, method, url, body):
        if method == "POST":
            ask = json.loads(body)
            self.requests.append(("search", ask["searchText"]))
            wanted = set(ask["appliedFacets"].get("jobFamilyGroup", []))
            matched = [p for p in self.workday.values() if ask["searchText"].lower() in p["row"]["title"].lower()]
            rows = [p["row"] for p in matched if not wanted or f"cat-{p['category']}" in wanted]
            page = rows[ask["offset"] : ask["offset"] + ask["limit"]]
            counts = {}
            for p in matched:
                counts[p["category"]] = counts.get(p["category"], 0) + 1
            values = [{"descriptor": name, "id": f"cat-{name}", "count": n} for name, n in sorted(counts.items())]
            facets = [{"facetParameter": "jobFamilyGroup", "descriptor": "Job Category", "values": values}]
            listing = {"total": len(rows) if ask["offset"] == 0 else 0, "jobPostings": page, "facets": facets}
            return 200, {}, json.dumps(listing).encode()
        jid = url.rsplit("_", 1)[-1]
        self.requests.append(("detail", jid))
        if jid in self.workday_down or jid not in self.workday:
            return 404, {}, b"{}"
        posting = self.workday[jid]
        info = {
            "title": posting["row"]["title"],
            "jobDescription": f"<p>{posting['text']}</p>",
            "location": posting["places"][0],
            "additionalLocations": posting["places"][1:],
            "startDate": "2026-10-10",
            "timeType": "Full time",
            "jobReqId": jid,
            "canApply": True,
            "posted": True,
        }
        return 200, {}, json.dumps({"jobPostingInfo": info}).encode()

    def details(self):
        """The ids whose text was asked for since the last call."""
        asked = [jid for kind, jid in (r for r in self.requests if isinstance(r, tuple)) if kind == "detail"]
        self.requests = [r for r in self.requests if not isinstance(r, tuple)]
        return asked

    def transport(self, method, url, headers, body, timeout):
        if ".myworkdayjobs.com/wday/cxs/" in url or ".myworkdaysite.com/wday/cxs/" in url:
            return self._workday(method, url, body)
        if "remotive.com" in url:
            self.requests.append("remotive")
            return 200, {}, json.dumps({"job-count": len(self.remotive), "jobs": self.remotive}).encode()
        slug = url.split("/boards/")[1].split("/")[0]
        self.requests.append(slug)
        if slug in self.status:
            return self.status[slug], {}, b"{}"
        return 200, {}, json.dumps({"jobs": list(self.jobs[slug].values())}).encode()


def tick(root, boards, now=NOW, adopt=True):
    """One collect. With `adopt`, the proposed state becomes the tracked state, as `apply` would do."""
    summary = collect.run(root, Client(boards.transport, sleep=lambda seconds: None, retries=0), now)
    if adopt:
        adopt_state(root)
    return summary


def adopt_state(root):
    """Make the proposed state the tracked state, as `apply` does."""
    layout = Layout(root)
    if layout.state.exists():
        shutil.rmtree(layout.state)
    shutil.copytree(layout.state_next, layout.state)


def rows(root, source="acme"):
    return read_rows(Layout(root).state / "jobs" / f"{source}.tsv")


def judge(root, source, jid, status, **changes):
    """Stand in for a verdict: set a row the way `apply` would after judging."""
    path = Layout(root).state / "jobs" / f"{source}.tsv"
    table = read_rows(path)
    row = table[jid]
    row.status, row.tg, row.ag = status, 1, 1 if status in "ARU" else 0
    for name, value in changes.items():
        setattr(row, name, value)
    path.write_text(rows_text(table))


def frozen_hash(post):
    return f"{simhash64(html_to_text(post['content'])):016x}"


def plan(root):
    return json.loads(Layout(root).plan.read_text())


def tracked(root):
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and ".hunter" not in p.relative_to(root).parts
    }


def answer_triage(root, verdict_for=lambda job: "keep"):
    """Write triage result files the way a subagent would. `verdict_for` gets the posting's facts."""
    layout = Layout(root)
    for packet, entry in json.loads(layout.packet_index.read_text()).items():
        if entry["stage"] != "triage":
            continue
        lines = [f"packet {packet} nonce {entry['nonce']}"]
        for local, key in entry["items"].items():
            source, jid = key.split(":")
            lines.append(f"{local} {verdict_for(json.loads((layout.snapshot / f'{source}.json').read_text())[jid])}")
        layout.results.mkdir(parents=True, exist_ok=True)
        (layout.results / f"{packet}.txt").write_text("\n".join([*lines, f"end {len(lines) - 1}"]) + "\n")


def good_assessment(job, fit=82, role_match="direct"):
    """A valid assessment whose quote is the first eight words of the posting."""
    quote = " ".join(job["description"].split()[:8])
    return {
        "fit": fit,
        "role_match": role_match,
        "seniority": "mid",
        "why": "Matches your research work.",
        "match": f"Core duties || {quote}",
    }


def answer_assess(root, assess_for=good_assessment):
    """Write assessment result files. `assess_for` returns the fields for a posting, or None to skip it.

    A field whose value is a list is written as one line per item, as repeated lines are.
    """
    layout = Layout(root)
    for packet, entry in json.loads(layout.packet_index.read_text()).items():
        if entry["stage"] != "assess":
            continue
        lines = [f"packet {packet} nonce {entry['nonce']}"]
        for local, key in entry["items"].items():
            source, jid = key.split(":")
            fields = assess_for(json.loads((layout.snapshot / f"{source}.json").read_text())[jid])
            if fields is not None:
                lines.append(f"## {local}")
                for name, value in fields.items():
                    lines += [f"{name}: {item}" for item in (value if isinstance(value, list) else [value])]
        layout.results.mkdir(parents=True, exist_ok=True)
        (layout.results / f"{packet}.txt").write_text("\n".join([*lines, f"end {len(entry['items'])}"]) + "\n")


def full_tick(root, boards, now=NOW, triage=lambda job: "keep", assess=good_assessment):
    """collect, judge, advance, judge, apply: one whole tick with scripted judges."""
    from ajh import advance, apply

    summary = collect.run(root, Client(boards.transport, sleep=lambda seconds: None, retries=0), now)
    answer_triage(root, triage)
    moved = advance.run(root, summary["seal"])
    answer_assess(root, assess)
    return apply.run(root, moved["seal"])
