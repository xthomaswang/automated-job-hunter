"""Community job lists kept in public GitHub repos, read as pointers to employers' own postings.

A list is one file in a public repo: JSON rows, or Markdown tables. A row names a company, a role
and a link. Only a row whose link is one posting on a system this engine reads through a public
API is used (Greenhouse, Lever, Ashby, SmartRecruiters, Workday): its text, date and link then come
from that system, never from the list, so each posting is the employer's own. A row linking
anywhere else (a site that re-posts jobs, a redirect, a system with no public API, an employer's
own page) is skipped, as is a row the source's `filters` leave out; `sources test` counts them.

A posting's id is where it lives, `<system>~<board>~<id>` (a Workday board is `<tenant>~<site>`),
so two lists naming one posting, or a list naming a posting on a board the feed watches, are known
to be one opening from the ids alone (see `collect.assign_variants`). Workday postings are compared
by tenant and requisition (`identity`), so one requisition on two sites of a tenant is one opening.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

from ..http import Client, HttpError
from ..model import Job, SourceError, public_https
from . import ats, lazy, smartrecruiters, workday

KINDS = ("github-list",)
RAW = "raw.githubusercontent.com"
GREENHOUSE, LEVER, ASHBY = "boards-api.greenhouse.io", "api.lever.co", "api.ashbyhq.com"
# Every host a list can lead to, for a network allowlist. Workday sites are each on their own host.
HOSTS = (RAW, GREENHOUSE, LEVER, ASHBY, smartrecruiters.HOST, "*.myworkdayjobs.com", "*.myworkdaysite.com")
SYSTEMS = ("greenhouse", "lever", "ashby", "smartrecruiters", "workday")
DETAILS = 50  # postings whose text one list fetches per update, unless the source sets max_details
DELAY = 0.5  # seconds between requests to one system's API

# Links never followed, by host or parent domain: sites that re-post other employers' jobs, and
# redirects to them. Following one would be reading the job from a re-poster.
REPOSTERS = frozenset(
    "linkedin.com indeed.com glassdoor.com ziprecruiter.com monster.com dice.com talent.com jooble.org bebee.com "
    "jobright.ai zapply.jobs simplify.jobs welcometothejungle.com wellfound.com builtin.com himalayas.app "
    "jobicy.com remoteok.com remotive.com weworkremotely.com ec.europa.eu".split()
)
# Systems whose postings this engine has no public API for, and Google's careers pages and searches.
NO_READER = frozenset(
    "icims.com successfactors.com successfactors.eu sapsf.com oraclecloud.com taleo.net eightfold.ai amazon.jobs "
    "jobvite.com workable.com bamboohr.com recruitee.com paylocity.com ultipro.com adp.com google.com".split()
)
READ = ("greenhouse.io", "lever.co", "ashbyhq.com", "smartrecruiters.com", "myworkdayjobs.com", "myworkdaysite.com")
# What a table's column headings may be called, for each fact a row gives.
HEADINGS = {
    "company": ("company", "employer", "organization"),
    "title": ("role", "title", "position", "job title", "job"),
    "location": ("location", "locations"),
    "link": ("application", "apply", "link", "url", "posting"),
}
FIELDS = {"company": "company", "title": "title", "location": "location", "link": "url"}  # JSON field names
_REPO = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+\Z")
_REF = re.compile(r"[A-Za-z0-9._/-]+\Z")
_FILE = re.compile(r"[A-Za-z0-9._/ -]+\Z")
_BOARD = re.compile(r"[A-Za-z0-9._%-]+\Z")
_UUID = re.compile(r"[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
_LANGUAGE = re.compile(r"[a-z]{2}(-[A-Za-z]{2})?\Z")  # as in /en-US/<site>/job/... on Workday
_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)
_PICTURES = {"️", "︎", "‍", "⃣"}  # emoji presentation, joiners and keycaps


@dataclass(frozen=True)
class Link:
    """One posting on a system this engine reads: where it lives, and what fetching it takes."""

    system: str
    board: str  # a Greenhouse, Lever or Ashby board, a SmartRecruiters company, or Workday's `<tenant>/<site>`
    id: str
    url: str  # the posting's own page, without tracking parameters
    host: str = ""  # Workday: the site's host
    path: str = ""  # Workday: the posting's path after the site, as the site's API takes it

    @property
    def source_id(self) -> str:
        return "~".join([self.system, *self.board.split("/"), self.id])


def link(url: Any) -> Link | None:
    """The posting a link points at, when it is one posting on a system this engine reads."""
    if not public_https(url):
        return None
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    segments = [s for s in parts.path.split("/") if s]
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io"):
        query = parse_qs(parts.query)
        if len(segments) >= 3 and segments[1] == "jobs":
            board, pid = segments[0], segments[2]
        elif segments[:2] == ["embed", "job_app"] and query.get("for") and query.get("token"):
            board, pid = query["for"][0], query["token"][0]
        else:
            return None
        if _BOARD.match(board) and pid.isdigit():
            return Link("greenhouse", board, pid, f"https://job-boards.greenhouse.io/{board}/jobs/{pid}")
    elif host in ("jobs.lever.co", "jobs.ashbyhq.com") and len(segments) >= 2 and _UUID.match(segments[1]):
        system = "lever" if host == "jobs.lever.co" else "ashby"
        board, pid = segments[0], segments[1].lower()
        if _BOARD.match(board):
            return Link(system, board, pid, f"https://{host}/{board}/{pid}")
    elif host == "jobs.smartrecruiters.com" and len(segments) >= 2:
        found = re.match(r"\d+", segments[1])
        if found and smartrecruiters._SLUG.match(segments[0]):
            board, pid = segments[0], found.group(0)
            return Link("smartrecruiters", board, pid, f"https://jobs.smartrecruiters.com/{board}/{pid}")
    else:
        own, shared = workday.HOST.match(host), workday.SHARED_HOST.match(host)
        rest = segments
        if shared and len(segments) >= 2 and segments[0] == "recruiting":
            tenant, rest = segments[1], segments[2:]
        elif own:
            tenant = own.group(1)
        else:
            return None
        if rest and _LANGUAGE.match(rest[0]) and len(rest) > 1 and rest[1] != "job":
            rest = rest[1:]
        while rest and rest[-1].lower() in ("apply", "application", "login"):
            rest = rest[:-1]
        found = workday._ID.search(rest[-1]) if len(rest) >= 3 and rest[1] == "job" else None
        if found and workday._TOKEN.match(tenant) and workday._TOKEN.match(rest[0]):
            site, tail = rest[0], "/" + "/".join(rest[1:])
            base = f"https://{host}/{site}" if own else f"https://{host}/recruiting/{tenant}/{site}"
            return Link("workday", f"{tenant}/{site}", found.group(1), base + tail, host=host, path=tail)
    return None


def _where(system: str, board: str, pid: str) -> tuple[str, str, str]:
    """(system, board, id), compared without case. For Workday it is (workday, tenant, requisition).

    A tenant numbers its requisitions across all its sites and can post one on several of them, so
    the site is left out, and so is the ending of a requisition's later postings
    (`workday.requisition`). The posting's own id, the state's key, still names its site.
    """
    board, pid = unquote(board).casefold(), pid.casefold()
    if system == "workday":
        return system, board.split("/")[0], workday.requisition(pid)
    return system, board, pid


def identity(source_id: str) -> tuple[str, str, str] | None:
    """Where a list's posting lives, from its id: (system, board, id), as `_where` compares them."""
    parts = source_id.split("~")
    if len(parts) < 3 or parts[0] not in SYSTEMS:
        return None
    return _where(parts[0], "/".join(parts[1:-1]), parts[-1])


def board_identity(source: Mapping[str, Any], row_id: str) -> tuple[str, str, str] | None:
    """Where a posting of a watched board lives, in the form `identity` gives a list's posting."""
    kind = source.get("ats")
    if kind in ("greenhouse", "lever", "ashby", *smartrecruiters.KINDS):
        board = source.get("slug")
    elif kind in workday.KINDS:
        try:
            board = workday.site(source)[2].split("/wday/cxs/", 1)[1]
        except (SourceError, TypeError):  # a half-written entry: it matches nothing
            return None
    else:
        return None
    if not isinstance(board, str) or not board:
        return None
    return _where(str(kind), board, row_id)


def _why(url: str) -> str:
    """Why a row's link is not used."""
    if not public_https(url):
        return "no link"
    host = (urlsplit(url).hostname or "").lower()

    def under(domains: Any) -> bool:
        return any(host == domain or host.endswith("." + domain) for domain in domains)

    if under(REPOSTERS) or "adzuna" in host.split("."):
        return "re-poster"
    if under(NO_READER):
        return "no reader"
    if under(READ):
        return "not one posting"
    return "other site"


def problems(source: Mapping[str, Any]) -> list[str]:
    """What is wrong with a list's entry in config/sources.json."""
    sid, found = source.get("id"), []
    if not isinstance(source.get("repo"), str) or not _REPO.match(source["repo"]):
        found.append(f"{sid}: repo must be <owner>/<name> of a public GitHub repo")
    branch = source.get("branch", "main")
    if not isinstance(branch, str) or not _REF.match(branch) or ".." in branch:
        found.append(f"{sid}: branch must be a branch name")
    path = source.get("path")
    if not isinstance(path, str) or not _FILE.match(path) or ".." in path or path.startswith("/"):
        found.append(f"{sid}: path must be the list file's path in the repo")
    elif _format(source) is None:
        found.append(f"{sid}: format must be json or markdown")
    for name in ("fields", "columns"):
        value = source.get(name, {})
        if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
            found.append(f"{sid}: {name} must map names to names")
    sections = source.get("sections", [])
    if not isinstance(sections, list) or not all(isinstance(s, str) and s.strip() for s in sections):
        found.append(f"{sid}: sections must be a list of headings")
    filters = source.get("filters", {})
    if not isinstance(filters, dict) or not all(
        isinstance(k, str) and isinstance(v, list) and all(isinstance(x, str) for x in v) for k, v in filters.items()
    ):
        found.append(f"{sid}: filters must map a field to the values to keep")
    return found


def _format(source: Mapping[str, Any]) -> str | None:
    named = source.get("format")
    if named is not None:
        return named if named in ("json", "markdown") else None
    path = str(source.get("path", "")).lower()
    return "json" if path.endswith(".json") else "markdown" if path.endswith((".md", ".markdown")) else None


def file_url(source: Mapping[str, Any]) -> str:
    return f"https://{RAW}/{source['repo']}/{quote(source.get('branch', 'main'))}/{quote(source['path'])}"


def label(source: Mapping[str, Any]) -> str:
    return str(source.get("label") or f"GitHub list {source['repo']}")


def _plain(cell: str, first_line: bool = False, joiner: str = " ") -> str:
    """A cell's text without markup or pictures: `**Acme** 🔥<br><sub>via X</sub>` is `Acme`."""
    cell = _BREAK.split(cell, maxsplit=1)[0] if first_line else _BREAK.sub(joiner, cell)
    cell = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", re.sub(r"<[^>]+>", " ", cell)).replace("**", "").replace("__", "")
    cell = "".join(c for c in cell if unicodedata.category(c) != "So" and c not in _PICTURES)
    return " ".join(cell.split()).strip(" ;,")


def _href(cell: str) -> str:
    for pattern in (r"\]\((https://[^)\s]+)\)", r'href="(https://[^"]+)"', r"(https://[^\s)<>\"']+)"):
        found = re.search(pattern, cell)
        if found:
            return found.group(1)
    return ""


def _cells(line: str) -> list[str]:
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") and not body.endswith("\\|") else body
    return [cell.replace("\0", "|").strip() for cell in body.replace("\\|", "\0").split("|")]


def _markdown_rows(source: Mapping[str, Any], text: str) -> list[tuple[dict[str, str], dict[str, Any]]]:
    """Rows of every table whose headings name a company, a role and a link; other tables are skipped.

    A company cell of `↳` means the company of the row above. Each row also carries the Markdown
    heading its table is under, which `sections` filters on.
    """
    named = source.get("columns") or {}
    wanted = {fact: (named[fact].casefold(),) if fact in named else names for fact, names in HEADINGS.items()}
    lines, rows, at, section = text.splitlines(), [], 0, ""
    while at < len(lines) - 1:
        head, rule = lines[at].strip(), lines[at + 1].strip()
        at += 1
        if head.startswith("#"):
            section = _plain(head.lstrip("#")).casefold()
        if not (head.startswith("|") and rule.startswith("|")):
            continue
        if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in _cells(rule)):
            continue
        headings = [_plain(cell).casefold() for cell in _cells(head)]
        column = {
            fact: next((headings.index(n) for n in names if n in headings), None) for fact, names in wanted.items()
        }
        usable = all(column[fact] is not None for fact in ("company", "title", "link"))
        at += 1
        above = ""
        while at < len(lines) and lines[at].strip().startswith("|"):
            cells = _cells(lines[at])
            at += 1
            if not usable:
                continue
            cell = {fact: cells[i] if i is not None and i < len(cells) else "" for fact, i in column.items()}
            company = above if cell["company"].startswith("↳") else _plain(cell["company"], first_line=True)
            above = company
            facts = {
                "company": company,
                "title": _plain(cell["title"], first_line=True),
                "location": _plain(cell["location"], joiner="; "),
                "link": _href(cell["link"]),
            }
            rows.append((facts, {"section": section}))
    return rows


def _json_rows(source: Mapping[str, Any], text: str) -> list[tuple[dict[str, str], dict[str, Any]]]:
    try:
        data = json.loads(text)
    except ValueError as error:
        raise SourceError(f"The list is not valid JSON ({error})") from None
    key = source.get("rows")
    if key is not None:
        items = data.get(key) if isinstance(data, dict) else None
    elif isinstance(data, list):
        items = data
    else:  # an object holding one list: that list is the rows
        found = [value for value in data.values() if isinstance(value, list)] if isinstance(data, dict) else []
        items = found[0] if len(found) == 1 else None
    if not isinstance(items, list):
        raise SourceError("The list's rows were not found; set `rows` to the key that holds them")
    names = {**FIELDS, **(source.get("fields") or {})}
    rows = []
    for item in items:
        if not isinstance(item, dict):
            raise SourceError("A row of the list is not an object")
        rows.append(({fact: str(item.get(name) or "").strip() for fact, name in names.items()}, item))
    return rows


def _stated(value: Any) -> set[str]:
    """The values a row gives for a field, compared without case; none when it does not say."""
    values = value if isinstance(value, list) else [value]
    return {str(v).strip().casefold() for v in values if isinstance(v, (str, int, float)) and str(v).strip()}


def _kept(raw: Mapping[str, Any], filters: Mapping[str, list[str]]) -> bool:
    """Whether a row passes the source's filters. A row that does not say is kept: unknown is not a no."""
    for field, allowed in filters.items():
        stated = _stated(raw.get(field))
        if stated and not stated & {a.casefold() for a in allowed}:
            return False
    return True


def _under(heading: str, names: list[str]) -> bool:
    """Whether a heading contains one of the names as whole words: `Mid` is in "Team Mid", not "Midwest"."""
    return any(re.search(rf"(?<!\w){re.escape(name)}(?!\w)", heading) for name in names)


def _unmatched(source: Mapping[str, Any], rows: list[tuple[dict[str, str], dict[str, Any]]]) -> dict[str, list[str]]:
    """Section names and filter values that nothing in the list has.

    When none of a filter's values, or none of the section names, matches anything, every row would be
    left out and every posting the list had would close; that is far likelier a typo or a renamed
    heading than a list with nothing for the feed, so the list fails instead and nothing closes.
    """
    unmatched: dict[str, list[str]] = {}
    names = [name.casefold() for name in source.get("sections") or []]
    if names:
        headings = {raw.get("section") or "" for _, raw in rows}
        missing = [name for name in source["sections"] if not any(_under(h, [name.casefold()]) for h in headings)]
        if len(missing) == len(names):
            shown = ", ".join(sorted(h for h in headings if h)[:12]) or "none"
            raise SourceError(f"No heading of the list contains any of `sections`; its headings are: {shown}")
        if missing:
            unmatched["sections"] = missing
    for field, allowed in (source.get("filters") or {}).items():
        stated = set().union(*(_stated(raw.get(field)) for _, raw in rows))
        if not stated:
            raise SourceError(f"No row of the list has a value for the filter field {field!r}")
        missing = [value for value in allowed if value.casefold() not in stated]
        if len(missing) == len(allowed):
            raise SourceError(f"None of {allowed} is a {field} in the list; it has {sorted(stated)[:12]}")
        if missing:
            unmatched[field] = missing
    return unmatched


def read(client: Client, source: Mapping[str, Any]) -> tuple[dict[str, tuple[Job, Link]], dict[str, Any]]:
    """The list's usable postings as listed, without their text, and a count of every row by outcome."""
    found = problems(source)
    if found:
        raise SourceError("; ".join(found))
    client.space(RAW, DELAY)
    text = client.get(file_url(source)).raise_for_status().text
    rows = _json_rows(source, text) if _format(source) == "json" else _markdown_rows(source, text)
    if not rows:
        # A moved or emptied file must not close every posting the list had.
        raise SourceError("The list has no rows of jobs; refusing an empty snapshot")
    unmatched = _unmatched(source, rows)
    tally: dict[str, Any] = {"rows": len(rows), "filtered": 0, "skipped": {}, "repeated": 0, "systems": {}}
    if unmatched:
        tally["unmatched"] = unmatched
    listed: dict[str, tuple[Job, Link]] = {}
    sections = [name.casefold() for name in source.get("sections") or []]
    for facts, raw in rows:
        under = raw.get("section") if _format(source) == "markdown" else None
        if not _kept(raw, source.get("filters") or {}) or (
            sections and under is not None and not _under(under, sections)
        ):
            tally["filtered"] += 1
            continue
        found_link = link(facts["link"])
        if found_link is None or not facts["title"]:
            why = "no title" if found_link else _why(facts["link"])
            tally["skipped"][why] = tally["skipped"].get(why, 0) + 1
            continue
        lid = found_link.source_id
        if lid in listed:
            tally["repeated"] += 1
            continue
        job = Job(
            key=f"{source['id']}:{lid}",
            source=source["id"],
            source_id=lid,
            company=facts["company"] or unquote(found_link.board.split("/")[0]),
            title=facts["title"],
            location=facts["location"] or "unknown",
            url=found_link.url,
            source_label=label(source),
        )
        listed[lid] = (job, found_link)
        tally["systems"][found_link.system] = tally["systems"].get(found_link.system, 0) + 1
    tally["postings"] = len(listed)
    return listed, tally


def _detail(client: Client, job: Job, found: Link, boards: dict[str, Any]) -> Job | None:
    """The posting with its text from the employer's own system, or None when it would not give it.

    Its title and place stay the list's: they are what change is measured on, every update.
    """
    try:
        if found.system == "workday":
            tenant, site = found.board.split("/")
            client.space(found.host, workday.DELAY)
            return workday._detail(client, f"https://{found.host}/wday/cxs/{tenant}/{site}", job, found.path)
        if found.system == "smartrecruiters":
            client.space(smartrecruiters.HOST, smartrecruiters.DELAY)
            plain = replace(job, key=f"{job.source}:{found.id}", source_id=found.id)
            root = f"https://{smartrecruiters.HOST}/v1/companies/{found.board}/postings"
            got = smartrecruiters._detail(client, root, plain, found.board)
            return replace(got, key=job.key, source_id=job.source_id) if got else None
        if found.system == "ashby":  # no endpoint for one posting: read the board once per update
            if found.board not in boards:
                client.space(ASHBY, DELAY)
                data = client.get(f"https://{ASHBY}/posting-api/job-board/{found.board}").raise_for_status().json()
                boards[found.board] = data.get("jobs") if isinstance(data, dict) else None
            rows = boards[found.board] if isinstance(boards[found.board], list) else []
            row = next((r for r in rows if isinstance(r, dict) and str(r.get("id", "")).lower() == found.id), None)
            if row is None or row.get("isListed") is False:
                return None
            facts = ats._ashby(row)
        else:
            host = GREENHOUSE if found.system == "greenhouse" else LEVER
            client.space(host, DELAY)
            if found.system == "greenhouse":
                api = f"https://{host}/v1/boards/{found.board}/jobs/{found.id}"
            else:
                api = f"https://{host}/v0/postings/{found.board}/{found.id}"
            data = client.get(api).raise_for_status().json()
            facts = (ats._greenhouse if found.system == "greenhouse" else ats._lever)(data)
            if str(facts["source_id"]).lower() != found.id:
                return None
    except (HttpError, OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    text = facts.get("description") or ""
    if not text:
        return None
    # The employment type stays in the text, as for Workday: stage 0 must see the same facts every
    # update, and most updates have only the list.
    kind = facts.get("employment_type")
    if isinstance(kind, str) and kind and kind != "unknown":
        text = f"Employment: {kind}\n\n{text}"
    url, applied = facts.get("url"), facts.get("apply_url")
    return replace(
        job,
        description=text,
        posted_at=facts.get("posted_at") if isinstance(facts.get("posted_at"), str) else None,
        url=url if public_https(url) else job.url,
        apply_url=applied if public_https(applied) else None,
        department=str(facts.get("department") or ""),
    )


def fetch(client: Client, source: Mapping[str, Any], rank: Callable[[Job], int | None] | None) -> list[Job]:
    listed, _ = read(client, source)
    boards: dict[str, Any] = {}
    return lazy.complete(
        listed, rank, lazy.budget(source, DETAILS), lambda job, found: _detail(client, job, found, boards)
    )
