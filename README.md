bibcheck

Verify, correct and enrich BibTeX references from online sources, without generative AI.

bibcheck takes a .bib file and, for every entry, checks whether the publication really exists, whether its DOI/URL points to the right paper, whether it has been retracted, and what is known about the journal or conference and its publisher. Corrections and findings are written to a separate .bib file as additional x-… fields, plus a human-readable report.

It was started for the project Verification and Enrichment of Bibliographic References (Pedagogical Immersion 2026, IU International University). The goal is explicitly not to ask an LLM whether a reference exists: all checks are deterministic lookups in open scholarly databases.

Status: early version (v0.1). The logic is covered by offline unit tests with simulated API responses. It has not yet been validated against the live APIs or the course test sets, so review the results before relying on them.

Features

• Existence check: looks up the DOI (Crossref, then DataCite for arXiv/Zenodo etc.) or searches by title, first author and year in Crossref, OpenAlex and DBLP.
• DOI/URL validation: tells apart "DOI does not exist", "DOI exists but points to a different publication" and "DOI is correct".
• Author clean-up: complete author list in the right order, initials expanded to full names, accents and Unicode encodings normalised, titles such as "Prof. Dr." removed, ORCIDs added where available.
• Field correction: title, journal/booktitle, volume, number, pages, year, publisher, DOI and URL are taken from the authoritative record. Previous values are preserved in x-original-<field>.
• Retraction check: Crossref update notices (including Retraction Watch data) and OpenAlex's retraction flag, with the retraction date so you can judge whether the citing authors worked carefully.
• Outlet information: print/electronic ISSN, whether the journal exists, homepage.
• Quality lists: DOAJ is queried live; whitelists, blacklists and rankings can be added as CSV files.
• Honest uncertainty: if the sources are unreachable the result is Unknown, never No.
• Two interfaces: command line (primary) and a minimal Flask web app using the same pipeline.

Installation

Requires Python 3.10+.

git clone <your-repo-url>
cd bibcheck
python -m venv .venv && source .venv/bin/activate   # optional
pip install -r requirements.txt

Usage

python -m bibcheck references.bib --email isabela.silverio31@gmail.com

This writes references.verified.bib and references.report.md next to the input. Giving an e-mail address puts you in the "polite pool" of Crossref and OpenAlex, which is faster and more reliable.

Option
Description
-o, --output FILE
Output .bib (default <input>.verified.bib)
--report FILE
Markdown report (default <input>.report.md)
--json FILE
Additional machine-readable report
--email ADDRESS
Contact address for the Crossref/OpenAlex polite pool
--lists DIR
Folder with whitelists/, blacklists/, rankings/ CSVs (default data/lists)
--no-adopt
Only fill missing fields, never overwrite existing values
--no-url-check
Do not fetch the URLs given in entries

Web app

python -m bibcheck.webapp      # http://127.0.0.1:5000

Upload a .bib, get a result table plus downloads of the enriched file and the report. Set BIBCHECK_LISTS to use a custom lists folder. Intended for local use; processing is synchronous.

Output

Each verified entry gets extra fields (BibTeX ignores fields starting with x-):

@article{anderson2022a,
  author = {Anderson, C. and Mukherjee, R.},
  title = {Multimodal sentiment fusion for brand storytelling},
  journal = {Marketing Science},
  doi = {10.1287/mksc.2021.1339},
  x-author-orcids = {n.a.; n.a.},
  x-outlet-electronic-issn = {1526-548X},
  x-outlet-print-issn = {0732-2399},
  x-outlet-exists = {Yes},
  x-outlet-predatory = {No},
  x-found-in-outlet = {No},
  x-found-in-google = {n.a.},
  x-doi-exists = {Yes},
  x-doi-points-to-the-correct-publication = {No},
  x-url-exists = {n.a.},
  x-publication-exists = {No},
  x-publication-retracted = {n.a.},
  x-whitelists = {n.a.},
  x-blacklists = {n.a.},
  x-rankings = {n.a.},
  x-additional-comments = {The DOI points to a different publication: "…"}
}

Additional fields that appear when relevant: x-retraction-date, x-retraction-notice-doi, x-retraction-reason, x-original-<field>.

Every entry also gets a status in the report: verified, corrected, retracted, mismatch (DOI wrong), not_found, or error (sources unreachable).

How matching works

A candidate record counts as "the same publication" only if all of these hold:

1. title similarity ≥ 0.90 (after removing case, accents, punctuation and a leading "RETRACTED:"),
2. at least 60 % of the surnames of the shorter author list occur in the longer one,
3. publication years differ by at most one.

Thresholds are constants at the top of bibcheck/verify.py.

Quality lists

Put CSV files into data/lists/whitelists/, blacklists/ or rankings/. The file name becomes the list name written to x-whitelists, x-blacklists or x-rankings.

name,issn,publisher,value,valid_from
Marketing Science,0732-2399,,A,2020

A row with only publisher matches every outlet of that publisher. valid_from restricts a ranking to publications from that year on. The lists themselves are not bundled; see data/lists/README.md for sources (DOAJ CSV, Beall's list copies, predatoryjournals.org, SCImago, CWTS, VHB, Clarivate MJL).

Project structure

bibcheck/
  bibtex.py     dependency-free BibTeX parser/writer
  textnorm.py   names, titles, DOI/ISSN normalisation and comparison
  sources.py    Crossref, DataCite, OpenAlex, DBLP, DOAJ clients
  lists.py      CSV whitelists / blacklists / rankings
  verify.py     per-entry verification and enrichment pipeline
  report.py     Markdown and JSON reports
  cli.py        command-line interface
  webapp.py     minimal Flask front end
data/lists/     quality-list CSVs (examples only)
tests/          offline unit tests
examples/       sample .bib

Tests

python -m unittest discover -s tests

The tests use fake sources, so they run offline and do not exercise the real APIs.

Limitations and roadmap

• x-found-in-google is always n.a. (Google offers no free API). A SerpAPI-based plug-in is a possible extension.
• Not yet implemented: books and ISBN lookup (Open Library, Google Books), PubMed/Europe PMC, Semantic Scholar, detection of hijacked journals and conferences, parsing of references from PDFs, and a full-featured GUI.
• Blacklist matching is exact (ISSN, normalised name or publisher), so variant spellings can be missed.
• The tool does not know when you cited a paper, so retractions are reported with their date only.
• Open data has gaps: a reference can be real but missing from Crossref, OpenAlex and DBLP. Treat x-publication-exists = No as "no evidence found", and check such entries manually.

Acknowledgements

Data comes from Crossref, DataCite, OpenAlex, DBLP and DOAJ. Please respect their usage policies and rate limits.

License

No license has been chosen yet. Add a LICENSE file (for example MIT) before publishing.