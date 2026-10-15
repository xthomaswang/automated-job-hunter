"""Oracle Recruiting Cloud: no reader, because Oracle limits its job APIs (docs/spikes.md, E9).

The probe names the system with that reason and asks nothing of Oracle's hosts, so nobody keeps
looking for a board that cannot be added.
"""

import pytest
from ajh import registry
from ajh.http import Client
from ajh.sources import lists as L

SITE = "https://acme.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1"
WHY = "Oracle Recruiting Cloud (its job APIs are only for Oracle and approved partners)"


def web(pages=None):
    """A client that records what it is asked for. Only the pages given exist."""
    asked = []

    def transport(method, url, headers, body, timeout):
        asked.append(url)
        page = (pages or {}).get(url)
        return (404, {}, b"") if page is None else (200, {}, page.encode())

    return Client(transport, sleep=lambda seconds: None, retries=0), asked


@pytest.mark.parametrize(
    "url",
    [
        SITE + "/requisitions",  # the career site
        SITE + "/job/1001",  # one posting
        "https://globex.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2001/job/77",  # a host without a region
    ],
)
def test_a_career_site_or_posting_is_named_with_why_and_never_requested(url):
    client, asked = web()
    found = registry.probe(f"{url} Acme", client)
    assert (found["matches"], found["unsupported"]) == ([], [WHY])
    assert found["next"] == f"This company uses {WHY}, which this version cannot read. There is no board to add."
    assert asked == []  # not even a count: the probe asks nothing of Oracle's endpoints


def test_a_careers_page_linking_to_it_names_it_and_a_lists_link_to_it_is_skipped():
    page = (
        f'<a href="{SITE}/requisitions">Search jobs</a> '
        '<a href="https://hooli.taleo.net/careersection/2/jobsearch.ftl">Campus</a>'
    )
    client, asked = web({"https://www.hooli.example/careers": page})
    found = registry.probe("https://www.hooli.example/careers Hooli", client)
    assert (found["matches"], found["unsupported"]) == ([], [WHY, "Oracle Taleo"])  # Taleo is a system of its own
    assert asked == ["https://www.hooli.example/careers"]  # the company's own page, once
    assert L.link(SITE + "/job/1001") is None and L._why(SITE + "/job/1001") == "no reader"
