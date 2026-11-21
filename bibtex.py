"""Small dependency-free BibTeX parser/writer (nested braces, quotes, @string macros, '#')."""
from __future__ import annotations
import re
from dataclasses import dataclass, field

STANDARD_ORDER = ["author", "editor", "year", "title", "booktitle", "journal", "series", "volume",
                  "number", "issue", "pages", "address", "month", "publisher", "organization",
                  "institution", "school", "edition", "isbn", "issn", "url", "doi", "language", "note"]
MONTHS = {m: m for m in "jan feb mar apr may jun jul aug sep oct nov dec".split()}


@dataclass
class Entry:
    type: str
    key: str
    fields: dict = field(default_factory=dict)

    def get(self, name, default=""):
        return self.fields.get(name, default)


def _skip_ws(s, i):
    while i < len(s) and s[i].isspace():
        i += 1
    return i


def _read_braced(s, i):
    """s[i] == '{'. Returns (content, index_after_closing_brace)."""
    depth, start = 0, i
    while i < len(s):
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[start + 1:i], i + 1
        i += 1
    raise ValueError("unbalanced braces in BibTeX input")


def _read_value(s, i, macros):
    parts = []
    while True:
        i = _skip_ws(s, i)
        if i >= len(s):
            break
        c = s[i]
        if c == "{":
            v, i = _read_braced(s, i)
            parts.append(v)
        elif c == '"':
            j, depth = i + 1, 0
            while j < len(s):
                if s[j] == "\\":
                    j += 2
                    continue
                if s[j] == "{":
                    depth += 1
                elif s[j] == "}":
                    depth -= 1
                elif s[j] == '"' and depth == 0:
                    break
                j += 1
            parts.append(s[i + 1:j])
            i = j + 1
        else:
            m = re.match(r"[^\s,#{}()=\"]+", s[i:])
            if not m:
                break
            tok = m.group(0)
            parts.append(macros.get(tok.lower(), tok))
            i += len(tok)
        i = _skip_ws(s, i)
        if i < len(s) and s[i] == "#":
            i += 1
            continue
        break
    return "".join(parts), i


def parse(text: str):
    """Return (entries, extras); extras keeps @string/@preamble/@comment blocks verbatim."""
    entries, extras, macros = [], [], dict(MONTHS)
    i, n = 0, len(text)
    while i < n:
        at = text.find("@", i)
        if at < 0:
            break
        m = re.match(r"@\s*([A-Za-z]+)\s*([{(])", text[at:])
        if not m:
            i = at + 1
            continue
        etype, opener = m.group(1).lower(), m.group(2)
        open_pos = at + m.end() - 1
        body_start = open_pos + 1
        if opener == "{":
            _, end = _read_braced(text, open_pos)
            inner = text[body_start:end - 1]
        else:
            close = text.find("\n)", body_start)
            end = len(text) if close < 0 else close + 2
            inner = text[body_start:end - 1]
        i = end
        if etype in ("comment", "preamble", "string"):
            raw = text[at:end]
            if etype == "string":
                mm = re.match(r"\s*([^\s=]+)\s*=\s*", inner)
                if mm:
                    val, _ = _read_value(inner, mm.end(), macros)
                    macros[mm.group(1).lower()] = val
            extras.append(raw)
            continue
        km = re.match(r"\s*([^,\s]+)\s*,?", inner)
        if not km:
            continue
        key, pos = km.group(1), km.end()
        fields = {}
        while pos < len(inner):
            pos = _skip_ws(inner, pos)
            fm = re.match(r"([A-Za-z0-9_\-:.]+)\s*=\s*", inner[pos:])
            if not fm:
                pos += 1
                continue
            name = fm.group(1).lower()
            val, pos = _read_value(inner, pos + fm.end(), macros)
            fields[name] = re.sub(r"\s+", " ", val).strip()
            pos = _skip_ws(inner, pos)
            if pos < len(inner) and inner[pos] == ",":
                pos += 1
        entries.append(Entry(etype, key, fields))
    return entries, extras


def dump(entries, extras=()):
    out = [e.strip() + "\n" for e in extras]
    for e in entries:
        std = [k for k in STANDARD_ORDER if k in e.fields]
        other = sorted(k for k in e.fields if k not in STANDARD_ORDER and not k.startswith("x-"))
        xs = [k for k in e.fields if k.startswith("x-")]
        lines = [f"@{e.type}{{{e.key},"]
        for k in std + other + xs:
            lines.append(f"  {k} = {{{str(e.fields[k]).replace(chr(10), ' ')}}},")
        lines.append("}")
        out.append("\n".join(lines) + "\n")
    return "\n".join(out)
