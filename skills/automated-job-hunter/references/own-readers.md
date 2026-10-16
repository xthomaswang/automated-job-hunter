# A feed's own readers

The engine reads every source in `config/sources.json` with a reader built into it. A career site
that no built-in reader reads, such as one employer's own site, can still be a source: the feed repo
can carry a reader for it in a `readers/` folder at its root. The engine loads that reader and
treats it like a built-in one. The feed repo is private, so the reader, and the employer it names,
never appear anywhere public.

**A reader is code, and it runs with the workflow's permissions.** During `collect` it runs inside
the engine with everything the engine has there: the network, the checked-out feed repo, and in the
scheduled workflow whatever the job is allowed to do. Review a reader as you would any code given
that access, and keep it to the same rules as the built-in readers:

- **https only.** The client the engine passes in refuses anything else; use it for every request.
- **A complete snapshot, or an error.** Return every posting the site lists, or raise
  `SourceError`. Never return part of a board: a posting missing from a snapshot counts as closed,
  so a partial read during an outage would close live postings.
- **No model calls.** The engine never calls a model, and a reader is part of the engine.
- **The site's terms and robots.txt.** Read only pages they allow, at a polite rate
  (`client.space(host, seconds)` spaces requests to one host). If the terms forbid automated
  reading, the site is not a source.

Readers are loaded only by the commands that read sources: `collect`, `sources test` and
`sources hosts` (and `status`, to report on them). They are never loaded while postings are judged:
the judging step runs no code from the feed, so its limits (no shell, no network, writes only to
the results folder) are unchanged.

## The contract

A module `readers/<name>.py` provides:

| Name | | What it is |
|---|---|---|
| `KIND` | required | The source type it reads, a string: a lowercase letter, then up to 39 lowercase letters, digits or hyphens, such as `acme-careers`. It must not be a built-in type (`greenhouse`, `workday`, ...) or another module's. |
| `fetch(client, source, rank=None)` | required | Returns a `list` of `Job`, every posting of the site, or raises `SourceError`. `client` is the engine's `Client`, `source` the entry from `config/sources.json`. When `rank` is given, fetch postings' text in rank order within a budget and return the rest with `detail_skipped=True`; `lazy.complete` does exactly that. |
| `QUERY_SCOPED` | optional, default `False` | `True` for a site read through its own search: an entry without `queries` then gets the wanted and related roles from `profile/criteria.json`, as for Workday. |
| `DETAILED` | optional, default `QUERY_SCOPED` | `True` for a site whose listing has no text, so that each text is one more request. `collect` then passes a rank function, and holds each posting it first sees to `backfill.days`, as for the built-in detail sources. Without it, `rank` is always `None` and every posting must come with its text. |
| `HOSTS`, or `hosts(source)` | optional | The hosts its sources contact: a tuple of host names, or a function returning them for one entry. `sources hosts` lists them for a network allowlist; without them it names the source under `unlisted` instead. |

A source uses the type with `"ats"` and whatever other fields the module reads:

```json
{"id": "acme", "ats": "acme-careers", "company": "Acme"}
```

The engine checks only `id` and `ats` on such an entry; the module checks the rest itself, and
raises `SourceError` for an entry it cannot read. The fields every source has work as usual:
`enabled`, `min_fetch_interval_seconds`, `membership_mode`, and for a searched or detailed reader
`queries`, `categories` and `max_details` (read the last with `lazy.budget`).

Each `Job` must belong to the entry it was read for (`source` is the entry's `id`, and `key` is
`<id>:<source_id>`), and each `source_id` may appear once. A reader that returns anything else
fails its source, as an error would.

### What a reader may import

The engine runs as `.hunter/engine/hunter.py`, which makes its package `ajh` importable. These names
are the supported surface; anything else in the engine can change in any version:

- `ajh.model`: `Job`, `SourceError`, `public_https`
- `ajh.http`: `Client`, `HttpError`
- `ajh.sources`: `lazy` (`lazy.complete`, `lazy.budget`), and the ranks `PENDING`, `NEW`, `CHANGED`,
  `LISTED`
- `ajh.textutil`: `html_to_text`, `words_key`

Use only the Python standard library besides these: the engine runs with nothing installed.

## A skeleton

A reader for Acme's own career site (a made-up site), whose listing gives every posting's facts but
not its text:

```python
"""Acme's own career site: one listing with every posting's facts, then one request per text."""

from __future__ import annotations

from dataclasses import replace

from ajh.http import HttpError
from ajh.model import Job, SourceError
from ajh.sources import lazy
from ajh.textutil import html_to_text

KIND = "acme-careers"
DETAILED = True  # the listing has no text, so collect passes a rank function
HOSTS = ("careers.acme.example",)

SITE = "https://careers.acme.example"
DETAILS = 50  # texts fetched per update, unless the entry sets max_details


def _posting(source, row):
    """A posting as the listing shows it: everything but its text."""
    jid = str(row.get("id", "")) if isinstance(row, dict) else ""
    if not jid.isalnum():
        raise SourceError("An Acme listing row has no plain id")
    return Job(
        key=f"{source['id']}:{jid}",
        source=source["id"],
        source_id=jid,
        company=source["company"],
        title=row.get("title") or "",  # Job refuses an empty title, which fails the whole source
        location=row.get("location") or "unknown",
        url=f"{SITE}/jobs/{jid}",
        posted_at=row.get("posted"),
    )


def _with_text(client, job):
    """The posting with its text, or None to try again on a later update."""
    try:
        data = client.get(f"{SITE}/api/jobs/{job.source_id}").raise_for_status().json()
    except (HttpError, OSError, ValueError):
        return None
    text = html_to_text(data.get("description")) if isinstance(data, dict) else ""
    return replace(job, description=text) if text else None


def fetch(client, source, rank=None):
    data = client.get(f"{SITE}/api/jobs").raise_for_status().json()
    rows = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(rows, list) or data.get("total") != len(rows):
        raise SourceError("The Acme listing is not complete")  # never return part of a board
    listed = {}
    for row in rows:
        job = _posting(source, row)
        listed[job.source_id] = (job, None)
    return lazy.complete(listed, rank, lazy.budget(source, DETAILS), lambda job, _: _with_text(client, job))
```

A site whose listing already carries every posting's text needs no `DETAILED`, no `lazy` and no
second request: build each `Job` with its `description` and return the list.

## How the engine loads them

- Every `*.py` file directly in `readers/` is loaded, in order of file name. Files whose names start
  with `_` or `.` are skipped, so a draft can wait as `_acme.py`. Other files and folders are left
  alone.
- Each file is loaded once per process, by its path, as module `ajh_own_readers.<name>`. The folder
  is never put on Python's path, so a file named like a standard module (`readers/json.py`) hides
  nothing, and one reader cannot import another file of the folder: keep each reader in one file.
- Keep loading free of side effects: do all requests in `fetch`. Print nothing: every engine command
  prints one JSON object, and a reader's output would corrupt it.
- A file that fails to load, has no `KIND` or no `fetch`, or declares an invalid type, a built-in
  type or a type another file declares too, is not used. The sources of that type fail with an
  error naming the file and the reason, such as `readers/acme.py has no fetch function`. A built-in
  type declared by a file fails the built-in sources of that type too, until the file is renamed or
  fixed. A file that fails before its `KIND` is known may be the one for any type nothing else
  reads, so each source of such a type fails with that file's error. Every other source reads as
  before, and a source that fails keeps its last snapshot: nothing closes because a reader broke.
- `status` and `sources test` list the types the readers provide under `readers`, and every file not
  used with the reason.

## Trying one

1. Write `readers/<name>.py` and add an entry to `config/sources.json`.
2. Run `python3 .hunter/engine/hunter.py sources test`. It reads every source once, the new one
   included, and changes nothing. A detailed reader fetches no text there, only the listing, so the
   count is the number of postings the site lists.
3. Run `sources hosts`, and check that it lists every host the reader contacts.
4. Commit `readers/` and the entry together. A full update refuses to publish from uncommitted
   files, and a push that changes `readers/` starts the scheduled workflow, as one that changes
   `profile/` or `config/` does.

`hunter upgrade` replaces the engine and its own files only; it never touches `readers/`. A reader
that uses only the names listed above keeps working across engine versions.
