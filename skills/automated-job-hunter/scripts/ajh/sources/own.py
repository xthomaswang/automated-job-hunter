"""A feed's own readers: reader modules kept in the feed repo's `readers/` folder.

A feed repo is private, so it can carry a reader for a career site that only its owner needs, or
whose employer the owner would rather not name in a public repo. Each module `readers/<name>.py`
declares a source type, which an entry in `config/sources.json` names as `"ats": "<KIND>"`, and
reads it exactly as a built-in reader does (see `sources.fetch`):

- `KIND` (required): a lowercase letter, then up to 39 lowercase letters, digits or hyphens. Not a
  built-in type, and no other module's.
- `fetch(client, source, rank=None)` (required): every posting, or `SourceError`. When `rank` is
  given, text in rank order within a budget and the rest `detail_skipped` (`lazy.complete`).
- `QUERY_SCOPED` (default False): an entry without `queries` gets the wanted and related roles.
- `DETAILED` (default `QUERY_SCOPED`): `collect` passes a rank function, and holds each posting it
  first sees to the backfill limit, as for the built-in detail sources.
- `HOSTS`, or `hosts(source)`: what `sources hosts` lists for its sources.

The supported imports are `ajh.model` (`Job`, `SourceError`, `public_https`), `ajh.http` (`Client`,
`HttpError`), `ajh.sources` (`lazy`, and the ranks `PENDING`, `NEW`, `CHANGED`, `LISTED`) and
`ajh.textutil` (`html_to_text`, `words_key`). `references/own-readers.md` is the guide for writing one.

Only the commands that read sources load them (`collect`, `sources test`, `sources hosts`, and
`status` to report on them): never on import, and never while postings are judged. Each file runs
once per process, loaded by its path. The folder is never put on `sys.path`, so a file such as
`readers/json.py` cannot hide a standard module. A module that fails to load, or declares a type it
may not, is not used, and the sources of that type fail with the file and the reason.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from ..http import Client
from ..layout import Layout
from ..model import Job, SourceError

PACKAGE = "ajh_own_readers"  # readers/acme.py runs as module ajh_own_readers.acme
_KIND = re.compile(r"[a-z][a-z0-9-]{0,39}")


@dataclass(frozen=True)
class Reader:
    """A module that passed every check, so sources of its type are read with it."""

    file: str  # readers/<name>.py
    module: ModuleType
    query_scoped: bool
    detailed: bool

    def hosts(self, source: Mapping[str, Any]) -> list[str]:
        named = getattr(self.module, "hosts", None)
        more = named(source) if named else ()
        found = [*getattr(self.module, "HOSTS", ()), *([more] if isinstance(more, str) else more)]
        if not all(isinstance(host, str) and host for host in found):
            raise SourceError(f"{self.file} names a host that is not a host name")
        if not found:  # an allowlist built from the list would leave this source's hosts out
            raise SourceError(f"{self.file} names no hosts: set HOSTS, or a hosts(source) function")
        return found


@dataclass
class Loaded:
    """What one feed's `readers/` folder provides, and what it could not."""

    found: bool = False  # the feed has a readers/ folder
    builtin: tuple[str, ...] = ()
    readers: dict[str, Reader] = field(default_factory=dict)  # by type
    errors: dict[str, str] = field(default_factory=dict)  # readers/<name>.py -> why it is not used
    refused: dict[str, str] = field(default_factory=dict)  # type -> why its sources cannot be read
    unclaimed: list[str] = field(default_factory=list)  # modules that failed before naming a type

    def problem(self, kind: Any) -> str | None:
        """Why a source of this type cannot be read because of the feed's readers, or None.

        A module that failed to load, or has no `KIND`, may be the one a type nobody else reads
        belongs to, so such a source fails with every one of those errors.
        """
        if not isinstance(kind, str):
            return None
        if kind in self.refused:
            return self.refused[kind]
        if kind in self.readers or kind in self.builtin or not self.unclaimed:
            return None
        return f"No reader for source type {kind!r}; " + "; ".join(self.unclaimed)

    def claims(self, kind: Any) -> bool:
        """Whether sources of this type are this feed's own readers' to read, or to fail."""
        return (isinstance(kind, str) and kind in self.readers) or self.problem(kind) is not None

    def query_scoped(self, kind: Any) -> bool:
        reader = self.readers.get(kind) if isinstance(kind, str) else None
        return bool(reader and reader.query_scoped)

    def detailed(self, kind: Any) -> bool:
        reader = self.readers.get(kind) if isinstance(kind, str) else None
        return bool(reader and reader.detailed)

    def fetch(self, client: Client, source: Mapping[str, Any], rank: Callable[[Job], int | None] | None) -> list[Job]:
        """A source's postings from its reader. A rank goes only to a detail reader, as `collect` intends."""
        kind = source.get("ats")
        problem = self.problem(kind)
        if problem:
            raise SourceError(problem)
        reader = self.readers[kind]
        jobs = reader.module.fetch(client, source, rank if reader.detailed else None)
        return _checked(jobs, source, reader.file)

    def hosts(self, source: Mapping[str, Any]) -> list[str]:
        problem = self.problem(source.get("ats"))
        if problem:
            raise SourceError(problem)
        try:
            return self.readers[source["ats"]].hosts(source)
        except SourceError:
            raise
        except Exception as error:  # the module's own `hosts` failed on this entry
            raise SourceError(f"{self.readers[source['ats']].file}: hosts() failed: {_said(error)}") from error

    def report(self) -> dict[str, Any]:
        """For `status` and `sources test`: the types the feed's readers provide, and the files not used."""
        return {
            "kinds": {kind: reader.file for kind, reader in sorted(self.readers.items())},
            "errors": dict(sorted(self.errors.items())),
        }


_loaded: dict[Path, Loaded] = {}
current = Loaded()  # the feed whose readers this process reads with; none until `load`


def load(root: Path | str, builtin: Iterable[str]) -> Loaded:
    """Load a feed's own readers, once per process, and read its sources with them from now on."""
    global current
    folder = Layout(root).readers
    if folder not in _loaded:
        _loaded[folder] = _load(folder, tuple(builtin))
    current = _loaded[folder]
    return current


def _said(error: BaseException) -> str:
    return f"{type(error).__name__}: {' '.join(str(error).split())}"[:160]


def _import(path: Path) -> ModuleType:
    """Run one file as module ajh_own_readers.<name>, by its path: its folder never goes on sys.path."""
    name = f"{PACKAGE}.{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"{path.name} cannot be loaded as a module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # before it runs: a dataclass in it looks its module up there
    written = sys.dont_write_bytecode
    sys.dont_write_bytecode = True  # no __pycache__ in the feed repo, which `apply` wants pristine
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if sys.modules.get(name) is module:
            del sys.modules[name]
        raise
    finally:
        sys.dont_write_bytecode = written
    return module


def _wrong(module: ModuleType, kind: str, builtin: tuple[str, ...]) -> str | None:
    """Why a module that declares this type cannot read it, or None."""
    if not _KIND.fullmatch(kind):
        return (
            f"declares KIND {kind!r}, which is not a lowercase letter followed by up to 39 lowercase letters, "
            "digits or hyphens"
        )
    if kind in builtin:
        return f"declares KIND {kind!r}, a built-in source type; give the reader a type of its own"
    if not callable(getattr(module, "fetch", None)):
        return "has no fetch function"
    for flag in ("QUERY_SCOPED", "DETAILED"):
        if not isinstance(getattr(module, flag, False), bool):
            return f"sets {flag} to something other than True or False"
    hosts = getattr(module, "HOSTS", ())
    if not isinstance(hosts, (tuple, list)) or not all(isinstance(host, str) and host for host in hosts):
        return "sets HOSTS to something other than a tuple of host names"
    if hasattr(module, "hosts") and not callable(module.hosts):
        return "sets hosts to something other than a function"
    return None


def _load(folder: Path, builtin: tuple[str, ...]) -> Loaded:
    loaded = Loaded(found=folder.is_dir(), builtin=builtin)
    if not loaded.found:
        return loaded
    files = sorted(p for p in folder.iterdir() if p.suffix == ".py" and not p.name.startswith(("_", ".")))
    declared: dict[str, list[tuple[str, ModuleType]]] = {}
    for path in files:
        if not path.is_file():
            continue
        name = f"{folder.name}/{path.name}"
        try:
            module = _import(path)
        except (Exception, SystemExit) as error:  # its sources fail; every other source reads as before
            loaded.errors[name] = f"failed to load: {_said(error)}"
        else:
            kind = getattr(module, "KIND", None)
            if isinstance(kind, str):
                declared.setdefault(kind, []).append((name, module))
                continue
            loaded.errors[name] = "has no KIND" if kind is None else "has a KIND that is not a string"
        loaded.unclaimed.append(f"{name} {loaded.errors[name]}")
    for kind, modules in declared.items():
        names = [name for name, _ in modules]
        if len(names) > 1:  # which one a source meant cannot be told, so neither is used
            for name in names:
                others = " and ".join(other for other in names if other != name)
                loaded.errors[name] = f"declares KIND {kind!r}, as {others} also does"
            every = "both" if len(names) == 2 else "all"
            loaded.refused[kind] = f"{', '.join(names[:-1])} and {names[-1]} {every} declare KIND {kind!r}"
            continue
        (name, module), why = modules[0], _wrong(modules[0][1], kind, builtin)
        if why:
            loaded.errors[name] = why
            loaded.refused[kind] = f"{name} {why}"
            continue
        scoped = getattr(module, "QUERY_SCOPED", False)
        loaded.readers[kind] = Reader(name, module, scoped, getattr(module, "DETAILED", scoped))
    return loaded


def _checked(jobs: Any, source: Mapping[str, Any], file: str) -> list[Job]:
    """A reader's postings, refused whole unless each is one posting of this source, listed once."""
    if not isinstance(jobs, (list, tuple)):
        raise SourceError(f"{file} returned {type(jobs).__name__}, not a list of postings")
    ids: set[str] = set()
    for job in jobs:
        if not isinstance(job, Job) or job.source != source.get("id"):
            raise SourceError(f"{file} returned something other than a posting of source {source.get('id')!r}")
        if job.source_id in ids:
            raise SourceError(f"{file} returned posting {job.source_id!r} twice")
        ids.add(job.source_id)
    return list(jobs)
