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
from .sources import ats, eightfold, lists, radancy, smartrecruiters, successfactors, workday
from .sources.aggregators import PROVIDERS
from .state import read_json, write_text

# Career systems this version cannot read, by the hosts their public pages are on. A probe names
# them, so that nobody keeps looking for a board that no reader exists for.
UNSUPPORTED = (
    # Oracle Fusion's hosts, <tenant>.fa.<region>.oraclecloud.com with or without the region. Oracle Cloud
    # serves much else under oraclecloud.com, such as files in object storage that a careers page shows.
    (
        r"(^|\.)fa(\.[a-z0-9-]+)*\.oraclecloud\.com$",
        "Oracle Recruiting Cloud (its job APIs are only for Oracle and approved partners)",
    ),
    (r"(^|\.)taleo\.net$", "Oracle Taleo"),
    (r"(^|\.)icims\.com$", "iCIMS (its terms forbid automated reading of its career sites)"),
    (r"(^|\.)(successfactors|sapsf)\.(com|eu)$", "SAP SuccessFactors on SAP's own hosts (closed by robots.txt)"),
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
        elif len(parts) == 3 and parts[0] in eightfold.KINDS:
            _, target, company = parts
            host, _, domain = target.partition("/")  # the site's host, then the domain its pages name
            entry = {"id": "", "company": company, "ats": "eightfold", "host": host.lower(), "domain": domain.lower()}
            eightfold.site(entry)  # refuses anything but a plain host name and domain name
            entry["id"] = re.sub(r"[^a-z0-9]+", "-", entry["domain"].split(".")[0]).strip("-")
            other, same = current.get(entry["id"]), ("ats", "host", "domain")
            if other and [other.get(k) for k in same] != [entry[k] for k in same]:  # another board has the name
                entry["id"] += "-eightfold"
        elif len(parts) == 3 and parts[0] in successfactors.KINDS:
            _, target, company = parts
            host, _, brand = target.partition("/")  # host, or host/brand for a brand's site under its own path
            entry = {"id": "", "company": company, "ats": "successfactors", "host": host.lower()}
            if brand:
                entry["brand"] = brand
            successfactors.site(entry)  # refuses anything but a plain host name and brand, and SAP's own hosts
            entry["id"] = successfactors.source_id(entry["host"], brand)
            other = current.get(entry["id"])
            if other and (other.get("host"), other.get("brand")) != (entry["host"], entry.get("brand")):  # another site
                entry["id"] = re.sub(r"[^a-z0-9]+", "-", f"{host}-{brand}".lower()).strip("-")
        elif len(parts) == 3 and parts[0] in radancy.KINDS:
            _, host, company = parts
            entry = {"id": "", "company": company, "ats": "radancy", "host": host.lower()}
            radancy.site(entry)  # refuses anything but a plain host name
            entry["id"] = successfactors.source_id(entry["host"])  # the first label of the host naming the employer
            other, same = current.get(entry["id"]), ("ats", "host")
            if other and [other.get(k) for k in same] != [entry[k] for k in same]:  # another board has the name
                entry["id"] += "-radancy"
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
        if first[:7].lower() == "http://":  # only https is read, and a careers site serves it too
            first = "https://" + first[7:]
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
    elif eightfold.HOST.match(host) and path[:1] == ["careers"]:  # not the vendor's own pages that sites link to
        domain = (parse_qs(parts.query).get("domain") or [None])[0]
        return {"system": "Eightfold", "ats": "eightfold", "host": host, "domain": domain}
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
        elif found["ats"] == "eightfold":
            client.space(found["host"], eightfold.DELAY)
            named = found.get("domain") or eightfold.page_domain(
                client.get(f"https://{found['host']}/careers").raise_for_status().text
            )  # the site's pages name its domain, which its search needs
            if not named:
                error = "No Eightfold career site answers at this address: probe the careers page itself"
                return {**{k: v for k, v in found.items() if v is not None}, "supported": True, "error": error}
            host, domain = eightfold.site({"host": found["host"], "domain": named})
            data = eightfold.search(client, host, domain, "")
            count, sample = data["count"], [row.get("name") for row in data["positions"]]
            return {**found, "domain": domain, "supported": True, "postings": count, "sample": sample[:3],
                    "add": f"eightfold:{host}/{domain}:{company}"}  # fmt: skip
        elif found["ats"] == "successfactors":
            facts = {k: v for k, v in found.items() if v is not None}
            _, base = successfactors.site(found)
            client.sleep(successfactors.DELAY)  # the site's own page was read from this host a moment ago
            try:
                data = successfactors.search(client, base, "")
            except successfactors.ScriptListing:
                return {**facts, "system": successfactors.SCRIPTED, "supported": False}
            sample = [" ".join(row.get("title", "").split()) for row in data.rows[:3]]
            add = f"successfactors:{'/'.join(p for p in (found['host'], found.get('brand')) if p)}:{company}"
            return {**facts, "supported": True, "postings": data.total, "sample": sample, "add": add}
        elif found["ats"] == "radancy":
            host = radancy.site(found)
            client.space(host, radancy.DELAY)
            client.sleep(radancy.DELAY)  # the site's own page was read from this host a moment ago
            postings = radancy.listing(client, host)  # robots.txt first, then the sitemap: no posting page
            sample = [posting.title for posting in postings[:3]]
            return {**found, "supported": True, "postings": len(postings), "sample": sample,
                    "add": f"radancy:{host}:{company}"}  # fmt: skip
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
    found, landed, page = _system(url), url, ""
    if found is None:  # a company's own careers page: look for links to a known system on it, one request
        try:
            reply = client.get(url).raise_for_status()
        except Exception as error:  # a timeout, a refusal, or a bot filter's 403
            return {
                "url": url,
                "matches": [],
                "unsupported": [],
                "next": (
                    f"The page could not be read: {type(error).__name__}. Trying it again seldom helps when a site "
                    "refuses scripted requests. Open one of the company's postings instead (a job site's Apply button "
                    "often leads to one) and probe the URL of its page or its Apply link, or probe the company's "
                    "name, which tries Greenhouse, Ashby, Lever, Recruitee and SmartRecruiters."
                ),
            }
        page, landed = reply.text, reply.url  # an old address may move to the site's own host
        found = _system(landed)
    if found is not None:
        systems = [found]
    else:
        systems, seen = [], set()
        named = eightfold.page_domain(page)
        if named:  # an Eightfold career site itself, on the company's own host or eightfold.ai
            host = urlsplit(landed).hostname
            systems.append({"system": "Eightfold", "ats": "eightfold", "host": host, "domain": named})
        for link in _LINK.findall(page):
            system = _system(link)
            if not system or system.get("forbidden"):  # a footer link says nothing about where postings are
                continue
            if named and system.get("ats") == "eightfold":  # the site is the board, not the ones it links to
                continue
            label = json.dumps(system, sort_keys=True)
            if label not in seen and len(systems) < 10:
                seen.add(label)
                systems.append(system)
        site = successfactors.recognize(landed, page)  # the page is itself a SuccessFactors career site: its links
        if site:  # to SAP's hosts are its own scripts and sign-in, not another board
            systems = [site]
        site = radancy.recognize(landed, page)  # the page is itself a TalentBrew career site: its links to other
        if site:  # systems are where its postings are applied for, which its own sitemap already lists
            systems = [site]
    results = [_try(system, name, client) for system in systems]
    result: dict[str, Any] = {"url": url}
    if landed != url:  # the page moved: what was read is there
        result["resolved"] = landed
    result["matches"] = [r for r in results if r.get("supported")]
    result["unsupported"] = [r["system"] for r in results if r.get("supported") is False]
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
    found, unlisted = set(), {}
    own = S.load_own(root)  # the feed's own readers, which name their hosts themselves
    for source in read_json(Layout(root).sources, []):
        if source["ats"] in ats.ATS:
            found.add(urlsplit(ats.feed_url(source)).hostname)
        elif source["ats"] in workday.KINDS:
            found.add(source["host"])
        elif source["ats"] in smartrecruiters.KINDS:
            found.add(smartrecruiters.HOST)
        elif source["ats"] in eightfold.KINDS:
            found.add(eightfold.site(source)[0])
        elif source["ats"] in successfactors.KINDS:
            found.add(source["host"])
        elif source["ats"] in radancy.KINDS:
            found.add(radancy.site(source))
        elif source["ats"] in lists.KINDS:
            found.update(lists.HOSTS)  # a list can lead to any board on the systems it reads
        elif source.get("provider") in PROVIDERS:
            found.add(urlsplit(PROVIDERS[source["provider"]][2]).hostname)
        elif own.claims(source["ats"]):  # a reader in the feed's readers/ names its own hosts
            try:
                found.update(own.hosts(source))
            except SourceError as error:
                unlisted[source["id"]] = str(error)
    return {"hosts": sorted(found), **({"unlisted": unlisted} if unlisted else {})}


def test(root: Path | str, client: Client | None = None) -> dict[str, Any]:
    """Read every configured source once and report what each returned. Changes nothing."""
    client = client or Client(retries=0)
    layout = Layout(root)
    own = S.load_own(layout.root)
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
    return {"sources": report, "failing": failing, **({"readers": own.report()} if own.found else {})}
