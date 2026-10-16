"""robots.txt rules, read as RFC 9309 says, the same on every Python version.

`urllib.robotparser` is not used, because its answer depends on the Python version. Python 3.9's
applies the first matching rule, not the longest; reads `*` and `$` as plain characters; and rewrites
a rule's path through urlparse and urlunparse, so `Disallow: /?` (the root with a query string)
becomes `Disallow: /`, which blocks the whole site. Fetching the file is the caller's: what its
status means is in RFC 9309, section 2.3.1.
"""

from __future__ import annotations

import re
from functools import lru_cache
from urllib.parse import quote

_ESCAPED = re.compile(r"%[0-9A-Fa-f]{2}")
_TOKEN = re.compile(r"[A-Za-z_-]*")  # the characters a product token may have


def allowed(robots_txt: str, agent: str, target: str) -> bool:
    """Whether a robots.txt lets `agent` fetch `target`: the path and `?query` exactly as they will be sent.

    `agent` is the User-Agent the request carries; its product token is the part before the first `/`.
    The rules are those of every group naming that token (in any case), or else of every group for
    `*`. Of the rules whose pattern matches the start of the target, the longest wins, and Allow wins
    a tie; `*` in a pattern matches any run of characters and a final `$` the end. An empty pattern
    matches nothing, and a target no rule matches is allowed.
    """
    if target == "/robots.txt":  # always allowed (RFC 9309, 2.2.2)
        return True
    token = agent.split("/", 1)[0].strip().lower() or "*"
    path = _normal(target)
    best = (0, True)  # (length, allows) of the longest matching rule so far: none allows
    for length, allows, pattern in _rules(robots_txt, token):
        if (length, allows) > best and _matches(pattern, path):
            best = (length, allows)
    return best[1]


def _agent(value: str) -> str:
    """The agent a User-agent line names: `*`, or its product token (its letters, `_` and `-`) in lower case."""
    first = value.split()[0] if value.split() else ""
    return "*" if first == "*" else _TOKEN.match(first).group().lower()


def _normal(text: str) -> str:
    """Text as patterns and targets are compared: any character outside ASCII percent-encoded as UTF-8,
    and every escape in capitals, so `%e4`, `%E4` and the raw character compare alike."""
    text = _ESCAPED.sub(lambda escape: escape.group().upper(), text)
    return "".join(c if c.isascii() else quote(c, safe="", errors="replace") for c in text)


@lru_cache(maxsize=16)
def _rules(robots_txt: str, token: str) -> tuple[tuple[int, bool, str], ...]:
    """(length, allows, pattern) of every rule for `token`: those of the groups naming it, else of the
    groups for `*`, merged. Consecutive User-agent lines form one group; one after a rule starts the
    next. Comments, unknown lines and Sitemap lines are ignored."""
    groups: list[tuple[set[str], list[tuple[bool, str]]]] = []
    for raw in robots_txt.lstrip("﻿").splitlines():
        field, colon, value = raw.split("#", 1)[0].partition(":")
        field, value = field.strip().lower(), value.strip()
        if not colon:
            continue
        if field == "user-agent":
            if not groups or groups[-1][1]:
                groups.append((set(), []))
            groups[-1][0].add(_agent(value))
        elif field in ("allow", "disallow") and groups:
            groups[-1][1].append((field == "allow", value))
    chosen = [rules for agents, rules in groups if token in agents]
    chosen = chosen or [rules for agents, rules in groups if "*" in agents]
    patterns = [(allows, _normal(value)) for rules in chosen for allows, value in rules if value]
    return tuple((len(pattern), allows, pattern) for allows, pattern in patterns)


def _matches(pattern: str, path: str) -> bool:
    """Whether a pattern matches the start of a path, `*` being any run of characters and a final `$`
    the path's end. Each piece between wildcards is found at its first place after the one before,
    which leaves the most room for the rest: linear time, unlike a backtracking regular expression."""
    anchored = pattern.endswith("$")
    pieces = (pattern[:-1] if anchored else pattern).split("*")
    if not path.startswith(pieces[0]):
        return False
    at = len(pieces[0])
    if len(pieces) == 1:
        return not anchored or at == len(path)
    for piece in pieces[1:-1]:
        found = path.find(piece, at)
        if found < 0:
            return False
        at = found + len(piece)
    last = pieces[-1]
    if anchored:
        return path.endswith(last) and len(path) - len(last) >= at
    return path.find(last, at) >= 0
