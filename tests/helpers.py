"""Shared test scaffolding: fake job boards and a throwaway feed repo."""
import json
import random
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
CRITERIA = {"schema_version": 1, "targets": {"roles": ["UX Researcher"]}}


def prose(seed, words=90):
    rng = random.Random(seed)
    vocab = ("research design product team users data analysis collaborate lead build insights strategy qualitative "
             "quantitative methods stakeholders roadmap interviews surveys experiments metrics impact synthesis").split()
    return " ".join(rng.choice(vocab) for _ in range(words))


def posting(jid, title="UX Researcher", location="Remote, US", text=None, published="2026-10-10T00:00:00Z"):
    return {"id": jid, "title": title, "absolute_url": f"https://job-boards.greenhouse.io/x/jobs/{jid}",
            "content": f"<p>{text or prose(jid)}</p>", "location": {"name": location}, "first_published": published}


class Boards:
    """Fake job boards. Tests change `jobs` and `status` between ticks."""

    def __init__(self, **boards):
        self.jobs = {slug: {p["id"]: p for p in postings} for slug, postings in boards.items()}
        self.status, self.remotive, self.requests = {}, [], []

    def transport(self, method, url, headers, body, timeout):
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
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and ".hunter" not in p.relative_to(root).parts}
