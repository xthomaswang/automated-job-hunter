"""robots.txt read as RFC 9309 says, the same on every Python version (`ajh.robots`)."""

from ajh.http import USER_AGENT
from ajh.robots import allowed


def check(robots_txt, *targets, agent=USER_AGENT):
    """Whether each target (a path and query, as sent) is allowed, for this engine unless `agent` says otherwise."""
    return [allowed(robots_txt, agent, target) for target in targets]


def test_a_question_mark_rule_disallows_the_root_with_a_query_not_the_whole_site():
    robots_txt = "User-agent: *\nDisallow: /?\n"  # urllib.robotparser reads this as Disallow: /
    assert check(robots_txt, "/about", "/", "/about?x=1", "/?x=1") == [True, True, True, False]


def test_the_longest_matching_rule_wins_and_allow_wins_a_tie():
    robots_txt = "User-agent: *\nDisallow: /?\nAllow: /?hl=\nDisallow: /?hl=*&\n"
    assert check(robots_txt, "/?hl=en", "/?hl=en&x=1", "/?x=1") == [True, False, False]
    assert check("User-agent: *\nDisallow: /jobs\nAllow: /jobs\n", "/jobs/1") == [True]  # the same length
    assert check("User-agent: *\nAllow: /\nDisallow: /en/job/\n", "/en/job/1", "/en/about") == [
        False,
        True,
    ]  # any order


def test_a_wildcard_matches_any_run_of_characters_anywhere_in_a_pattern():
    robots_txt = "User-agent: *\nDisallow: /results/?*&page=\n"
    targets = ("/results/?q=ux&page=2", "/results/?page=2", "/results/?q=ux", "/results/x?q=1&page=2")
    assert check(robots_txt, *targets) == [False, True, True, True]
    assert check("User-agent: *\nDisallow: /*/job/\n", "/en/job/1", "/fr-ca/job/", "/job/1") == [False, False, True]
    assert check("User-agent: *\nDisallow: /a**b*c\n", "/a-b-c", "/a-c-b", "/abc?x") == [False, True, False]


def test_a_final_dollar_sign_anchors_the_end_of_the_target():
    robots_txt = "User-agent: *\nDisallow: /*.pdf$\n"
    assert check(robots_txt, "/files/a.pdf", "/a.pdf", "/files/a.pdf?x=1", "/files/a.pdfx") == [
        False,
        False,
        True,
        True,
    ]
    assert check("User-agent: *\nDisallow: /\nAllow: /$\n", "/", "/about") == [True, False]  # the home page only


def test_the_groups_naming_this_engine_replace_those_for_every_crawler():
    robots_txt = "User-agent: *\nDisallow: /\n\nUser-agent: automated-job-hunter\nDisallow: /private\n"
    assert check(robots_txt, "/jobs", "/private/1") == [True, False]
    assert check(robots_txt, "/jobs", agent="otherbot/1.0") == [False]  # another crawler has the * group
    named = "User-Agent: Automated-Job-Hunter/1.0 # in any case, with a version\nDisallow: /x\n"
    assert check(named, "/x", "/y") == [False, True]
    other = "User-agent: otherbot\nDisallow: /\n\nUser-agent: *\nDisallow: /search\n"  # another crawler's group
    assert check(other, "/jobs", "/search?q=1") == [True, False]
    assert check("User-agent: otherbot\nDisallow: /\n", "/jobs") == [True]  # no group for this engine or for *


def test_consecutive_user_agent_lines_form_one_group_and_one_after_a_rule_starts_the_next():
    robots_txt = (
        "User-agent: otherbot\nUser-agent: automated-job-hunter\nDisallow: /a\nUser-agent: thirdbot\nDisallow: /b\n"
    )
    assert check(robots_txt, "/a", "/b") == [False, True]


def test_every_group_that_applies_is_merged():
    every = "User-agent: *\nDisallow: /a\n\nUser-agent: *\nDisallow: /b\n"
    assert check(every, "/a/1", "/b/1", "/c") == [False, False, True]
    ours = (
        "User-agent: automated-job-hunter\nDisallow: /a\n\nUser-agent: *\nDisallow: /c\n\n"
        "User-agent: automated-job-hunter\nDisallow: /b\n"
    )
    assert check(ours, "/a", "/b", "/c") == [False, False, True]


def test_an_empty_disallow_matches_nothing():
    assert check("User-agent: *\nDisallow:\n", "/", "/anything?x=1") == [True, True]
    assert check("User-agent: *\nDisallow:\nDisallow: /x\n", "/x", "/y") == [False, True]  # the other rules stand
    assert check("User-agent: *\nDisallow: /\nAllow:\n", "/x") == [False]  # nor does an empty Allow match


def test_comments_unknown_lines_and_sitemaps_are_ignored():
    robots_txt = (
        "# robots.txt for careers.acme.com\n"
        "User-agent: * # every crawler\n"
        "Crawl-delay: 10\n"
        "Sitemap: https://careers.acme.com/sitemap.xml\n"
        "Disallow: /search # the search pages\n"
        "Noindex: /x\n"
        "this line means nothing\n"
        "Disallow: /category/\n"
    )
    assert check(robots_txt, "/search?q=1", "/category/x", "/sitemap.xml", "/x") == [False, False, True, True]
    assert check("Disallow: /\nUser-agent: *\nDisallow: /x\n", "/y") == [True]  # a rule before any group is in none
    assert check("﻿User-agent: *\nDisallow: /x\n", "/x") == [False]  # a byte order mark
    assert check("", "/x") == [True]


def test_patterns_and_targets_are_compared_percent_encoded():
    robots_txt = "User-agent: *\nDisallow: /job/上海/\nDisallow: /a%2fb\n"
    targets = ("/job/%E4%B8%8A%E6%B5%B7/x", "/job/%e4%b8%8a%e6%b5%b7/x", "/a%2Fb", "/a/b")
    assert check(robots_txt, *targets) == [False, False, False, True]
    assert check("User-agent: *\nDisallow: /\n", "/robots.txt") == [True]  # always allowed
