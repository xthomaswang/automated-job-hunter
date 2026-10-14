import random

import pytest
from ajh import textutil as T


def test_html_to_text_unescapes_escaped_html_and_puts_each_block_on_its_own_line():
    escaped = ("&lt;h2&gt;About the role&lt;/h2&gt;&lt;p&gt;Run &amp;amp; analyze   studies.&lt;/p&gt;"
               "&lt;ul&gt;&lt;li&gt;Interviews&lt;/li&gt;&lt;li&gt;&lt;/li&gt;&lt;li&gt;Surveys&lt;/li&gt;&lt;/ul&gt;")
    assert T.html_to_text(escaped) == "About the role\nRun & analyze studies.\n- Interviews\n- Surveys"


def test_html_to_text_drops_script_style_and_page_chrome():
    page = "<nav>Home</nav><p>Keep&nbsp;this<br>and this</p><script>var x='drop';</script><style>.a{}</style><footer>bye</footer>"
    assert T.html_to_text(page) == "Keep this\nand this"
    assert T.html_to_text(None) == "" and T.html_to_text("") == ""


def test_keys_ignore_punctuation_case_and_legal_suffixes_but_keep_non_ascii_letters():
    assert T.company_key("Acme, Inc.") == T.company_key("ACME") == "acme"
    assert T.company_key("Google LLC") == "google"
    assert T.company_key("Co") == "co"  # a name that is only a suffix is left alone
    assert T.words_key("Verkäufer (m/w/d)") == "verkäufer m w d"
    assert T.words_key("ＵＸ  Researcher—II") == "ux researcher ii"


SOURCE = "We’re looking for 5+ years of “mixed-methods” research\nexperience — ideally in B2B. Remote OK."


def test_find_span_returns_the_sources_own_wording_not_the_models():
    quote = "we're looking for 5+ years of \"mixed-methods\" research experience - ideally"
    assert T.find_span(SOURCE, quote) == "We’re looking for 5+ years of “mixed-methods” research\nexperience — ideally"
    assert T.find_span(SOURCE, "Remote OK.") == "Remote OK."
    assert T.find_span(SOURCE, "  ideally\n in   B2B ") == "ideally in B2B"


@pytest.mark.parametrize("quote", ["", "   ", "10+ years of research", "looking for 5 years"])
def test_find_span_rejects_empty_and_altered_quotes(quote):
    assert T.find_span(SOURCE, quote) is None


def prose(rng, words):
    vocab = ("research design product team experience users data analysis collaborate lead build insights "
             "strategy qualitative quantitative methods stakeholders roadmap interviews surveys experiments "
             "metrics impact synthesis deliver partner mentor scope ambiguous problems customers").split()
    return " ".join(rng.choice(vocab) for _ in range(words))


def test_simhash_separates_cosmetic_edits_from_rewrites():
    rng = random.Random(7)
    base = prose(rng, 600)
    words = base.split()
    promo = " ".join([*words[:300], "apply", "today", "via", "our", "partner", "portal", *words[300:]])
    token = base + " ref 9f2c77ab01"
    rewrite = prose(rng, 600)
    near = max(T.hamming(T.simhash64(base), T.simhash64(promo)), T.hamming(T.simhash64(base), T.simhash64(token)))
    far = T.hamming(T.simhash64(base), T.simhash64(rewrite))
    assert near <= 8 < 20 <= far
    assert T.simhash64(base) == T.simhash64("  " + base.upper().replace(" ", "\n"))  # layout and case do not matter
    assert T.simhash64("") == 0 and T.simhash64("two words") == T.simhash64("Two   words!")


def test_snippet_starts_at_the_role_section_when_there_is_one():
    boilerplate = "\n".join("Acme is a mission-driven company that values people." for _ in range(30))
    posting = boilerplate + "\nWhat you'll do\n- Plan and run usability studies\n- Synthesize findings\nRequirements\n- 3 years"
    out = T.snippet(posting, 60)
    assert out == "What you'll do\n- Plan and run usability studies\n- Synthesize"
    assert T.snippet(posting, 57) == "What you'll do\n- Plan and run usability studies\n-"  # never ends inside a word
    assert T.snippet(boilerplate, 80).startswith("Acme is a mission-driven") and len(T.snippet(boilerplate, 80)) <= 80
    assert T.snippet("short posting", 1200) == "short posting"
    assert T.snippet(posting, 0) == ""


def test_snippet_backs_up_when_the_role_section_is_at_the_very_end():
    posting = "x" * 50 + " " + "y" * 50 + "\nThe role\nLead."
    assert T.snippet(posting, 60).endswith("The role\nLead.")


def test_wrap_changes_layout_only():
    rng = random.Random(3)
    text = "\n".join(prose(rng, n) for n in (5, 80, 1, 200))
    wrapped = T.wrap(text, 72)
    assert T.norm_ws(wrapped) == T.norm_ws(text)
    assert max(len(line) for line in wrapped.split("\n")) <= 72
    assert T.find_span(wrapped, text.split("\n")[1]) is not None  # a quote still matches across wrapped lines
