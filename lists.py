"""Local whitelists / blacklists / rankings (CSV files you maintain; see data/lists/README.md).

CSV columns (header required, all optional except one of name/issn/publisher):
    name,issn,publisher,value[,valid_from]
``value`` is e.g. a rating ("A+"), SJR quartile, etc.  ``valid_from`` (year) restricts a ranking to
publications published in or after that year (a ranking only applies to later publications).
"""
from __future__ import annotations
import csv
from pathlib import Path

from .textnorm import fold, normalize_issn


class QualityLists:
    def __init__(self, root=None):
        self.kinds = {"whitelists": [], "blacklists": [], "rankings": []}
        self.loaded = {}
        if root:
            for kind in self.kinds:
                for f in sorted((Path(root) / kind).glob("*.csv")):
                    rows = self._read(f)
                    if rows:
                        self.kinds[kind].append((f.stem, rows))
                        self.loaded.setdefault(kind, []).append(f"{f.stem} ({len(rows)})")

    @staticmethod
    def _read(path):
        rows = []
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                rows.append({"name": fold(r.get("name", "")), "issn": normalize_issn(r.get("issn", "")),
                             "publisher": fold(r.get("publisher", "")), "value": (r.get("value") or "").strip(),
                             "valid_from": int(r["valid_from"]) if (r.get("valid_from") or "").isdigit() else None})
        return rows

    def _match(self, kind, outlet, issns, publisher, year=None, publisher_only=False):
        hits = []
        o, p, issn_set = fold(outlet), fold(publisher), {normalize_issn(i) for i in issns if i}
        for lname, rows in self.kinds[kind]:
            for r in rows:
                if r["valid_from"] and year and year < r["valid_from"]:
                    continue
                by_pub = bool(p) and r["publisher"] and r["publisher"] == p and not r["name"] and not r["issn"]
                by_outlet = not publisher_only and ((r["issn"] and r["issn"] in issn_set) or (r["name"] and o and r["name"] == o))
                if by_pub or by_outlet:
                    hits.append(f"{lname}: {r['value']}" if r["value"] else lname)
                    break
        return hits

    def whitelists(self, outlet, issns, publisher):
        return self._match("whitelists", outlet, issns, publisher)

    def blacklists(self, outlet, issns, publisher):
        return self._match("blacklists", outlet, issns, publisher)

    def publisher_blacklists(self, publisher):
        return self._match("blacklists", "", [], publisher, publisher_only=True)

    def rankings(self, outlet, issns, publisher, year):
        return self._match("rankings", outlet, issns, publisher, year)

    @property
    def empty(self):
        return not any(self.kinds.values())
