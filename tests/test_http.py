import json

import pytest
from ajh.http import Client, HttpError


def scripted(replies):
    """A transport that returns (or raises) the given replies in order and records each call."""
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append({"method": method, "url": url, "headers": headers, "body": body})
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
    assert c.get("https://api.example.com/jobs?content=true", params={"q": "ux research", "limit": 5}).json() == {"jobs": [1]}
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
    c, _, sleeps = client([ok(status=429, headers={"retry-after": "7"}), ok(status=429, headers={"retry-after": "900"}), ok()])
    c.get("https://api.example.com/x")
    assert sleeps == [7.0, 30.0]


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


@pytest.mark.parametrize("url", ["http://api.example.com/x", "file:///etc/hosts", "ftp://example.com/x", "//example.com/x"])
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
