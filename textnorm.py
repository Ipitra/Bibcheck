"""Normalisation and comparison helpers for titles, author names, DOIs, ISSNs."""
from __future__ import annotations
import re
import unicodedata
from difflib import SequenceMatcher

_ACCENTS = {'"': "\u0308", "'": "\u0301", "`": "\u0300", "^": "\u0302", "~": "\u0303"}
_SPECIAL = {r"\ss": "ß", r"\o": "ø", r"\O": "Ø", r"\aa": "å", r"\AA": "Å", r"\ae": "æ", r"\l": "ł"}
PREFIXES = {"van", "von", "der", "den", "de", "del", "della", "di", "da", "dos", "du", "la", "le",
            "ten", "ter", "bin", "al", "el"}
TITLES = {"dr", "prof", "mr", "mrs", "ms", "phd", "md", "dipl", "ing", "jr", "sr"}


def delatex(s: str) -> str:
    """Convert common LaTeX accent macros to Unicode and drop braces/other commands."""
    s = s or ""
    s = re.sub(r"\\([\"'`^~])\s*\{?\\?([A-Za-z])\}?",
               lambda m: unicodedata.normalize("NFC", m.group(2) + _ACCENTS[m.group(1)]), s)
    for k, v in _SPECIAL.items():
        s = re.sub(re.escape(k) + r"(?![A-Za-z])\s*\{?\}?", v, s)
    s = re.sub(r"\\[A-Za-z]+\s*", "", s)
    s = s.replace("{", "").replace("}", "").replace("\\", "")
    return unicodedata.normalize("NFC", s)


def fold(s: str) -> str:
    """Lower-case, strip accents/punctuation, collapse whitespace – for comparisons only."""
    s = unicodedata.normalize("NFKD", delatex(s))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower().replace("ß", "ss")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def strip_retracted_prefix(title: str) -> str:
    return re.sub(r"^\s*(retracted|withdrawn)\s*[:\-–]?\s*", "", title or "", flags=re.I)


def title_similarity(a: str, b: str) -> float:
    fa, fb = fold(strip_retracted_prefix(a)), fold(strip_retracted_prefix(b))
    if not fa or not fb:
        return 0.0
    return 1.0 if fa == fb else SequenceMatcher(None, fa, fb).ratio()


def normalize_doi(value: str) -> str:
    if not value:
        return ""
    m = re.search(r"10\.\d{4,9}/\S+", value.strip())
    return m.group(0).rstrip(".,;)}>\"'").lower() if m else ""


def normalize_issn(value: str) -> str:
    v = re.sub(r"[^0-9Xx]", "", value or "").upper()
    return f"{v[:4]}-{v[4:]}" if len(v) == 8 else ""


def parse_name(raw: str):
    """Return (family, given) from 'Last, First' or 'First Last'; strips academic titles."""
    raw = delatex(raw).strip()
    if not raw:
        return "", ""
    if "," in raw:
        family, _, given = raw.partition(",")
        family, given = family.strip(), given.strip()
    else:
        toks = raw.split()
        i = len(toks) - 1
        while i > 0 and toks[i - 1].lower().strip(".") in PREFIXES:
            i -= 1
        family, given = " ".join(toks[i:]), " ".join(toks[:i])
    given = " ".join(t for t in given.split() if t.lower().strip(".") not in TITLES)
    return family, given


def parse_authors(value: str):
    return [parse_name(p) for p in re.split(r"\s+and\s+", value or "") if p.strip()]


def format_authors(authors) -> str:
    return " and ".join(f"{f}, {g}" if g else f for f, g in authors)


def surname_key(family: str) -> str:
    toks = fold(family).split()
    while len(toks) > 1 and toks[0] in PREFIXES:
        toks.pop(0)
    return " ".join(toks)


def authors_match(a, b) -> float:
    """Share of surnames of the shorter list that also occur in the longer one (order-insensitive)."""
    ka = [surname_key(f) for f, _ in a if f]
    kb = [surname_key(f) for f, _ in b if f]
    if not ka or not kb:
        return 0.0
    short, long_ = (ka, kb) if len(ka) <= len(kb) else (kb, ka)
    hits = sum(1 for s in short if any(s == t or s in t or t in s for t in long_))
    return hits / len(short)


def given_is_abbreviated(given: str) -> bool:
    toks = [t for t in re.split(r"[\s.\-]+", given or "") if t]
    return bool(toks) and all(len(t) == 1 for t in toks)


def normalize_pages(p: str) -> str:
    return re.sub(r"\s*[-–—]+\s*", "--", p.strip()) if p else ""


def key_year(y: str):
    m = re.search(r"(19|20)\d{2}", y or "")
    return int(m.group(0)) if m else None
