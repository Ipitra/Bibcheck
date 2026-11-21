"""Per-entry verification, correction and enrichment pipeline (deterministic, no generative AI)."""
from __future__ import annotations
import re
from dataclasses import dataclass, field

from .bibtex import Entry
from .lists import QualityLists
from .sources import Record, SourceError
from .textnorm import (authors_match, format_authors, given_is_abbreviated, key_year, normalize_doi,
                       normalize_issn, normalize_pages, parse_authors, strip_retracted_prefix,
                       title_similarity)

TITLE_OK = 0.90        # similarity needed to call two titles "the same publication"
AUTHOR_OK = 0.60       # share of matching surnames
ADOPT_FIELDS = ["author", "title", "journal", "booktitle", "volume", "number", "pages", "year", "publisher"]


@dataclass
class Result:
    key: str
    status: str = "unknown"            # verified | corrected | not_found | retracted | mismatch | error
    changes: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    errors: list = field(default_factory=list)


def yn(v):
    return {True: "Yes", False: "No", None: "n.a."}[v]


class Verifier:
    def __init__(self, crossref, datacite, openalex, dblp, doaj, lists: QualityLists | None = None,
                 check_urls=True, url_fetcher=None, adopt=True, searchers=None):
        self.cr, self.dc, self.oa, self.dblp, self.doaj = crossref, datacite, openalex, dblp, doaj
        self.lists = lists or QualityLists()
        self.check_urls, self.fetch, self.adopt = check_urls, url_fetcher, adopt
        self.searchers = searchers or [s for s in (crossref, openalex, dblp) if s]

    # ---------------------------------------------------------------- matching
    def score(self, entry: Entry, rec: Record):
        t = title_similarity(entry.get("title"), rec.title)
        a = authors_match(parse_authors(entry.get("author")), [(f, g) for f, g, *_ in rec.authors]) if entry.get("author") and rec.authors else 0.5
        ey, ry = key_year(entry.get("year")), rec.year
        y = 1.0 if not (ey and ry) else (1.0 if abs(ey - ry) <= 1 else 0.0)
        return t, a, y

    def is_same(self, entry, rec):
        t, a, y = self.score(entry, rec)
        return t >= TITLE_OK and a >= AUTHOR_OK and y > 0

    # ---------------------------------------------------------------- lookups
    def lookup_doi(self, doi, res):
        """Registry lookup: Crossref first, DataCite (arXiv/Zenodo/...) second. Returns (record|None, reachable)."""
        reachable = False
        for src in (self.cr, self.dc):
            try:
                rec = src.lookup_doi(doi)
                reachable = True
                if rec:
                    return rec, True
            except SourceError as e:
                res.errors.append(f"{src.name}: {e}")
        return None, reachable

    def find_candidates(self, entry, res):
        first = (parse_authors(entry.get("author")) or [("", "")])[0][0]
        year = key_year(entry.get("year"))
        cands, reachable = [], False
        for s in self.searchers:
            try:
                found = s.search(entry.get("title"), first, year)
                reachable = True
                cands += found
            except SourceError as e:
                res.errors.append(f"{s.name}: {e}")
        return cands, reachable

    # ---------------------------------------------------------------- main
    def process(self, entry: Entry):
        res, f, x = Result(entry.key), entry.fields, {}
        original = dict(f)
        doi = normalize_doi(f.get("doi", "")) or normalize_doi(f.get("url", ""))
        if not doi:
            m = re.search(r"arxiv\.org/(?:abs|pdf)/([\w.\-/]+?)(?:v\d+)?(?:\.pdf)?$", f.get("url", ""))
            if m:
                doi = f"10.48550/arxiv.{m.group(1).lower()}"
        x["x-doi-exists"] = x["x-doi-points-to-the-correct-publication"] = "n.a."
        best, reachable_any = None, False

        # 1) DOI given -> does it exist and does it point to this publication?
        if doi:
            rec, reach = self.lookup_doi(doi, res)
            reachable_any |= reach
            if rec:
                x["x-doi-exists"] = "Yes"
                same = self.is_same(entry, rec)
                x["x-doi-points-to-the-correct-publication"] = yn(same)
                if same:
                    best = rec
                else:
                    t, a, _ = self.score(entry, rec)
                    res.notes.append(f"The DOI points to a different publication: \"{rec.title}\" "
                                     f"({', '.join(au[0] for au in rec.authors[:3])}, {rec.year}); title similarity {t:.2f}, author match {a:.2f}.")
            elif reach:
                x["x-doi-exists"] = "No"
                res.notes.append(f"DOI {doi} is not registered at Crossref or DataCite.")
            else:
                x["x-doi-exists"] = "Unknown"

        # 2) No (valid) match yet -> search by bibliographic data
        searched_found = False
        if best is None:
            cands, reach = self.find_candidates(entry, res)
            reachable_any |= reach
            good = [c for c in cands if self.is_same(entry, c)]
            if good:
                good.sort(key=lambda c: (bool(c.doi), sum(self.score(entry, c)[:2])), reverse=True)
                best, searched_found = good[0], True
                # enrich with the registry record when a DOI was found
                if best.doi:
                    rec, _ = self.lookup_doi(best.doi, res)
                    if rec and self.is_same(entry, rec):
                        best = rec
                if not doi and best.doi:
                    res.notes.append(f"DOI found online: {best.doi}")
        x["x-publication-exists"] = "Yes" if best else ("No" if reachable_any else "Unknown")
        # "found in outlet" = the publisher-side registry (Crossref/DataCite) lists this publication
        x["x-found-in-outlet"] = "Yes" if best and best.source in ("Crossref", "DataCite") else ("No" if reachable_any else "Unknown")
        x["x-found-in-google"] = "n.a."

        # 3) enrich from OpenAlex (ORCIDs, outlet ISSNs, DOAJ flag, retraction flag)
        oa_rec = None
        if best and best.doi:
            try:
                oa_rec = self.oa.lookup_doi(best.doi)
            except SourceError as e:
                res.errors.append(f"OpenAlex: {e}")
        elif best is None:
            pass
        if oa_rec and not self.is_same(entry, oa_rec):
            oa_rec = None

        # 4) retraction
        retr, retr_known = [], False
        if best and best.doi:
            retr += best.retractions
            try:
                retr += self.cr.notices_for(best.doi)
                retr_known = True
            except SourceError as e:
                res.errors.append(f"Crossref notices: {e}")
            if oa_rec and oa_rec.is_retracted and not retr:
                retr.append({"date": "", "doi": "", "type": "retraction", "label": "Retraction", "source": "OpenAlex"})
            if best.is_retracted and not retr:
                retr.append({"date": "", "doi": "", "type": "retraction", "label": "Retraction", "source": "title"})
        if best is None:
            x["x-publication-retracted"] = "n.a."
        elif retr:
            seen, uniq = set(), []
            for r in retr:
                k = (r.get("doi"), r.get("date"))
                if k not in seen:
                    seen.add(k); uniq.append(r)
            x["x-publication-retracted"] = "Yes"
            dates = sorted(r["date"] for r in uniq if r.get("date"))
            if dates:
                x["x-retraction-date"] = dates[0]
            nd = [r["doi"] for r in uniq if r.get("doi")]
            if nd:
                x["x-retraction-notice-doi"] = "; ".join(nd)
            reasons = [r["reason"] for r in uniq if r.get("reason")]
            if reasons:
                x["x-retraction-reason"] = "; ".join(reasons)
            res.notes.append("Publication is RETRACTED" + (f" (notice dated {dates[0]})" if dates else "") +
                             ". Compare the notice date with the date of citation to judge whether the author worked carefully.")
        else:
            x["x-publication-retracted"] = "No" if retr_known else "Unknown"

        # 5) outlet (journal / conference) information + quality lists
        issns = list(dict.fromkeys(([best.issn_print, best.issn_electronic] if best else []) +
                                   (best.issns if best else []) + (oa_rec.issns if oa_rec else [])))
        issns = [i for i in issns if i]
        outlet = (best.container if best else "") or f.get("journal", "") or f.get("booktitle", "")
        publisher = (best.publisher if best else "") or f.get("publisher", "")
        outlet_exists, outlet_url, in_doaj = None, "", None
        if best and best.container and issns:
            try:
                j = self.cr.journal(issns[0])
                outlet_exists = bool(j)
                if j and not publisher:
                    publisher = j.get("publisher", "")
            except SourceError as e:
                res.errors.append(f"Crossref journals: {e}")
        if oa_rec:
            outlet_url = oa_rec.outlet_url
            in_doaj = oa_rec.in_doaj
            outlet_exists = True if outlet_exists is None and oa_rec.container else outlet_exists
        for i in issns[:2]:
            try:
                d = self.doaj.by_issn(i)
                if d:
                    in_doaj = True
                    break
                elif in_doaj is None:
                    in_doaj = False
            except SourceError as e:
                res.errors.append(f"DOAJ: {e}")
                break
        pe = best.issn_electronic if best else ""
        pp = best.issn_print if best else ""
        if best and not (pe or pp) and issns:
            pe = issns[0]
            pp = issns[1] if len(issns) > 1 else ""
        if best and best.container:
            x["x-outlet-electronic-issn"] = pe or "n.a."
            x["x-outlet-print-issn"] = pp or "n.a."
            x["x-outlet-exists"] = yn(outlet_exists) if outlet_exists is not None else "Unknown"
            x["x-outlet-url"] = outlet_url or "n.a."
        year = key_year(f.get("year")) or (best.year if best else None)
        wl = (["DOAJ"] if in_doaj else []) + self.lists.whitelists(outlet, issns, publisher)
        bl = self.lists.blacklists(outlet, issns, publisher)
        pbl = self.lists.publisher_blacklists(publisher)
        rk = self.lists.rankings(outlet, issns, publisher, year)
        if best:
            x["x-outlet-predatory"] = "Yes" if bl else ("No" if not self.lists.empty else "Unknown")
            x["x-publisher-predatory"] = "Yes" if pbl else ("No" if not self.lists.empty else "Unknown")
        x["x-whitelists"] = "; ".join(wl) or "n.a."
        x["x-blacklists"] = "; ".join(dict.fromkeys(bl + pbl)) or "n.a."
        x["x-rankings"] = "; ".join(rk) or "n.a."
        if bl or pbl:
            res.notes.append("Outlet/publisher appears on a blacklist: " + "; ".join(dict.fromkeys(bl + pbl)))

        # 6) URL checks
        self._check_urls(entry, best, x, res)

        # 7) correct / enrich fields from the authoritative record
        if best:
            self._adopt(entry, best, oa_rec, x, res, original)
        if res.notes:
            x["x-additional-comments"] = " | ".join(res.notes)

        for k, v in x.items():
            f[k] = v

        if x["x-publication-exists"] == "No":
            res.status = "not_found"
        elif x["x-publication-exists"] == "Unknown":
            res.status = "error"
        elif x.get("x-publication-retracted") == "Yes":
            res.status = "retracted"
        elif x["x-doi-exists"] in ("No",) or x["x-doi-points-to-the-correct-publication"] == "No":
            res.status = "mismatch"
        elif res.changes:
            res.status = "corrected"
        else:
            res.status = "verified"
        return res

    # ---------------------------------------------------------------- helpers
    def _check_urls(self, entry, best, x, res):
        url = entry.get("url")
        x["x-url-exists"] = x["x-url-points-to-the-correct-publication"] = "n.a."
        if not url:
            return
        if not self.check_urls or not self.fetch:
            x["x-url-exists"] = x["x-url-points-to-the-correct-publication"] = "Unknown"
            return
        u_doi = normalize_doi(url) if "doi.org/" in url else ""
        status, html = self.fetch(url)
        x["x-url-exists"] = "Yes" if status and status < 400 else ("Unknown" if status is None else "No")
        if u_doi:
            x["x-url-points-to-the-correct-publication"] = x["x-doi-points-to-the-correct-publication"] if u_doi == normalize_doi(entry.get("doi", u_doi)) else "No"
        elif html:
            m = re.search(r'<meta[^>]+name=["\']citation_title["\'][^>]+content=["\']([^"\']+)', html, re.I)
            page_title = m.group(1) if m else (re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S) or [None, ""])[1]
            from .textnorm import fold
            ft = fold(strip_retracted_prefix(entry.get("title")))
            ok = bool(ft) and (ft in fold(html[:400000]) or title_similarity(entry.get("title"), page_title) >= 0.8)
            x["x-url-points-to-the-correct-publication"] = yn(ok)
        else:
            x["x-url-points-to-the-correct-publication"] = "Unknown"

    def _set(self, entry, name, new, res, original):
        old = entry.fields.get(name, "")
        if new and new != old:
            entry.fields[name] = new
            res.changes.append((name, old, new))
            if old:
                entry.fields.setdefault(f"x-original-{name}", old)

    def _adopt(self, entry, rec, oa_rec, x, res, original):
        """Fill gaps always; overwrite existing values only when --adopt is on (default)."""
        f = entry.fields
        # authors: full names, correct order, ORCIDs
        pub = [(fam, giv) for fam, giv, *_ in rec.authors]
        orcids = [o for *_, o in rec.authors]
        if oa_rec and len(oa_rec.authors) == len(rec.authors):
            orcids = [o or oa_rec.authors[i][2] for i, o in enumerate(orcids)]
        if pub and all(fam for fam, _ in pub):
            mine = parse_authors(f.get("author", ""))
            new = format_authors(pub)
            if self.adopt or not mine:
                if len(pub) >= len(mine) or not mine:
                    self._set(entry, "author", new, res, original)
            elif len(pub) > len(mine) or any(given_is_abbreviated(g) for _, g in mine):
                res.notes.append(f"Publisher lists authors as: {new}")
        x["x-author-orcids"] = "; ".join(o or "n.a." for o in orcids) if orcids else "n.a."
        if self.adopt:
            if rec.title and title_similarity(rec.title, f.get("title", "")) < 1.0:
                self._set(entry, "title", rec.title, res, original)
            key = "booktitle" if entry.type in ("inproceedings", "incollection", "conference") else "journal"
            if rec.container and entry.type in ("article", "inproceedings", "incollection", "conference"):
                self._set(entry, key, rec.container, res, original)
            for name, val in (("volume", rec.volume), ("number", rec.issue), ("pages", normalize_pages(rec.pages)),
                              ("publisher", rec.publisher), ("year", str(rec.year) if rec.year else "")):
                if val:
                    self._set(entry, name, val, res, original)
        else:
            for name, val in (("volume", rec.volume), ("number", rec.issue), ("pages", normalize_pages(rec.pages)),
                              ("publisher", rec.publisher)):
                if val and not f.get(name):
                    self._set(entry, name, val, res, original)
        if rec.doi:
            if f.get("doi", "") != rec.doi:   # also strips "https://doi.org/" prefixes
                self._set(entry, "doi", rec.doi, res, original)
            if not f.get("url") or "doi.org" in f.get("url", ""):
                self._set(entry, "url", f"https://doi.org/{rec.doi}", res, original)
        elif rec.url and not f.get("url"):
            self._set(entry, "url", rec.url, res, original)
        f.setdefault("language", "en")
