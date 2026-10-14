"""Text handling: HTML to plain text, comparison keys, quote location, simhash, snippets, wrapping."""
from __future__ import annotations

import hashlib
import html
import re
import textwrap
import unicodedata
from html.parser import HTMLParser

_BLOCK = frozenset("p div br li ul ol h1 h2 h3 h4 h5 h6 tr table section article header blockquote hr dt dd".split())
_SKIP = frozenset("script style nav footer noscript template".split())


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self.skipping += 1
        elif tag in _BLOCK:
            self.parts.append("\n- " if tag == "li" else "\n")

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self.skipping = max(0, self.skipping - 1)
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skipping:
            self.parts.append(data)


def html_to_text(value: str | None) -> str:
    """Plain text with one line per block element and no blank lines, NFC-normalized.

    The input is unescaped once before parsing because some boards (Greenhouse) deliver the
    posting as HTML-escaped HTML.
    """
    parser = _TextExtractor()
    parser.feed(html.unescape(value or ""))
    parser.close()
    lines = (" ".join(raw.split()) for raw in unicodedata.normalize("NFC", "".join(parser.parts)).split("\n"))
    return "\n".join(line for line in lines if line and line != "-")  # "-" is an empty list item


def norm_ws(text: str) -> str:
    return " ".join(text.split())


def words_key(text: str) -> str:
    """Lowercase words without punctuation; letters of every script are kept."""
    return " ".join(re.sub(r"[\W_]+", " ", unicodedata.normalize("NFKC", text).casefold()).split())


LEGAL_SUFFIXES = frozenset("inc llc ltd limited corp corporation co com gmbh plc ag sa bv".split())


def company_key(name: str) -> str:
    """Comparable form of an employer name: words_key without a trailing legal suffix."""
    words = words_key(name).split()
    while len(words) > 1 and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


# Typographic variants a model commonly swaps when copying a quote.
_FOLD = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
    "\u200b": "", "‌": "", "‍": "", "﻿": "", "­": "",
}


def fold_map(text: str) -> tuple[str, list[int]]:
    """Fold text for comparison and remember where each folded character came from.

    Folding is NFKC per character, typographic punctuation to ASCII, case folding, and every run
    of whitespace to one space. `index[i]` is the position in `text` that produced `folded[i]`.
    """
    out: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        rep = _FOLD.get(ch)
        if rep is None:
            rep = unicodedata.normalize("NFKC", ch).casefold()
        for c in rep:
            if c.isspace():
                if out and out[-1] != " ":
                    out.append(" ")
                    index.append(i)
            else:
                out.append(c)
                index.append(i)
    if out and out[-1] == " ":
        out.pop()
        index.pop()
    return "".join(out), index


def fold(text: str) -> str:
    return fold_map(text)[0]


def find_span(source: str, quote: str) -> str | None:
    """The source's own substring matching `quote`, or None.

    Matching ignores line wrapping, case and typographic variants, but the returned text is copied
    from `source`, so a published quote is always the posting's wording and never the model's.
    """
    if not unicodedata.is_normalized("NFC", source):
        source = unicodedata.normalize("NFC", source)
    folded, index = fold_map(source)
    needle = fold(unicodedata.normalize("NFC", quote))
    if not needle:
        return None
    at = folded.find(needle)
    if at < 0:
        return None
    return source[index[at]: index[at + len(needle) - 1] + 1]


_WORD = re.compile(r"[^\W_]+")
_FIELD_BITS = 16  # one packed counter per hash bit; enough for 65,535 shingles
_MAX_SHINGLES = 60000
_BIT_TABLE = [
    [sum(1 << ((pos * 8 + bit) * _FIELD_BITS) for bit in range(8) if (value >> bit) & 1) for value in range(256)]
    for pos in range(8)
]


def simhash64(text: str, shingle: int = 5) -> int:
    """64-bit simhash over word shingles: similar texts differ in few bits.

    Used to tell a cosmetic edit of a posting (an inserted promo phrase, a rotating token) from a
    real rewrite. The 64 bit counters are packed into one integer so each shingle costs eight
    table lookups instead of 64 additions.
    """
    words = _WORD.findall(text.casefold())
    if not words:
        return 0
    count = len(words) - shingle + 1
    grams = {" ".join(words[i:i + shingle]) for i in range(count)} if count > 0 else {" ".join(words)}
    if len(grams) > _MAX_SHINGLES:
        grams = set(sorted(grams)[:_MAX_SHINGLES])
    acc = 0
    for gram in grams:
        d = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
        acc += (_BIT_TABLE[0][d[0]] + _BIT_TABLE[1][d[1]] + _BIT_TABLE[2][d[2]] + _BIT_TABLE[3][d[3]]
                + _BIT_TABLE[4][d[4]] + _BIT_TABLE[5][d[5]] + _BIT_TABLE[6][d[6]] + _BIT_TABLE[7][d[7]])
    half = len(grams) / 2.0
    mask = (1 << _FIELD_BITS) - 1
    return sum(1 << bit for bit in range(64) if ((acc >> (bit * _FIELD_BITS)) & mask) > half)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


_ROLE_HEADER = re.compile(
    r"^\W{0,3}(?:"
    r"what you(?:['’]ll| will) (?:do|be doing|work on|own)"
    r"|(?:key |your |core |main |primary )?responsibilities"
    r"|(?:about |in )?(?:the|this) (?:role|job|position|opportunity)"
    r"|(?:role|job|position) (?:overview|description|summary)"
    r"|your (?:role|impact|mission)|the opportunity|what we(?:['’]re| are) looking for|duties"
    r")\b",
    re.I | re.M,
)


def snippet(description: str, limit: int = 1200) -> str:
    """Up to `limit` characters of a posting, starting at the first role section when there is one.

    Company boilerplate usually comes first, and the part that says what the job is comes later.
    """
    if limit <= 0:
        return ""
    match = _ROLE_HEADER.search(description)
    # A role section too close to the end to fill the budget is preceded by earlier context.
    start = min(match.start(), max(0, len(description) - limit)) if match else 0
    end = start + limit
    piece = description[start:end]
    if end < len(description) and not description[end].isspace():  # the cut landed inside a word
        cut = max(piece.rfind(" "), piece.rfind("\n"))
        if cut > limit // 2:
            piece = piece[:cut]
    return piece.strip()


def wrap(text: str, width: int = 100) -> str:
    """Hard-wrap each line at word boundaries. `norm_ws` of the result equals `norm_ws` of the input."""
    out: list[str] = []
    for line in text.split("\n"):
        out.extend(textwrap.wrap(line, width=width, break_long_words=False, break_on_hyphens=False) or [""])
    return "\n".join(out)
