"""A small HTTPS client over urllib: bounded retries, per-host spacing, injectable transport."""

from __future__ import annotations

import gzip
import http.client
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Callable, Optional, Union

from . import __version__

USER_AGENT = f"automated-job-hunter/{__version__} (personal job search)"
MAX_BODY = 100 * 1024 * 1024
RETRY_STATUS = frozenset({500, 502, 503, 504})  # the server failed: asked again `retries` times
RATE_LIMITED = 429  # too many requests: asked again `rate_limit_retries` times
RATE_LIMIT_RETRIES = 7  # eight attempts in all, waiting 1, 2, 4, 8, 16, 30 and 30 s at the default backoff
MAX_WAIT = 30.0

# (method, url, headers, body, timeout) -> (status, lowercased headers, body)
# (status, headers, body), and optionally the URL the reply came from once redirects were followed.
Transport = Callable[
    [str, str, dict[str, str], Optional[bytes], float],
    Union[tuple[int, dict[str, str], bytes], tuple[int, dict[str, str], bytes, str]],
]


class HttpError(Exception):
    def __init__(self, status: int, url: str) -> None:
        super().__init__(f"HTTP {status} for {url}")
        self.status, self.url = status, url


class Response:
    """A reply. Its `url` is where it came from: after any redirects, not always the URL asked for."""

    def __init__(self, status: int, headers: Mapping[str, str], body: bytes, url: str) -> None:
        self.status, self.headers, self.body, self.url = status, headers, body, url

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.text)

    def raise_for_status(self) -> Response:
        # Redirects are followed by the transport, so a remaining 3xx is a refused redirect.
        if not 200 <= self.status < 300:
            raise HttpError(self.status, self.url)
        return self


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise urllib.error.HTTPError(newurl, code, "redirect to a non-https URL refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_HttpsOnlyRedirects)


def urllib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
) -> tuple[int, dict[str, str], bytes, str]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    landed = url
    try:
        with _OPENER.open(request, timeout=timeout) as reply:
            status, raw_headers, data = reply.status, reply.headers, reply.read(MAX_BODY + 1)
            landed = getattr(reply, "url", None) or url  # where the redirects, if any, ended
    except urllib.error.HTTPError as error:
        status, raw_headers, data = error.code, error.headers, error.read(MAX_BODY + 1)
    if len(data) > MAX_BODY:
        raise ValueError(f"Response from {url} exceeds {MAX_BODY} bytes")
    reply_headers = {k.lower(): v for k, v in (raw_headers.items() if raw_headers else [])}
    if reply_headers.get("content-encoding", "").lower() == "gzip":
        data = gzip.decompress(data)
    return status, reply_headers, data, landed


class Client:
    """Fetches https URLs only. Thread-safe; one instance can serve a thread pool.

    A request that met a 5xx or a network error is asked again while fewer than `retries` retries
    were made. One answered 429 (too many requests: wait and try again) is asked again while fewer
    than `rate_limit_retries` were, seven unless given. Each wait doubles from `backoff` up to
    MAX_WAIT, unless the reply's Retry-After says how long, still at most MAX_WAIT.

    A client built with `retries=0` asks once, as `sources test` and the probe build theirs to
    answer quickly. Its `rate_limit_retries` then defaults to 0, so it retries nothing, and a 429
    there does not mean that `collect` fails. Given explicitly, `rate_limit_retries` holds either way.
    """

    def __init__(
        self,
        transport: Transport | None = None,
        *,
        user_agent: str = USER_AGENT,
        timeout: float = 45.0,
        retries: int = 2,
        rate_limit_retries: int | None = None,
        backoff: float = 1.0,
        host_delay: Mapping[str, float] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.transport = transport or urllib_transport
        self.user_agent, self.timeout, self.retries, self.backoff = user_agent, timeout, retries, backoff
        if rate_limit_retries is None:
            rate_limit_retries = RATE_LIMIT_RETRIES if retries > 0 else 0
        self.rate_limit_retries = rate_limit_retries
        self.host_delay = dict(host_delay or {})
        self.sleep, self.clock = sleep, clock
        self._lock = threading.Lock()
        self._next: dict[str, float] = {}
        self._local = threading.local()  # the tallies open in each thread (`tally`)
        self.requests = 0
        self.rate_limited: dict[str, int] = {}  # 429 retries made, by host

    def space(self, host: str, seconds: float) -> None:
        """Keep at least `seconds` between requests to `host`: a source's documented or polite rate."""
        with self._lock:
            self.host_delay[host] = max(self.host_delay.get(host, 0.0), seconds)

    def _pace(self, host: str) -> None:
        delay = self.host_delay.get(host, 0.0)
        if delay <= 0:
            return
        with self._lock:
            now = self.clock()
            ready = max(now, self._next.get(host, 0.0))
            self._next[host] = ready + delay
        if ready > now:
            self.sleep(ready - now)

    def _wait(self, attempt: int, retry_after: str | None) -> float:
        try:
            return min(MAX_WAIT, max(0.0, float(retry_after)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return min(MAX_WAIT, self.backoff * (2**attempt))

    @contextmanager
    def tally(self) -> Iterator[dict[str, int]]:
        """Count, by host, the 429 retries this thread makes until the block ends.

        `collect` reads each source in one thread of its pool, start to end, so the count is that
        source's alone, even while another source on the same host is read. Requests a reader made
        from threads of its own would not be counted; none does.
        """
        counts: dict[str, int] = {}
        if not hasattr(self._local, "tallies"):
            self._local.tallies = []
        self._local.tallies.append(counts)
        try:
            yield counts
        finally:
            self._local.tallies.pop()

    def _refused(self, host: str) -> None:
        """Count one 429 retry: in the client's total for the host and in this thread's tallies."""
        with self._lock:
            self.rate_limited[host] = self.rate_limited.get(host, 0) + 1
        for counts in getattr(self._local, "tallies", ()):
            counts[host] = counts.get(host, 0) + 1

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> Response:
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise ValueError(f"Only https URLs are fetched, not {url!r}")
        send = {"User-Agent": self.user_agent, "Accept": "application/json,text/html;q=0.9", "Accept-Encoding": "gzip"}
        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            send["Content-Type"] = "application/json"
        send.update(headers or {})
        # `attempt` is the number of retries made so far, of either kind
        for attempt in range(max(0, self.retries, self.rate_limit_retries) + 1):
            self._pace(parts.hostname)
            with self._lock:
                self.requests += 1
            try:
                status, reply_headers, data, *landed = self.transport(method, url, send, body, self.timeout)
            except (OSError, http.client.HTTPException):
                if attempt >= self.retries:
                    raise
                self.sleep(self._wait(attempt, None))
                continue
            if status == RATE_LIMITED and attempt < self.rate_limit_retries:
                # The line not to cross: a 429 is asked again after longer waits, with the same
                # identity and at the same pace (`_pace` spaces every attempt like a first one), and
                # once the retries are used up it goes back to the caller, so the update fails
                # cleanly. Never change the User-Agent, the address or anything else to get past a
                # refusal.
                self._refused(parts.hostname)
                self.sleep(self._wait(attempt, reply_headers.get("retry-after")))
                continue
            if status in RETRY_STATUS and attempt < self.retries:
                self.sleep(self._wait(attempt, reply_headers.get("retry-after")))
                continue
            return Response(status, reply_headers, data, landed[0] if landed else url)
        raise AssertionError("unreachable")

    def get(self, url: str, **kwargs: Any) -> Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Response:
        return self.request("POST", url, **kwargs)
