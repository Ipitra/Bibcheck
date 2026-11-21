"""Command-line interface:  python -m bibcheck input.bib [-o out.bib] [--report report.md] ..."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

from . import bibtex
from .lists import QualityLists
from .report import to_json, to_markdown
from .sources import DBLP, DOAJ, Crossref, DataCite, Http, OpenAlex
from .verify import Verifier


def make_url_fetcher(http):
    def fetch(url):
        try:
            r = http.s.get(url, timeout=http.timeout, allow_redirects=True, stream=True)
            body = next(r.iter_content(400_000, decode_unicode=True), "") or ""
            r.close()
            return r.status_code, body if isinstance(body, str) else body.decode("utf-8", "ignore")
        except Exception:
            return None, ""
    return fetch


def build_verifier(mailto="", lists_dir=None, check_urls=True, adopt=True, http=None):
    http = http or Http(mailto=mailto)
    return Verifier(Crossref(http), DataCite(http), OpenAlex(http), DBLP(http), DOAJ(http),
                    QualityLists(lists_dir), check_urls=check_urls, url_fetcher=make_url_fetcher(http), adopt=adopt)


def run(text, verifier, progress=None):
    entries, extras = bibtex.parse(text)
    results = []
    for i, e in enumerate(entries, 1):
        r = verifier.process(e)
        results.append(r)
        if progress:
            progress(i, len(entries), e.key, r.status)
    return entries, extras, results


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bibcheck", description="Verify, correct and enrich BibTeX entries (no generative AI).")
    ap.add_argument("input", help="input .bib file")
    ap.add_argument("-o", "--output", help="output .bib (default: <input>.verified.bib)")
    ap.add_argument("--report", help="Markdown report path (default: <input>.report.md)")
    ap.add_argument("--json", help="optional JSON report path")
    ap.add_argument("--email", default="", help="contact e-mail for Crossref/OpenAlex 'polite pool' (recommended)")
    ap.add_argument("--lists", default=str(Path(__file__).resolve().parent.parent / "data" / "lists"),
                    help="folder with whitelists/ blacklists/ rankings/ CSVs")
    ap.add_argument("--no-adopt", action="store_true", help="only fill missing fields; never overwrite existing values")
    ap.add_argument("--no-url-check", action="store_true", help="do not fetch the URLs of entries")
    a = ap.parse_args(argv)
    src = Path(a.input)
    text = src.read_text(encoding="utf-8")
    v = build_verifier(a.email, a.lists, not a.no_url_check, not a.no_adopt)
    def prog(i, n, key, status):
        print(f"[{i}/{n}] {key}: {status}", file=sys.stderr)
    entries, extras, results = run(text, v, prog)
    out = Path(a.output) if a.output else src.with_suffix(".verified.bib")
    out.write_text(bibtex.dump(entries, extras), encoding="utf-8")
    rep = Path(a.report) if a.report else src.with_suffix(".report.md")
    rep.write_text(to_markdown(entries, results, src.name), encoding="utf-8")
    if a.json:
        Path(a.json).write_text(to_json(entries, results), encoding="utf-8")
    print(f"Wrote {out} and {rep}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
