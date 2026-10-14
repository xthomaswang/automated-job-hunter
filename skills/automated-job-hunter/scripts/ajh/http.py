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
from collections.abc import Mapping
from typing import Any, Callable, Optional

from . import __version__

USER_AGENT = f"automated-job-hunter/{__version__} (personal job search)"
MAX_BODY = 100 * 1024 * 1024
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_WAIT = 30.0

# (method, url, headers, body, timeout) -> (status, lowercased headers, body)
Transport = Callable[[str, str, dict[str, str], Optional[bytes], float], tuple[int, dict[str, str], bytes]]


class HttpError(Exception):
    def __init__(self, status: int, url: str) -> None:
        super().__init__(f"HTTP {status} for {url}")
        self.status, self.url = status, url


class Response:
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
) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with _OPENER.open(request, timeout=timeout) as reply:
            status, raw_headers, data = reply.status, reply.headers, reply.read(MAX_BODY + 1)
    except urllib.error.HTTPError as error:
        status, raw_headers, data = error.code, error.headers, error.read(MAX_BODY + 1)
    if len(data) > MAX_BODY:
        raise ValueError(f"Response from {url} exceeds {MAX_BODY} bytes")
    reply_headers = {k.lower(): v for k, v in (raw_headers.items() if raw_headers else [])}
    if reply_headers.get("content-encoding", "").lower() == "gzip":
        data = gzip.decompress(data)
    return status, reply_headers, data


class Client:
    """Fetches https URLs only. Thread-safe; one instance can serve a thread pool."""

    def __init__(
        self,
        transport: Transport | None = None,
        *,
        user_agent: str = USER_AGENT,
        timeout: float = 45.0,
        retries: int = 2,
        backoff: float = 1.0,
        host_delay: Mapping[str, float] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.transport = transport or urllib_transport
        self.user_agent, self.timeout, self.retries, self.backoff = user_agent, timeout, retries, backoff
        self.host_delay = dict(host_delay or {})
        self.sleep, self.clock = sleep, clock
        self._lock = threading.Lock()
        self._next: dict[str, float] = {}
        self.requests = 0

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
        for attempt in range(self.retries + 1):
            self._pace(parts.hostname)
            with self._lock:
                self.requests += 1
            try:
                status, reply_headers, data = self.transport(method, url, send, body, self.timeout)
            except (OSError, http.client.HTTPException):
                if attempt == self.retries:
                    raise
                self.sleep(self._wait(attempt, None))
                continue
            if status in RETRY_STATUS and attempt < self.retries:
                self.sleep(self._wait(attempt, reply_headers.get("retry-after")))
                continue
            return Response(status, reply_headers, data, url)
        raise AssertionError("unreachable")

    def get(self, url: str, **kwargs: Any) -> Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Response:
        return self.request("POST", url, **kwargs)
