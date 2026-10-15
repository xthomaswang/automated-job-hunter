"""SAP SuccessFactors career sites built with Career Site Builder, read through their own search pages.

No login, no key. A site's search page (`/search/?q=<query>&startrow=<n>`, under its brand's path if
it has one) lists 25 postings at a time without their text, with the number it found: as a table
("Results 1 - 25 of 312") or as tiles ("Showing 1 to 25 of 312 Jobs"). Every posting a search finds is
read and checked against that number, so a page whose rows differ from its own count, a count that
changes while the search is read, or a page with neither postings nor its "no results" notice fails
the source. A posting's text is its own page, one more request each, fetched only where the rank
function asks for it, at most `max_details` per update, one request a second, as no rate is
documented. So, like Workday, a site is read through searches (`queries`, by default the wanted and
related roles), each searched as a phrase.

Not read, by what SAP's own robots.txt files say: a site whose search page fills in its results from a
script, which loads them from `/services/`, a path the robots.txt SAP generates for every site
disallows; and the older job board on SAP's own hosts (`career<N>.successfactors.com`), whose
robots.txt disallows everything but signing in. docs/spikes.md (E12) records what sites answered
when this was written; these are web pages, not an API, so any change of layout fails the source and
keeps its last good snapshot.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, urlsplit

from ..http import Client, HttpError
from ..model import Job, SourceError
from ..textutil import html_to_text
from . import lazy

KINDS = ("successfactors",)
SYSTEM = "SAP SuccessFactors"
# How a probe names a site it cannot read: its search page fills in its results from a script.
SCRIPTED = "SAP SuccessFactors (a career site whose search loads its postings from a path its robots.txt disallows)"
MAX_PER_QUERY = 1000  # 40 pages at a request a second; a search matching more is too broad to read every update
DETAILS = 100  # texts fetched per source per update, unless the source sets max_details
DELAY = 1.0  # seconds between requests to one site: no rate is documented
# A career site is on the employer's own domain. SAP's own hosts carry the older job board instead.
_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]\Z")
_SAP = re.compile(r"(^|\.)(successfactors|sapsf)\.[a-z]+\Z")
_BRAND = re.compile(r"[A-Za-z0-9_-]+\Z")
_JOB = re.compile(r"/job/[^/\\\s]+/(\d+)/?\Z")  # /job/<city-title-region>/<id>/
_GENERIC = frozenset("www jobs job careers career".split())  # host labels that do not name the employer
# A count such as "312", "1,234", "1.234" or "1 234" (with a no-break space). An ordinary space is not
# read as a separator, so a count written that way has too many numbers and is refused, not misread.
_NUMBER = re.compile(r"\d{1,3}(?:[,.'\u00a0\u202f\u2009]\d{3})+(?!\d)|\d+")
_FIELD = re.compile(r"job-\d+-(?:desktop|tablet|mobile)-section-([a-z0-9]+)-value\Z")  # a tile's fields
_TILE_FIELDS = ("location", "multilocation", "department", "date")
_BREAKS = frozenset("br p div li ul ol tr td table h1 h2 h3 h4 h5 h6".split())


class ScriptListing(SourceError):
    """The site's search page fills in its results from a script: they come from a path robots.txt disallows."""


def site(source: Mapping[str, Any]) -> tuple[str, str]:
    """(host, base URL) of a source, checked so that no request goes anywhere else."""
    host, brand = source.get("host"), source.get("brand") or None
    if not isinstance(host, str) or not _HOST.match(host) or not (brand is None or _BRAND.match(str(brand))):
        raise SourceError(
            "SuccessFactors needs the career site's host name, such as jobs.example.com, and optionally its brand "
            f"path, not {host!r}/{brand!r}"
        )
    if _SAP.search(host):
        raise SourceError(f"{host} is SAP's own host, whose robots.txt disallows reading; use the career site's host")
    return host, f"https://{host}" + (f"/{brand}" if brand else "")


def source_id(host: str, brand: str | None = None) -> str:
    """A short id for a site: the first label of its host that names the employer, and its brand."""
    labels = host.split(".")[:-1] or [host]
    name = next((label for label in labels if label not in _GENERIC), labels[0])
    words = [name, *([brand] if brand and brand.casefold() != name.casefold() else [])]
    return re.sub(r"[^a-z0-9]+", "-", "-".join(words).lower()).strip("-")


class _Page(HTMLParser):
    """What one search page shows: its count, its rows, or what it shows instead of them."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, str]] = []
        self.counts: list[list[str]] = []  # each count on the page: the table's <b> parts, or the tiles' label
        self.layout = ""
        self.returned: str | None = None  # how many rows the tiles say they hold
        self.starts: set[int] = set()  # the startrow of every page the page links to
        self.none = self.scripted = False
        self._row: dict[str, str] | None = None
        self._row_tag, self._row_depth = "", 0
        self._open: list[list[Any]] = []  # text being collected: [what, tag, depth, parts]

    def _collect(self, what: str, tag: str) -> None:
        self._open.append([what, tag, 1, []])

    def _collecting(self, what: str) -> bool:
        return any(entry[0] == what for entry in self._open)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {name: value or "" for name, value in attrs}
        classes, ident = a.get("class", "").split(), a.get("id", "")
        for entry in self._open:
            if entry[1] == tag:
                entry[2] += 1
            if tag in _BREAKS:
                entry[3].append("\n")
        linked = re.search(r"[?&]startrow=(\d+)", a.get("href", "")) if tag == "a" else None
        if linked:
            self.starts.add(int(linked.group(1)))
        if tag == "script" and "searchResultsUnify" in a.get("src", ""):
            self.scripted = True
        elif tag == "div" and ident == "noresults":
            self.none = True
        elif tag == "ul" and ident == "job-tile-list":
            self.returned = a.get("data-record-returned")
        if tag == "span" and "paginationLabel" in classes:  # Results <b>1 - 25</b> of <b>312</b>
            self.layout = "table"
            self.counts.append([])
            self._collect("count", tag)
        elif tag == "span" and ident == "tile-search-results-label":  # Showing 1 to 25 of 312 Jobs
            self.layout = "tiles"
            self._collect("label", tag)
        elif tag == "b" and self._collecting("count"):
            self._collect("part", tag)
        if (tag == "tr" and "data-row" in classes) or (tag == "li" and "job-tile" in classes):
            self._row = {"href": a.get("data-url", "")}
            self._row_tag, self._row_depth = tag, 1
            self.rows.append(self._row)
            return
        if self._row is None:
            return
        if tag == self._row_tag:
            self._row_depth += 1
        field = _FIELD.match(ident) if tag == "div" else None
        if tag == "a" and "jobTitle-link" in classes and "title" not in self._row and not self._collecting("title"):
            self._row["href"] = a.get("href") or self._row["href"]
            self._collect("title", tag)
        elif tag == "span" and {"jobLocation", "jobDate"} & set(classes):
            what = "location" if "jobLocation" in classes else "date"
            if what not in self._row and not self._collecting(what):
                self._collect(what, tag)
        elif field and field.group(1) in _TILE_FIELDS:
            what = field.group(1)
            if what not in self._row and not self._collecting(what):  # the first copy: tiles repeat per screen size
                self._collect(what, tag)

    def handle_endtag(self, tag: str) -> None:
        for entry in list(self._open):
            if entry[1] == tag:
                entry[2] -= 1
                if entry[2] == 0:
                    self._open.remove(entry)
                    self._done(entry[0], "".join(entry[3]))
        if self._row is not None and tag == self._row_tag:
            self._row_depth -= 1
            if self._row_depth == 0:
                self._row = None

    def handle_data(self, data: str) -> None:
        for entry in self._open:
            entry[3].append(data)

    def _done(self, what: str, text: str) -> None:
        if what == "part":
            self.counts[-1].append(text)
        elif what == "label":
            self.counts.append([text])
        elif what != "count" and self._row is not None:
            self._row[what] = text


def _numbers(text: str) -> list[int]:
    return [int(re.sub(r"\D", "", number)) for number in _NUMBER.findall(text)]


def _count(parts: list[str], layout: str) -> tuple[int, int, int]:
    """(first row, last row, total) from a count, read by its structure rather than its words."""
    if layout == "table" and len(parts) == 2:
        shown, total = _numbers(parts[0]), _numbers(parts[1])
        numbers = [*shown, *total] if len(shown) == 2 and len(total) == 1 else []
    else:
        numbers = _numbers(parts[0]) if layout == "tiles" and len(parts) == 1 else []
    if len(numbers) != 3:
        raise SourceError("SuccessFactors search page shows its count in a form this version cannot read")
    return numbers[0], numbers[1], numbers[2]


@dataclass(frozen=True)
class Results:
    """One search page: how many postings the search found, and the rows on this page."""

    total: int
    rows: list[dict[str, str]]
    starts: frozenset[int]


def read(page: str, startrow: int) -> Results:
    """A search page, checked against its own count. A page that shows neither fails."""
    parser = _Page()
    parser.feed(page)
    parser.close()
    rows = parser.rows
    if parser.scripted:  # first: such a page may carry an empty results template, which must not read as none
        raise ScriptListing(
            "This SuccessFactors site's search page loads its postings from a script that reads /services/, "
            "which its robots.txt disallows, so it cannot be read"
        )
    if not parser.counts:
        if parser.none and not rows:  # the site's own notice that nothing matched
            return Results(0, [], frozenset())
        raise SourceError(
            "SuccessFactors search page shows neither postings with their count nor its no-results notice; "
            "its layout may have changed"
        )
    counts = {_count(parts, parser.layout) for parts in parser.counts}
    first, last, total = counts.pop() if len(counts) == 1 else (-1, -1, -1)
    shown = (first, last) == (startrow + 1, startrow + len(rows)) and last <= total  # the rows its count names
    held = parser.returned in (None, str(len(rows)))  # tiles also say how many rows they hold
    if not (shown and held):
        raise SourceError("SuccessFactors search page does not match its own count; its layout may have changed")
    return Results(total, rows, frozenset(parser.starts))


def phrase(query: str) -> str:
    """A query as the site should search it. Its search matches any of the words anywhere in a posting, so
    a role title is searched as a phrase, unless the query has quotes of its own."""
    return query if '"' in query else f'"{query}"'


def search(client: Client, base: str, query: str, startrow: int = 0) -> Results:
    reply = client.get(base + "/search/", params={"q": query, "startrow": startrow})
    return read(reply.raise_for_status().text, startrow)


def _listing(client: Client, base: str, query: str) -> list[dict[str, str]]:
    """Every posting one search finds. Anything short of its count aborts the snapshot."""
    rows: dict[str, dict[str, str]] = {}
    host, prefix = urlsplit(base).hostname or "", urlsplit(base).path
    total: int | None = None
    startrow = 0
    while True:
        page = search(client, base, query, startrow)
        if total is None:
            total = page.total
            if total > MAX_PER_QUERY:
                raise SourceError(
                    f"SuccessFactors search {query} matches {total} postings, more than {MAX_PER_QUERY}: "
                    "use fewer or more specific queries"
                )
            last_page = (total - 1) // len(page.rows) * len(page.rows) if page.rows else 0
            if max(page.starts, default=0) > last_page:  # pages beyond its count: the count was misread
                raise SourceError(
                    "SuccessFactors search page links past the count it shows; its layout may have changed"
                )
        elif page.total != total:
            raise SourceError("SuccessFactors listing changed while it was read; the next update retries")
        for row in page.rows:
            row["path"], row["id"] = _path(row.get("href", ""), host, prefix)
            rows.setdefault(row["id"], row)
        startrow += len(page.rows)
        if not page.rows or startrow >= total:
            break
    if len(rows) != total:
        raise SourceError("SuccessFactors listing ended short of its total; the next update retries")
    return list(rows.values())


def _path(href: str, host: str, prefix: str) -> tuple[str, str]:
    """(path, posting id) of a row's link, which must be one posting on this site."""
    parts = urlsplit(href)
    path = quote(parts.path, safe="/%-._~!$&'()*+,;=:@")
    elsewhere = (parts.scheme or parts.netloc) and (parts.scheme != "https" or parts.hostname != host)
    found = _JOB.match(path[len(prefix) :]) if path.startswith(prefix + "/job/") else None
    if elsewhere or not found or ".." in path:
        raise SourceError("SuccessFactors posting link is missing or not one posting on this site")
    return path, found.group(1)


def _line(text: str | None) -> str:
    return " ".join((text or "").split())


def _place(row: Mapping[str, str]) -> str:
    """The row's place, built the same way on every update: its location and any others it lists."""
    places = [_line(line) for key in ("location", "multilocation") for line in (row.get(key) or "").split("\n")]
    return " / ".join(dict.fromkeys(place for place in places if place)) or "unknown"


def _listed(source: Mapping[str, Any], host: str, row: Mapping[str, str]) -> Job:
    """A posting as the listing shows it: everything but its text. Its title and place are what change
    is measured on. The listing's date is the site's own text, when it shows one."""
    return Job(
        key=f"{source['id']}:{row['id']}",
        source=source["id"],
        source_id=row["id"],
        company=source["company"],
        title=_line(row.get("title")),
        location=_place(row),
        url=f"https://{host}{row['path']}",
        posted_at=_line(row.get("date")) or None,
        department=_line(row.get("department")),
    )


class _Posting(HTMLParser):
    """A posting's page: its canonical link and, as simple HTML, the element marked as its description."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.canonical = ""
        self.parts: list[str] = []
        self._tag, self._depth, self._seen = "", 0, False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {name: value or "" for name, value in attrs}
        if tag == "link" and a.get("rel", "").casefold() == "canonical" and not self.canonical:
            self.canonical = a.get("href", "")
        if self._depth:
            if tag == self._tag:
                self._depth += 1
            self.parts.append(f"<{tag}>")
        elif not self._seen and a.get("itemprop") == "description" and tag != "meta":
            self._tag, self._depth, self._seen = tag, 1, True

    def handle_endtag(self, tag: str) -> None:
        if self._depth:
            if tag == self._tag:
                self._depth -= 1
            if self._depth:
                self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._depth:
            self.parts.append(html.escape(data, quote=False))


def _detail(client: Client, job: Job) -> Job | None:
    """The posting with its text, or None when the site would not give it this time.

    The page's own date (`datePosted`) is not used: on the sites checked it was the day the page was
    read, not the day the posting was published. The page must name itself as this posting.
    """
    try:
        page = client.get(job.url).raise_for_status().text
    except (HttpError, OSError, ValueError):
        return None
    parser = _Posting()
    parser.feed(page)
    parser.close()
    named = _JOB.search(urlsplit(parser.canonical).path)
    text = html_to_text("".join(parser.parts))
    if not named or named.group(1) != job.source_id or not text:
        return None  # closed, moved, or another page
    return replace(job, description=text)


def recognize(url: str, page: str) -> dict[str, Any] | None:
    """The career site a page is on, when the page is a Career Site Builder page itself. Such a page sets
    up SAP's scripts (`j2w.init`, `/platform/js/j2w/`), with its brand's path, if any, among their settings."""
    start = page.find("j2w.init(")
    if start < 0 or "/platform/js/j2w/" not in page:
        return None
    named = re.search(r'"brand"\s*:\s*"([^"]*)"', page[start : start + 20000])
    brand = named.group(1) if named and _BRAND.match(named.group(1)) else None
    return {"system": SYSTEM, "ats": "successfactors", "host": (urlsplit(url).hostname or "").lower(), "brand": brand}


def fetch(client: Client, source: Mapping[str, Any], rank: Callable[[Job], int | None] | None) -> list[Job]:
    host, base = site(source)
    client.space(host, DELAY)
    queries = source.get("queries")
    if not isinstance(queries, list) or not queries or not all(isinstance(q, str) and q.strip() for q in queries):
        raise SourceError("SuccessFactors is read through searches: set queries, or wanted roles in the criteria")
    listed: dict[str, tuple[Job, None]] = {}
    for query in dict.fromkeys(q.strip() for q in queries):
        for row in _listing(client, base, phrase(query)):
            job = _listed(source, host, row)
            listed.setdefault(job.source_id, (job, None))
    return lazy.complete(listed, rank, lazy.budget(source, DETAILS), lambda job, _: _detail(client, job))
