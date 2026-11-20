"""Text normalisation used by both the indexer and the query path.

The same ``tokenize`` function is applied to documents at index time and to
queries at search time, so both sides end up in the same term space.

Pipeline: lowercase -> split on non-alphanumerics -> drop stopwords ->
light suffix stemming -> (queries only) synonym expansion.
"""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[a-z0-9]+")

STOPWORDS: frozenset[str] = frozenset(
    """
    a about above after again against all am an and any are as at be because
    been before being below between both but by can could did do does doing
    down during each few for from further had has have having he her here hers
    herself him himself his how i if in into is it its itself just me more most
    my myself nor of off on once or other our ours ourselves out over own same
    she should so some such than that the their theirs them themselves then
    there these they this those through to too under until up very was we were
    what when where which while who whom why will with would you your yours
    yourself yourselves also may must much many s t
    """.split()
)

# Query-side synonym expansion, written with plain words; keys and values are
# stemmed at import time so they match the indexed term space. A query that
# contains a key also searches for the listed terms.
_SYNONYMS_RAW: dict[str, tuple[str, ...]] = {
    "extra": ("additional",),
    "additional": ("extra",),
    "data": ("internet",),
    "gb": ("internet",),
    "late": ("due", "overdue", "unpaid"),
    "overdue": ("late", "due", "unpaid"),
    "cost": ("charge",),
    "price": ("charge", "cost"),
    "pricing": ("charge", "cost"),
    "fee": ("charge",),
    "part": ("partial",),
    "partly": ("partial",),
    "complaint": ("dispute",),
    "complain": ("dispute",),
    "contest": ("dispute",),
    "minutes": ("phone",),
    "call": ("phone", "minutes"),
    "suspend": ("restrict",),
    "suspended": ("restrict",),
    "cut": ("restrict",),
}

# Suffixes removed by the light stemmer, longest first.
_SUFFIXES: tuple[str, ...] = ("ations", "ation", "ments", "ment", "ings", "ing", "ies", "ied", "ed", "es", "ly", "s")
_MIN_STEM = 3


def stem(token: str) -> str:
    """Light suffix stemmer (plurals, -ing, -ed, -ly, -ment, -ation, final -e).

    Conservative on purpose: a suffix is only removed when at least three
    characters remain, numbers are never touched, and a trailing ``e`` is
    dropped so that e.g. ``charge``/``charges``/``charged`` all map to
    ``charg``.
    """
    if token.isdigit() or len(token) <= _MIN_STEM:
        return token
    for suffix in _SUFFIXES:
        if not token.endswith(suffix) or len(token) - len(suffix) < _MIN_STEM:
            continue
        if suffix == "s" and token.endswith(("ss", "us")):
            break
        base = token[: -len(suffix)]
        if suffix in ("ies", "ied"):
            base += "y"
        elif suffix in ("ing", "ed") and base[-1] == base[-2] and base[-1] not in "lsz":
            base = base[:-1]  # "stopped" -> "stop"
        token = base
        break
    if len(token) > _MIN_STEM and token.endswith("e"):
        token = token[:-1]
    return token


def raw_tokens(text: str) -> list[str]:
    """Lowercase and split into alphanumeric tokens (no filtering)."""
    return _TOKEN_RE.findall(text.lower())


def tokenize(text: str) -> list[str]:
    """Tokenize document text: lowercase, strip punctuation and stopwords, stem."""
    return [stem(tok) for tok in raw_tokens(text) if tok not in STOPWORDS]


SYNONYMS: dict[str, tuple[str, ...]] = {
    stem(key): tuple(dict.fromkeys(stem(v) for v in values)) for key, values in _SYNONYMS_RAW.items()
}


def expand_query(tokens: list[str]) -> list[str]:
    """Return query tokens followed by their synonyms, deduplicated in order."""
    out = list(dict.fromkeys(tokens))
    seen = set(out)
    for tok in tokens:
        for syn in SYNONYMS.get(tok, ()):
            if syn not in seen:
                seen.add(syn)
                out.append(syn)
    return out


def tokenize_query(query: str) -> list[str]:
    """Tokenize a user query and apply synonym expansion."""
    return expand_query(tokenize(query))
