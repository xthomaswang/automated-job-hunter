"""Radancy TalentBrew career sites, read through their sitemap, each posting's text from its own page.

No login, no key. A TalentBrew site is on the employer's own host, and the robots.txt it serves
disallows its search, category and location pages, so none of them is read. Its sitemap
(`/sitemap.xml`: a sitemaps.org list of links, or an index of such lists) names every open posting
as `https://<host>[/<lang>]/job/<city>/<title>/<org id>/<job id>`, among pages that are not postings,
which are never fetched. So the listing is the sitemap. A posting's title and place come from its
link's slugs, the same on every update, and its text, date and own place from the schema.org
`JobPosting` its page embeds: one more request each, fetched only where the rank function asks for
it, at most `max_details` per update, one request a second, as no rate is documented. Every update
first reads robots.txt and refuses the site when it disallows the sitemap or a posting. The
sitemap's dates are when it was made, the same for every link, so they are not used; a closed
posting leaves the sitemap. docs/spikes.md (E13) records what this was built from; these are web
pages, not an API, so any change of shape fails the source and keeps its last good snapshot.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import replace
from html.parser import HTMLParser
from typing import Any, NamedTuple
from urllib.parse import SplitResult, quote, unquote, urlsplit
from xml.etree import ElementTree

from .. import robots
from ..http import Client, HttpError
from ..model import Job, SourceError
from ..textutil import html_to_text, words_key
from . import lazy

KINDS = ("radancy",)
SYSTEM = "Radancy TalentBrew"
DETAILS = 100  # texts fetched per source per update, unless the source sets max_details
DELAY = 1.0  # seconds between requests to one site: no rate is documented
MAX_SITEMAPS = 50  # the sitemaps an index names are all read on every update, one request a second
# At most one posting link in twenty may be malformed, and one always may: a stray hand-made link then
# costs only its own posting. The links all come from one template, so when that changes most or all
# of them break, and the source fails instead of closing its postings. `collect` holds only a sharp
# shrink (over 40%, and only once), so a smaller loss has to be refused here.
MALFORMED = 20
# A career site is on the employer's own domain: a plain host name, not an address.
_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]\Z")
_LANG = re.compile(r"[a-z]{2}(?:-[a-z0-9]{2,4})?\Z", re.I)  # a language segment, such as en or fr-ca
_ID = re.compile(r"\d{1,20}\Z")
_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
_MARKER = re.compile(r"(?:https?:)?//(?:tbcdn\.talentbrew\.com|cdn\.radancy\.eu)/", re.I)  # the vendor's assets


class Listed(NamedTuple):
    """A posting as the sitemap names it."""

    jid: str
    url: str
    city: str
    title: str


def site(source: Mapping[str, Any]) -> str:
    """The career site's host, checked so that no request goes anywhere else."""
    host = source.get("host")
    host = host.lower() if isinstance(host, str) else ""
    if len(host) > 253 or not _HOST.match(host):
        raise SourceError(
            f"Radancy needs the career site's host name, such as careers.<company>.com, not {source.get('host')!r}"
        )
    return host


def _split(loc: str | None) -> SplitResult | None:
    try:
        return urlsplit(loc) if loc else None
    except ValueError:
        return None


def _quoted(path: str) -> str:
    """A link's path as it can be requested: its own escapes kept, any other character escaped."""
    return quote(path, safe="/%-._~!$&'()*+,;=:@")


def _read(client: Client, url: str) -> tuple[bool, list[str | None]]:
    """(whether it is an index, the address in each entry) of one sitemap. Anything else fails the source."""
    body = client.get(url).raise_for_status().body
    if re.search(rb"<!(?:DOCTYPE|ENTITY)", body, re.I):  # a sitemap needs neither; refused before parsing
        raise SourceError(f"{url} is not a sitemap: it declares a document type, which no sitemap needs")
    try:
        root = ElementTree.fromstring(body)
    except (ElementTree.ParseError, ValueError, LookupError) as error:
        raise SourceError(f"The sitemap {url} is not well-formed XML") from error
    index = root.tag == _NS + "sitemapindex"
    if not index and root.tag != _NS + "urlset":
        raise SourceError(f"{url} is not a sitemap; the site's layout may have changed")
    locs = [entry.find(_NS + "loc") for entry in root.findall(_NS + ("sitemap" if index else "url"))]
    return index, [((loc.text or "").strip() or None) if loc is not None else None for loc in locs]


def _children(host: str, locs: list[str | None]) -> list[str]:
    """The sitemaps an index names on the site's own host, the only host this reader asks anything."""
    children = []
    for loc in locs:
        parts = _split(loc)
        if parts is not None and parts.hostname != host:
            continue  # another host's sitemap is not read
        if parts is None or parts.scheme != "https":
            raise SourceError(f"The sitemap index of {host} has an entry without an https address on {host}")
        children.append(f"https://{host}{_quoted(parts.path)}" + (f"?{parts.query}" if parts.query else ""))
    children = list(dict.fromkeys(children))
    if not children:
        raise SourceError(f"The sitemap index of {host} names no sitemap on {host}")
    if len(children) > MAX_SITEMAPS:
        raise SourceError(f"The sitemap index of {host} names {len(children)} sitemaps, more than {MAX_SITEMAPS}")
    return children


def _words(slug: str) -> str:
    """A slug as words, each capitalized: `new-york` is "New York". A percent-encoded one is decoded."""
    return " ".join(word[:1].upper() + word[1:] for word in unquote(slug).replace("-", " ").split())


def _posting(host: str, parts: SplitResult, rest: list[str]) -> Listed | None:
    """The posting a link names, or None when the link is not in the shape every posting link has:
    `<city>/<title>/<org id>/<job id>` after `/job/`, on https."""
    if parts.scheme != "https" or len(rest) != 4 or not (_ID.match(rest[2]) and _ID.match(rest[3])):
        return None
    city, title = _words(rest[0]), _words(rest[1])
    if not city or not title:
        return None
    return Listed(rest[3], f"https://{host}{_quoted(parts.path)}", city, title)


def _postings(host: str, locs: list[str | None]) -> list[Listed]:
    """Every posting the sitemap's links name. Too many malformed posting links fail the source."""
    found: dict[str, Listed] = {}
    links = malformed = 0
    for loc in locs:
        parts = _split(loc)
        if parts is not None and parts.hostname != host:
            continue  # not a page of this site
        path = [segment for segment in parts.path.split("/") if segment] if parts is not None else []
        lead = 1 if len(path) > 1 and path[1] == "job" and _LANG.match(path[0]) else 0
        if parts is not None and path[lead : lead + 1] != ["job"]:
            continue  # not a posting: the home page, or a category, place, employment or business page
        links += 1  # a posting link, or an entry that cannot be read at all
        posting = _posting(host, parts, path[lead + 1 :]) if parts is not None else None
        if posting is None:
            malformed += 1
        elif posting.jid not in found or posting.url < found[posting.jid].url:
            found[posting.jid] = posting  # one posting under several links: the same one on every update
    if malformed > max(1, links // MALFORMED):
        raise SourceError(
            f"The sitemap of {host} has {malformed} of {links} posting links in a shape this version cannot read; "
            "its layout may have changed"
        )
    if not found:
        raise SourceError(f"The sitemap of {host} names no posting; its layout may have changed")
    return list(found.values())


def listing(client: Client, host: str) -> list[Listed]:
    """Every posting the site's sitemap names, once robots.txt allows reading it and them."""
    permits = robots.permits(client, host)
    root = f"https://{host}/sitemap.xml"
    if not permits(root):
        raise SourceError(f"robots.txt on {host} disallows its sitemap, so the site cannot be read")
    index, locs = _read(client, root)
    if index:
        children, locs = _children(host, locs), []
        for child in children:
            if not permits(child):
                raise SourceError(f"robots.txt on {host} disallows its sitemap {child}, so the site cannot be read")
            nested, found = _read(client, child)
            if nested:
                raise SourceError(f"The sitemap {child} is an index inside an index, which sitemaps do not allow")
            locs += found
    postings = _postings(host, locs)
    refused = next((posting.url for posting in postings if not permits(posting.url)), None)
    if refused:
        raise SourceError(
            f"robots.txt on {host} disallows postings such as {urlsplit(refused).path}, so the site cannot be read"
        )
    return postings


def _listed(source: Mapping[str, Any], posting: Listed) -> Job:
    """A posting as the sitemap names it: everything but its text. Its title and place come from its
    link's slugs, the same on every update, so they are what change is measured on."""
    return Job(
        key=f"{source['id']}:{posting.jid}",
        source=source["id"],
        source_id=posting.jid,
        company=source["company"],
        title=posting.title,
        location=posting.city,
        url=posting.url,
    )


class _Page(HTMLParser):
    """A posting's page: the JSON-LD it embeds, and the posting ids its elements carry (`data-job-id`)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[str] = []
        self.ids: set[str] = set()
        self._script: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {name: value or "" for name, value in attrs}
        if a.get("data-job-id", "").strip():
            self.ids.add(a["data-job-id"].strip())
        if tag == "script" and a.get("type", "").split(";")[0].strip().lower() == "application/ld+json":
            self._script = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script is not None:
            self.scripts.append("".join(self._script))
            self._script = None

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script.append(data)


def _find(value: Any) -> dict[str, Any] | None:
    """The first schema.org JobPosting in a JSON-LD value: the value itself, a list, or a `@graph`."""
    if isinstance(value, list):
        return next((found for found in map(_find, value) if found is not None), None)
    if isinstance(value, dict):
        kind = value.get("@type")
        if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
            return value
        return _find(value.get("@graph"))
    return None


def _job_posting(scripts: list[str]) -> dict[str, Any] | None:
    for script in scripts:
        try:
            found = _find(json.loads(script, strict=False))  # strict=False: raw line breaks inside strings
        except (ValueError, RecursionError):
            continue
        if found is not None:
            return found
    return None


def _name(value: Any) -> str:
    """A JSON-LD text: the text itself, or the `name` of a schema.org object such as a Country."""
    if isinstance(value, dict):
        value = value.get("name")
    return " ".join(html.unescape(value).split()) if isinstance(value, str) else ""


def _own_place(places: Any) -> str:
    """Where the posting's own record puts it: "Locality, Region, Country" for each of its places."""
    named = []
    for place in places if isinstance(places, list) else [places]:
        address = place.get("address") if isinstance(place, dict) else None
        if isinstance(address, dict):
            parts = (_name(address.get(key)) for key in ("addressLocality", "addressRegion", "addressCountry"))
            line = ", ".join(part for part in parts if part)
        else:
            line = _name(address)  # an address written out as one text
        if line:
            named.append(line)
    return " / ".join(dict.fromkeys(named))


def _detail(client: Client, job: Job) -> Job | None:
    """The posting with its text, or None when its page would not give it this time.

    The page must be this posting's: where it landed ends in the posting's id, an element carries
    that id, and it embeds a JobPosting with a description.
    """
    try:
        reply = client.get(job.url).raise_for_status()
    except (HttpError, OSError, ValueError):
        return None
    landed = [segment for segment in urlsplit(reply.url).path.split("/") if segment]
    page = _Page()
    page.feed(reply.text)
    page.close()
    posting = _job_posting(page.scripts)
    if landed[-1:] != [job.source_id] or job.source_id not in page.ids or posting is None:
        return None  # closed, moved, or another page
    described = posting.get("description")
    text = html_to_text(described if isinstance(described, str) else "")
    if not text:
        return None
    # The page's title, with its own capitals and punctuation, only when its words are the slug's: the
    # title is what change is measured on, and most updates have only the sitemap.
    named = _name(posting.get("title"))
    title = named if named and words_key(named) == words_key(job.title) else job.title
    # The employment type stays in the text, not in `employment_type`: stage 0 must see the same facts
    # every update, and most updates have only the sitemap, which has no such field. Otherwise a posting
    # it drops for being part-time would look new again next time, and its text would be fetched again
    # and again. The place stays the slug's for the same reason. The record's own place goes in
    # `own_location`, and `collect` stores what it decides from it, so updates with only the sitemap
    # still know.
    own = _own_place(posting.get("jobLocation"))
    kind = posting.get("employmentType")
    kinds = [label for label in map(_name, kind if isinstance(kind, list) else [kind]) if label]
    head = [
        *([("Locations: " if " / " in own else "Location: ") + own] if own else []),
        *(["Employment: " + ", ".join(kinds)] if kinds else []),
    ]
    posted = posting.get("datePosted")
    return replace(
        job,
        title=title,
        description="\n".join(head) + "\n\n" + text if head else text,
        posted_at=posted.strip() if isinstance(posted, str) and posted.strip() else None,
        own_location=own,
    )


def recognize(url: str, page: str) -> dict[str, Any] | None:
    """The career site a page is on, when the page is a TalentBrew page itself: it loads the vendor's
    assets (`tbcdn.talentbrew.com`, `cdn.radancy.eu`). A posting link's shape alone proves nothing."""
    if not _MARKER.search(page):
        return None
    return {"system": SYSTEM, "ats": "radancy", "host": (urlsplit(url).hostname or "").lower()}


def fetch(client: Client, source: Mapping[str, Any], rank: Callable[[Job], int | None] | None) -> list[Job]:
    host = site(source)
    client.space(host, DELAY)
    listed = {posting.jid: (_listed(source, posting), None) for posting in listing(client, host)}
    return lazy.complete(listed, rank, lazy.budget(source, DETAILS), lambda job, _: _detail(client, job))
