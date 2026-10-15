"""Choosing sources: the bundled list of known boards, and probing for a company's board."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import criteria as C
from . import sources as S
from .http import Client
from .layout import Layout
from .model import SourceError
from .sources import ats, lists, smartrecruiters, workday
from .sources.aggregators import PROVIDERS
from .state import read_json, write_text

# Career systems this version cannot read, by the hosts their public pages are on. A probe names
# them, so that nobody keeps looking for a board that no reader exists for.
UNSUPPORTED = (
    (r"(^|\.)oraclecloud\.com$", "Oracle Recruiting Cloud (its job APIs are only for Oracle and approved partners)"),
    (r"(^|\.)taleo\.net$", "Oracle Taleo"),
    (r"(^|\.)icims\.com$", "iCIMS (its terms forbid automated reading of its career sites)"),
    (r"(^|\.)(successfactors|sapsf)\.(com|eu)$", "SAP SuccessFactors"),
    (r"(^|\.)eightfold\.ai$", "Eightfold"),
    (r"(^|\.)avature\.net$", "Avature"),
    (r"(^|\.)phenompeople\.com$", "Phenom"),
    (r"(^|\.)brassring\.com$", "Kenexa BrassRing"),
    (r"(^|\.)jobvite\.com$", "Jobvite"),
    (r"(^|\.)workable\.com$", "Workable"),
    (r"(^|\.)bamboohr\.com$", "BambooHR"),
    (r"(^|\.)teamtailor\.com$", "Teamtailor"),
    (r"(^|\.)personio\.(de|com)$", "Personio"),
    (r"(^|\.)(ultipro|ukg)\.com$", "UKG"),
    (r"(^|\.)dayforcehcm\.com$", "Dayforce"),
)
# Never read, whose terms forbid it. Named only for a URL probed directly: almost every careers page
# links to them in its footer, which says nothing about where its postings are.
_FORBIDDEN = re.compile(r"(^|\.)(linkedin|indeed)\.com$")
_LINK = re.compile(r"https?://[^\s\"'<>()\\]+", re.I)
_LOCALE = re.compile(r"[a-z]{2}-[A-Z]{2}\Z")


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
        elif len(parts) == 3 and parts[0] in smartrecruiters.KINDS:
            _, slug, company = parts
            entry = {
                "id": re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-"),
                "company": company,
                "ats": "smartrecruiters",
                "slug": slug,
            }
            smartrecruiters.root(entry)  # refuses an identifier that is not a plain token
        elif len(parts) == 3 and parts[0] in workday.KINDS:
            _, target, company = parts
            pieces = target.split("/")  # host/site, or host/tenant/site on a shared host
            host, name = pieces[0], pieces[-1]
            entry = {"id": "", "company": company, "ats": "workday", "host": host, "site": name}
            if len(pieces) == 3:
                entry["tenant"] = pieces[1]
            elif len(pieces) != 2:
                raise SourceError(f"A Workday entry is workday:<host>/<site>:Company, not {item!r}")
            workday.site(entry)  # refuses anything but a Workday host and plain tenant and site names
            own = workday.HOST.match(host)
            tenant = entry.get("tenant") or (own.group(1) if own else "")
            entry["id"] = re.sub(r"[^a-z0-9]+", "-", tenant.lower()).strip("-")
            other = current.get(entry["id"])
            if other and (other.get("host"), other.get("site")) != (host, name):  # a second site of one tenant
                entry["id"] = re.sub(r"[^a-z0-9]+", "-", f"{tenant}-{name}".lower()).strip("-")
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


def probe(query: str, client: Client | None = None) -> dict[str, Any]:
    """Find a company's board: by its name, trying likely slugs, or by a careers page URL."""
    client = client or Client(retries=0, timeout=20)
    first, _, rest = query.strip().partition(" ")
    if first.lower().startswith(("https://", "http://")):
        return probe_url(first, rest.strip() or None, client)
    company = query
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
    for slug in slug_candidates(company):  # an unknown company answers with no postings, not an error
        found = _try({"system": "SmartRecruiters", "ats": "smartrecruiters", "slug": slug}, company, client)
        if found.get("postings"):
            matches.append({k: found[k] for k in ("ats", "slug", "postings", "sample", "add")})
    result: dict[str, Any] = {"company": company, "tried": slug_candidates(company), "matches": matches}
    if not matches:
        result["next"] = (
            "None of Greenhouse, Ashby, Lever, Recruitee or SmartRecruiters has a board under these names. "
            "Large employers "
            "mostly use other systems: find the company's careers page, or the link of one of its postings, "
            "and run `sources probe <url> <Company Name>`. It reads Workday sites and names the systems this "
            "version cannot read."
        )
    return result


def _system(url: str) -> dict[str, Any] | None:
    """The career system a URL belongs to, with what is needed to read it, or None if none is known."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = [segment for segment in parts.path.split("/") if segment]
    slug = None
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io"):
        kind = "greenhouse"
        slug = (parse_qs(parts.query).get("for") or [None])[0] if path[:2] == ["embed", "job_board"] else None
        slug = slug or (path[0] if path and path[0] != "embed" else None)
    elif host == "jobs.ashbyhq.com":
        kind, slug = "ashby", path[0] if path else None
    elif host == "jobs.lever.co":
        kind, slug = "lever", path[0] if path else None
    elif re.fullmatch(r"[a-z0-9-]+\.recruitee\.com", host):
        kind, slug = "recruitee", host.split(".")[0]
    elif host in ("jobs.smartrecruiters.com", "careers.smartrecruiters.com"):
        kind, slug = "smartrecruiters", path[0] if path else None
    elif workday.HOST.match(host):
        name = next((segment for segment in path if not _LOCALE.match(segment)), None)
        return {"system": "Workday", "ats": "workday", "host": host, "site": name}
    elif workday.SHARED_HOST.match(host):
        tenant, name = (path[1], path[2]) if len(path) >= 3 and path[0] == "recruiting" else (None, None)
        return {"system": "Workday", "ats": "workday", "host": host, "tenant": tenant, "site": name}
    elif _FORBIDDEN.search(host):
        return {
            "system": "LinkedIn or Indeed, whose terms forbid automated reading",
            "supported": False,
            "forbidden": True,
        }
    else:
        for pattern, system in UNSUPPORTED:
            if re.search(pattern, host):
                return {"system": system, "supported": False}
        return None
    return {"system": {"smartrecruiters": "SmartRecruiters"}.get(kind, kind.capitalize()), "ats": kind, "slug": slug}


def _try(found: dict[str, Any], company: str, client: Client) -> dict[str, Any]:
    """Read a supported board once, to say how many postings it has and how to add it."""
    if found.get("supported") is False:
        return found
    try:
        if found["ats"] == "workday":
            if not found.get("site"):
                return {
                    **{k: v for k, v in found.items() if v is not None},
                    "supported": True,
                    "error": "This Workday address has no site name. Open one posting on the careers site and probe "
                    "that posting's URL: the site name follows the host (…myworkdayjobs.com/<site>/job/…).",
                }
            source = {"id": "probe", "company": company, "ats": "workday", "host": found["host"], "site": found["site"]}
            if found.get("tenant"):
                source["tenant"] = found["tenant"]
            data = workday.search(client, workday.site(source)[2], "", limit=3)
            count, sample = data["total"], [row.get("title") for row in data["jobPostings"]]
            target = "/".join(p for p in (found["host"], found.get("tenant"), found["site"]) if p)
            add = f"workday:{target}:{company}"
            ranked = sorted(workday.categories(data).items(), key=lambda item: -item[1][2])
            listed = [{"name": name, "postings": n} for _, (_, _, n, name) in ranked[:15]]
            return {**{k: v for k, v in found.items() if v is not None}, "supported": True, "postings": count,
                    "sample": sample[:3], "add": add, "categories": listed}  # fmt: skip
        elif found["ats"] == "smartrecruiters":
            data = smartrecruiters.search(client, smartrecruiters.root({"slug": found["slug"]}), "", limit=3)
            if not data["totalFound"]:
                return {**found, "supported": True, "error": "No public postings under this company identifier"}
            rows = data["content"]
            slug = ((rows[0].get("company") or {}).get("identifier") if rows else None) or found["slug"]
            count, sample = data["totalFound"], [row.get("name") for row in rows]
            add = f"smartrecruiters:{slug}:{company}"
            return {**found, "slug": slug, "supported": True, "postings": count, "sample": sample[:3], "add": add}
        else:
            jobs = ats.fetch_board(
                client, {"id": "probe", "company": company, "ats": found["ats"], "slug": found["slug"]}
            )
            count, sample = len(jobs), [job.title for job in jobs[:3]]
            add = f"{found['ats']}:{found['slug']}:{company}"
    except Exception as error:  # a malformed link, a removed board, or a site that refuses
        return {**found, "supported": True, "error": f"{type(error).__name__}: {str(error)[:160]}"}
    return {**found, "supported": True, "postings": count, "sample": sample[:3], "add": add}


def probe_url(url: str, company: str | None, client: Client) -> dict[str, Any]:
    """What a careers URL is: a known system's board, or a company's own page linking to one."""
    name = company or "<Company Name>"
    found = _system(url)
    if found is not None:
        systems = [found]
    else:  # a company's own careers page: look for links to a known system on it, one request
        try:
            page = client.get(url).raise_for_status().text
        except Exception as error:
            return {
                "url": url,
                "matches": [],
                "unsupported": [],
                "next": f"The page could not be read: {type(error).__name__}",
            }
        systems, seen = [], set()
        for link in _LINK.findall(page):
            system = _system(link)
            if not system or system.get("forbidden"):  # a footer link says nothing about where postings are
                continue
            label = json.dumps(system, sort_keys=True)
            if label not in seen and len(systems) < 10:
                seen.add(label)
                systems.append(system)
    results = [_try(system, name, client) for system in systems]
    result: dict[str, Any] = {
        "url": url,
        "matches": [r for r in results if r.get("supported")],
        "unsupported": [r["system"] for r in results if r.get("supported") is False],
    }
    if company is None and result["matches"]:
        result["next"] = "Put the company's name after the URL, or in place of <Company Name> in the add line."
    elif not results:
        result["next"] = (
            "No link to a known career system on that page; its listings may load from a script. Open one "
            "posting and probe the URL of its page or its Apply link."
        )
    elif not result["matches"]:
        result["next"] = (
            f"This company uses {', '.join(result['unsupported'])}, which this version cannot read. "
            "There is no board to add."
        )
    return result


def hosts(root: Path | str) -> dict[str, Any]:
    """The hosts a tick contacts, for a network allowlist."""
    found = set()
    for source in read_json(Layout(root).sources, []):
        if source["ats"] in ats.ATS:
            found.add(urlsplit(ats.feed_url(source)).hostname)
        elif source["ats"] in workday.KINDS:
            found.add(source["host"])
        elif source["ats"] in smartrecruiters.KINDS:
            found.add(smartrecruiters.HOST)
        elif source["ats"] in lists.KINDS:
            found.update(lists.HOSTS)  # a list can lead to any board on the systems it reads
        elif source.get("provider") in PROVIDERS:
            found.add(urlsplit(PROVIDERS[source["provider"]][2]).hostname)
    return {"hosts": sorted(found)}


def test(root: Path | str, client: Client | None = None) -> dict[str, Any]:
    """Read every configured source once and report what each returned. Changes nothing."""
    client = client or Client(retries=0)
    layout = Layout(root)
    try:
        criteria = C.load(layout.criteria, C.Places.bundled())
    except C.CriteriaError:
        criteria = None  # searched sources then need their own queries
    report = {}
    for source in read_json(layout.sources, []):
        try:
            searched = S.scoped(source, criteria) if criteria else source
            if source.get("ats") in lists.KINDS:  # what became of each row, as no update reports it
                report[source["id"]] = lists.read(client, source)[1]
            else:
                report[source["id"]] = {"postings": len(S.fetch(client, searched))}
        except Exception as error:
            report[source["id"]] = {"error": f"{type(error).__name__}: {str(error)[:160]}"}
    failing = sorted(sid for sid, result in report.items() if "error" in result)
    return {"sources": report, "failing": failing}
