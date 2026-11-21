"""Clients for open scholarly metadata APIs. All return normalised ``Record`` objects.

Conventions:  lookup_* returns Record or None (None = definitively not found, HTTP 404);
network/server problems raise SourceError so the caller can tell "does not exist" from "unknown".
"""
from __future__ import annotations
import re
import time
from dataclasses import dataclass, field
from urllib.parse import quote

from .textnorm import normalize_doi, normalize_issn, strip_retracted_prefix


class SourceError(Exception):
    pass


@dataclass
class Record:
    source: str
    title: str = ""
    authors: list = field(default_factory=list)      # [(family, given, orcid)]
    year: int | None = None
    container: str = ""                              # journal / proceedings / book title
    volume: str = ""
    issue: str = ""
    pages: str = ""
    doi: str = ""
    url: str = ""
    publisher: str = ""
    issn_print: str = ""
    issn_electronic: str = ""
    issns: list = field(default_factory=list)
    type: str = ""
    retractions: list = field(default_factory=list)  # [{"date","doi","type","label","source"}]
    is_retracted: bool = False
    in_doaj: bool | None = None
    outlet_url: str = ""
    raw_id: str = ""


class Http:
    """Tiny JSON GET client with retry, politeness delay and in-memory cache."""

    def __init__(self, mailto="", delay=0.15, timeout=20, retries=3, session=None):
        import requests
        self.s = session or requests.Session()
        self.s.headers["User-Agent"] = f"bibcheck/0.1 (mailto:{mailto})" if mailto else "bibcheck/0.1"
        self.mailto, self.delay, self.timeout, self.retries = mailto, delay, timeout, retries
        self.cache = {}

    def get_json(self, url, params=None):
        key = (url, tuple(sorted((params or {}).items())))
        if key in self.cache:
            return self.cache[key]
        last = None
        for attempt in range(self.retries):
            try:
                time.sleep(self.delay)
                r = self.s.get(url, params=params, timeout=self.timeout)
                if r.status_code == 404:
                    self.cache[key] = (404, None)
                    return self.cache[key]
                if r.status_code in (429, 500, 502, 503, 504):
                    last = f"HTTP {r.status_code}"
                    time.sleep(min(2 ** attempt * 1.5, 10))
                    continue
                if r.status_code >= 400:
                    raise SourceError(f"HTTP {r.status_code} for {url}")
                self.cache[key] = (r.status_code, r.json())
                return self.cache[key]
            except SourceError:
                raise
            except Exception as exc:  # network, JSON decode ...
                last = str(exc)
                time.sleep(min(2 ** attempt, 5))
        raise SourceError(f"{url}: {last}")


def _year(parts):
    try:
        return int(parts["date-parts"][0][0])
    except Exception:
        return None


def _date_str(parts):
    try:
        return "-".join(f"{int(x):02d}" if i else str(x) for i, x in enumerate(parts["date-parts"][0]))
    except Exception:
        return ""


class Crossref:
    name = "Crossref"
    base = "https://api.crossref.org"

    def __init__(self, http):
        self.http = http

    def _params(self, extra=None):
        p = dict(extra or {})
        if self.http.mailto:
            p["mailto"] = self.http.mailto
        return p

    @staticmethod
    def parse(m) -> Record:
        issn_type = {t.get("type"): normalize_issn(t.get("value")) for t in m.get("issn-type", [])}
        r = Record(
            source="Crossref",
            title=(m.get("title") or [""])[0],
            authors=[(a.get("family", a.get("name", "")), a.get("given", ""),
                      re.sub(r"^https?://orcid.org/", "", a.get("ORCID", "")))
                     for a in m.get("author", [])],
            year=_year(m.get("issued") or m.get("published") or {}),
            container=(m.get("container-title") or [""])[0],
            volume=m.get("volume", ""), issue=m.get("issue", ""),
            pages=m.get("page", ""), doi=normalize_doi(m.get("DOI", "")), url=m.get("URL", ""),
            publisher=m.get("publisher", ""), type=m.get("type", ""),
            issn_print=issn_type.get("print", ""), issn_electronic=issn_type.get("electronic", ""),
            issns=[normalize_issn(i) for i in m.get("ISSN", []) if normalize_issn(i)],
        )
        for u in m.get("updated-by", []) or []:
            if "retract" in (u.get("type", "") + u.get("label", "")).lower():
                r.retractions.append({"date": _date_str(u.get("updated", {})), "doi": normalize_doi(u.get("DOI", "")),
                                      "type": u.get("type", ""), "label": u.get("label", ""),
                                      "source": u.get("source", "Crossref")})
        if r.retractions or re.match(r"^\s*retracted\b", r.title, re.I):
            r.is_retracted = True
        return r

    def lookup_doi(self, doi):
        status, data = self.http.get_json(f"{self.base}/works/{quote(doi, safe='/')}", self._params())
        return None if status == 404 else self.parse(data["message"])

    def search(self, title, author="", year=None, rows=5):
        p = {"query.bibliographic": f"{strip_retracted_prefix(title)} {author}".strip(), "rows": rows}
        if year:
            p["filter"] = f"from-pub-date:{year - 1},until-pub-date:{year + 1}"
        _, data = self.http.get_json(f"{self.base}/works", self._params(p))
        return [self.parse(i) for i in data["message"].get("items", [])]

    def notices_for(self, doi):
        """Retraction/correction notices that declare an update to ``doi`` (Crossref 'updates' filter)."""
        _, data = self.http.get_json(f"{self.base}/works", self._params({"filter": f"updates:{doi}", "rows": 20}))
        out = []
        for item in data["message"].get("items", []):
            for u in item.get("update-to", []) or []:
                if normalize_doi(u.get("DOI", "")) == doi and "retract" in (u.get("type", "") + u.get("label", "")).lower():
                    out.append({"date": _date_str(u.get("updated", {})) or _date_str(item.get("issued", {})),
                                "doi": normalize_doi(item.get("DOI", "")), "type": u.get("type", ""),
                                "label": u.get("label", ""), "source": u.get("source", "Crossref"),
                                "reason": ", ".join(u.get("reasons", [])) if isinstance(u.get("reasons"), list) else ""})
        return out

    def journal(self, issn):
        status, data = self.http.get_json(f"{self.base}/journals/{issn}", self._params())
        return None if status == 404 else data["message"]


class DataCite:
    name = "DataCite"
    base = "https://api.datacite.org"

    def __init__(self, http):
        self.http = http

    def lookup_doi(self, doi):
        status, data = self.http.get_json(f"{self.base}/dois/{quote(doi, safe='/')}")
        if status == 404:
            return None
        a = data["data"]["attributes"]
        authors = []
        for c in a.get("creators", []):
            orcid = ""
            for n in c.get("nameIdentifiers", []):
                if (n.get("nameIdentifierScheme") or "").upper() == "ORCID":
                    orcid = re.sub(r"^https?://orcid.org/", "", n.get("nameIdentifier", ""))
            fam = c.get("familyName") or c.get("name", "")
            authors.append((fam, c.get("givenName", ""), orcid))
        cont = a.get("container") or {}
        return Record(source="DataCite", title=(a.get("titles") or [{}])[0].get("title", ""), authors=authors,
                      year=a.get("publicationYear"), container=cont.get("title", "") if isinstance(cont, dict) else "",
                      doi=normalize_doi(a.get("doi", "")), url=a.get("url", ""), publisher=a.get("publisher") if isinstance(a.get("publisher"), str) else (a.get("publisher") or {}).get("name", ""),
                      type=(a.get("types") or {}).get("resourceTypeGeneral", ""))


class OpenAlex:
    name = "OpenAlex"
    base = "https://api.openalex.org"

    def __init__(self, http):
        self.http = http

    def _params(self, extra=None):
        p = dict(extra or {})
        if self.http.mailto:
            p["mailto"] = self.http.mailto
        return p

    @staticmethod
    def parse(w) -> Record:
        src = ((w.get("primary_location") or {}).get("source")) or {}
        bib = w.get("biblio") or {}
        pages = "--".join(x for x in (bib.get("first_page"), bib.get("last_page")) if x)
        authors = []
        for a in w.get("authorships", []):
            au = a.get("author") or {}
            name = au.get("display_name") or a.get("raw_author_name", "")
            giv, _, fam = name.rpartition(" ")
            authors.append((fam, giv, re.sub(r"^https?://orcid.org/", "", au.get("orcid") or "")))
        issns = [normalize_issn(i) for i in (src.get("issn") or []) if normalize_issn(i)]
        return Record(source="OpenAlex", title=w.get("title") or w.get("display_name") or "", authors=authors,
                      year=w.get("publication_year"), container=src.get("display_name", ""),
                      volume=bib.get("volume") or "", issue=bib.get("issue") or "", pages=pages,
                      doi=normalize_doi(w.get("doi") or ""), url=(w.get("primary_location") or {}).get("landing_page_url") or "",
                      publisher=src.get("host_organization_name") or "", issns=issns, type=w.get("type", ""),
                      is_retracted=bool(w.get("is_retracted")), in_doaj=src.get("is_in_doaj"),
                      outlet_url=src.get("homepage_url") or "", raw_id=w.get("id", ""))

    def lookup_doi(self, doi):
        status, data = self.http.get_json(f"{self.base}/works/doi:{quote(doi, safe='/')}", self._params())
        return None if status == 404 else self.parse(data)

    def search(self, title, author="", year=None, rows=5):
        _, data = self.http.get_json(f"{self.base}/works", self._params(
            {"search": strip_retracted_prefix(title), "per-page": rows}))
        return [self.parse(w) for w in data.get("results", [])]


class DBLP:
    name = "DBLP"
    base = "https://dblp.org/search/publ/api"

    def __init__(self, http):
        self.http = http

    def search(self, title, author="", year=None, rows=5):
        q = f"{strip_retracted_prefix(title)} {author}".strip()
        _, data = self.http.get_json(self.base, {"q": q, "format": "json", "h": rows})
        hits = (((data.get("result") or {}).get("hits") or {}).get("hit")) or []
        out = []
        for h in hits:
            i = h.get("info", {})
            au = (i.get("authors") or {}).get("author", [])
            au = [au] if isinstance(au, dict) else au
            authors = []
            for a in au:
                nm = re.sub(r"\s+\d{4}$", "", a.get("text", ""))
                authors.append((nm.rpartition(" ")[2], nm.rpartition(" ")[0], ""))
            try:
                yr = int(i.get("year"))
            except (TypeError, ValueError):
                yr = None
            out.append(Record(source="DBLP", title=(i.get("title") or "").rstrip("."), authors=authors, year=yr,
                              container=i.get("venue") if isinstance(i.get("venue"), str) else ", ".join(i.get("venue", [])),
                              volume=i.get("volume", ""), pages=i.get("pages", ""),
                              doi=normalize_doi(i.get("doi", "")), url=i.get("ee", ""), type=i.get("type", "")))
        return out


class DOAJ:
    name = "DOAJ"
    base = "https://doaj.org/api/search/journals"

    def __init__(self, http):
        self.http = http

    def by_issn(self, issn):
        """Return bibjson dict if journal is in DOAJ else None."""
        status, data = self.http.get_json(f"{self.base}/issn:{issn}")
        if status == 404 or not data or not data.get("total"):
            return None
        return data["results"][0].get("bibjson", {})
