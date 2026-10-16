import json
import threading

import pytest
from ajh import http as ajh_http
from ajh.http import Client, HttpError


def scripted(replies):
    """A transport that returns (or raises) the given replies in order and records each call."""
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    return transport, calls


def ok(payload=None, status=200, headers=None):
    return status, headers or {}, json.dumps(payload if payload is not None else {}).encode()


def client(replies, **options):
    transport, calls = scripted(replies)
    sleeps = []
    return Client(transport, sleep=sleeps.append, **options), calls, sleeps


def test_get_encodes_params_and_identifies_itself():
    c, calls, _ = client([ok({"jobs": [1]})])
    assert c.get("https://api.example.com/jobs?content=true", params={"q": "ux research", "limit": 5}).json() == {
        "jobs": [1]
    }
    assert calls[0]["url"] == "https://api.example.com/jobs?content=true&q=ux+research&limit=5"
    assert calls[0]["headers"]["User-Agent"].startswith("automated-job-hunter/")


def test_post_sends_json():
    c, calls, _ = client([ok()])
    c.post("https://api.example.com/search", json_body={"offset": 0})
    assert calls[0]["method"] == "POST" and json.loads(calls[0]["body"]) == {"offset": 0}
    assert calls[0]["headers"]["Content-Type"] == "application/json"


def test_transient_statuses_are_retried_with_backoff_then_succeed():
    c, calls, sleeps = client([ok(status=503), ok(status=502), ok({"done": True})])
    assert c.get("https://api.example.com/x").raise_for_status().json() == {"done": True}
    assert len(calls) == 3 and sleeps == [1.0, 2.0]


def test_retry_after_is_honored_but_capped():
    c, _, sleeps = client(
        [ok(status=429, headers={"retry-after": "7"}), ok(status=429, headers={"retry-after": "900"}), ok()]
    )
    c.get("https://api.example.com/x")
    assert sleeps == [7.0, 30.0]


def test_a_429_is_asked_again_after_waits_that_double_then_succeeds():
    c, calls, sleeps = client([ok(status=429)] * 3 + [ok({"done": True})])
    assert c.get("https://api.example.com/x").raise_for_status().json() == {"done": True}
    assert len(calls) == 4 and sleeps == [1.0, 2.0, 4.0]
    assert c.rate_limited == {"api.example.com": 3}


def test_a_429_gets_eight_attempts_in_all_with_the_same_identity_then_fails_cleanly():
    c, calls, sleeps = client([ok(status=429)] * 7 + [ok({"done": True})])
    assert c.get("https://api.example.com/x", params={"q": "ux"}).raise_for_status().json() == {"done": True}
    assert len(calls) == 8 and sleeps == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]
    assert all(call == calls[0] for call in calls)  # the same request every time: User-Agent, headers, URL
    c, calls, _ = client([ok(status=429)] * 9)
    with pytest.raises(HttpError) as failure:
        c.get("https://api.example.com/x").raise_for_status()
    assert failure.value.status == 429 and len(calls) == 8 and c.rate_limited == {"api.example.com": 7}


def test_retry_after_on_a_429_is_honored_but_capped_and_the_waits_go_on_doubling():
    refused = [ok(status=429, headers={"retry-after": "3"}), ok(status=429, headers={"retry-after": "120"})]
    c, _, sleeps = client([*refused, ok(status=429), ok()])
    assert c.get("https://api.example.com/x").status == 200
    assert sleeps == [3.0, 30.0, 4.0]


def test_a_5xx_still_gets_two_retries_and_is_not_counted_as_refused():
    c, calls, sleeps = client([ok(status=503)] * 4)
    assert c.get("https://api.example.com/x").status == 503
    assert len(calls) == 3 and sleeps == [1.0, 2.0] and c.rate_limited == {}


def test_a_client_built_to_ask_once_does_not_ask_again_after_a_429():
    c, calls, sleeps = client([ok(status=429), ok()], retries=0)
    assert c.get("https://api.example.com/x").status == 429
    assert len(calls) == 1 and sleeps == [] and c.rate_limit_retries == 0 and c.rate_limited == {}
    c, calls, sleeps = client([ok(status=429), ok(status=503), ok()], retries=0, rate_limit_retries=2)
    assert c.get("https://api.example.com/x").status == 503  # given explicitly, it holds for a 429 alone
    assert len(calls) == 2 and sleeps == [1.0]


def test_a_tally_counts_by_host_the_429_retries_its_own_thread_makes_inside_it():
    c, _, _ = client([ok(status=429), ok()] * 4)
    c.get("https://before.example.com/x")
    with c.tally() as outer:
        other = threading.Thread(target=c.get, args=("https://api.example.com/y",))  # another thread's
        other.start()
        other.join()
        c.get("https://api.example.com/x")
        with c.tally() as inner:
            c.get("https://more.example.com/x")
    assert outer == {"api.example.com": 1, "more.example.com": 1} and inner == {"more.example.com": 1}
    assert c.rate_limited == {"before.example.com": 1, "api.example.com": 2, "more.example.com": 1}


def test_a_persistent_failure_surfaces_as_an_error_not_an_empty_result():
    c, calls, _ = client([ok(status=503)] * 3)
    with pytest.raises(HttpError) as failure:
        c.get("https://api.example.com/x").raise_for_status()
    assert failure.value.status == 503 and len(calls) == 3


@pytest.mark.parametrize("status", [401, 403, 404])
def test_client_errors_are_not_retried(status):
    c, calls, sleeps = client([ok(status=status)])
    with pytest.raises(HttpError):
        c.get("https://api.example.com/x").raise_for_status()
    assert len(calls) == 1 and sleeps == []


def test_network_errors_are_retried_then_raised():
    c, calls, _ = client([OSError("reset"), ok({"a": 1})])
    assert c.get("https://api.example.com/x").json() == {"a": 1}
    c, calls, _ = client([OSError("down")] * 3)
    with pytest.raises(OSError):
        c.get("https://api.example.com/x")
    assert len(calls) == 3


@pytest.mark.parametrize(
    "url", ["http://api.example.com/x", "file:///etc/hosts", "ftp://example.com/x", "//example.com/x"]
)
def test_only_https_is_fetched(url):
    c, calls, _ = client([ok()])
    with pytest.raises(ValueError):
        c.get(url)
    assert calls == []


def test_requests_to_a_paced_host_are_spaced_and_other_hosts_are_not():
    now = [100.0]
    transport, _calls = scripted([ok(), ok(), ok()])
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    c = Client(transport, sleep=sleep, clock=lambda: now[0], host_delay={"slow.example.com": 0.5})
    c.get("https://slow.example.com/a")
    c.get("https://slow.example.com/b")
    c.get("https://fast.example.com/c")
    assert sleeps == [0.5] and c.requests == 3


def test_a_reply_says_where_it_came_from_after_redirects(monkeypatch):
    moved = Client(lambda *a: (200, {}, b"{}", "https://www.acme.example/careers"))
    assert moved.get("https://acme.example/jobs").url == "https://www.acme.example/careers"
    same, _, _ = client([ok()])  # a transport that doesn't say: the URL asked for
    assert same.get("https://acme.example/jobs", params={"q": "ux"}).url == "https://acme.example/jobs?q=ux"

    class Reply:  # what urllib gives once it has followed a redirect
        status, headers, url = 200, {"Content-Type": "text/html"}, "https://www.acme.example/careers"

        def read(self, size):
            return b"<html></html>"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(ajh_http._OPENER, "open", lambda request, timeout: Reply())
    landed = ajh_http.urllib_transport("GET", "https://acme.example/jobs", {}, None, 1.0)
    assert landed == (200, {"content-type": "text/html"}, b"<html></html>", "https://www.acme.example/careers")
