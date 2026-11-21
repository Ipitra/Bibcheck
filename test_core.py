import unittest
from bibcheck import bibtex
from bibcheck.sources import Record, Crossref, OpenAlex
from bibcheck.textnorm import (parse_name, title_similarity, normalize_doi, fold, authors_match, parse_authors, delatex)
from bibcheck.verify import Verifier
from bibcheck.lists import QualityLists

SAMPLE = r'''
@string{mksc = "Marketing Science"}
@article{anderson2022a,
  author = {Anderson, C. and Mukherjee, R.},
  year = {2022},
  title = {Multimodal sentiment fusion for brand storytelling},
  journal = mksc,
  volume = {41}, pages = {921-939},
  doi = {10.1287/mksc.2021.1339}
}
@article{real2021,
  author = "M{\"u}ller, Hans and van der Berg, Anna",
  title = {A {Real} Paper on Graphs},
  journal = {Journal of Tests}, year = 2021,
  doi = {https://doi.org/10.1000/real.1}
}
'''


class FakeSrc:
    name = "Fake"
    def __init__(self, docs=None, found=None, notices=None, journal=None):
        self.docs, self.found, self.n, self.j = docs or {}, found or [], notices or {}, journal
    def lookup_doi(self, doi): return self.docs.get(doi)
    def search(self, *a, **k): return self.found
    def notices_for(self, doi): return self.n.get(doi, [])
    def journal(self, issn): return self.j
    def by_issn(self, issn): return None


class ParserTests(unittest.TestCase):
    def test_parse_roundtrip(self):
        es, extras = bibtex.parse(SAMPLE)
        self.assertEqual([e.key for e in es], ["anderson2022a", "real2021"])
        self.assertEqual(es[0].get("journal"), "Marketing Science")      # macro expanded
        self.assertEqual(es[1].get("year"), "2021")
        again, _ = bibtex.parse(bibtex.dump(es, extras))
        self.assertEqual(es[1].fields, again[1].fields)

    def test_names_and_text(self):
        self.assertEqual(parse_name("van der Berg, Anna"), ("van der Berg", "Anna"))
        self.assertEqual(parse_name("Prof. Dr. Sebastian Lempert"), ("Lempert", "Sebastian"))
        self.assertEqual(parse_name("Mathieu van der Poel"), ("van der Poel", "Mathieu"))
        self.assertEqual(fold(r'M{\"u}ller'), fold("Müller"))
        self.assertEqual(fold("Müller"), fold("Mu\u0308ller"))           # different Unicode encodings compare equal
        self.assertEqual(delatex(r'{\"u}'), "ü")
        self.assertEqual(normalize_doi("https://doi.org/10.1000/ABC."), "10.1000/abc")
        self.assertGreater(title_similarity("RETRACTED: Sentiment analysis", "Sentiment Analysis"), 0.99)
        self.assertEqual(authors_match(parse_authors("Anderson, C."), parse_authors("Chris Anderson and R Mukherjee")), 1.0)


class VerifierTests(unittest.TestCase):
    def verifier(self, cr, **kw):
        none = FakeSrc()
        return Verifier(cr, none, kw.get("oa", none), none, none, kw.get("lists"), check_urls=False)

    def test_doi_points_to_other_publication_and_nothing_found(self):
        es, _ = bibtex.parse(SAMPLE)
        other = Record("Crossref", title="Totally different article", authors=[("Smith", "J", "")], year=2022, doi="10.1287/mksc.2021.1339", container="Marketing Science")
        v = self.verifier(FakeSrc(docs={"10.1287/mksc.2021.1339": other}))
        r = v.process(es[0])
        f = es[0].fields
        self.assertEqual(f["x-doi-exists"], "Yes")
        self.assertEqual(f["x-doi-points-to-the-correct-publication"], "No")
        self.assertEqual(f["x-publication-exists"], "No")
        self.assertEqual(r.status, "not_found")
        self.assertIn("different publication", f["x-additional-comments"])

    def test_verified_entry_gets_corrected_and_enriched(self):
        es, _ = bibtex.parse(SAMPLE)
        rec = Record("Crossref", title="A Real Paper on Graphs", year=2021, doi="10.1000/real.1", container="Journal of Tests",
                     authors=[("Müller", "Hans", "0000-0001-2345-6789"), ("van der Berg", "Anna", "")], volume="7", pages="1-9",
                     publisher="Test Press", issn_print="1234-5678", issn_electronic="8765-4321")
        v = self.verifier(FakeSrc(docs={"10.1000/real.1": rec}, journal={"title": "Journal of Tests"}))
        r = v.process(es[1])
        f = es[1].fields
        self.assertEqual(f["x-publication-exists"], "Yes")
        self.assertEqual(f["x-publication-retracted"], "No")
        self.assertEqual(f["x-author-orcids"], "0000-0001-2345-6789; n.a.")
        self.assertEqual(f["pages"], "1--9")
        self.assertEqual(f["doi"], "10.1000/real.1")
        self.assertEqual(f["x-outlet-exists"], "Yes")
        self.assertEqual(r.status, "corrected")

    def test_retraction_and_lists(self):
        es, _ = bibtex.parse(SAMPLE)
        rec = Record("Crossref", title="A Real Paper on Graphs", year=2021, doi="10.1000/real.1", container="Journal of Tests",
                     authors=[("Müller", "Hans", ""), ("van der Berg", "Anna", "")], publisher="Shady Publisher Ltd")
        notice = {"date": "2023-05-01", "doi": "10.1000/retraction.1", "type": "retraction", "label": "Retraction", "source": "retraction-watch"}
        ql = QualityLists()
        ql.kinds["blacklists"].append(("TestList", [{"name": "", "issn": "", "publisher": "shady publisher ltd", "value": "", "valid_from": None}]))
        v = self.verifier(FakeSrc(docs={"10.1000/real.1": rec}, notices={"10.1000/real.1": [notice]}), lists=ql)
        r = v.process(es[1])
        f = es[1].fields
        self.assertEqual(r.status, "retracted")
        self.assertEqual(f["x-retraction-date"], "2023-05-01")
        self.assertEqual(f["x-publisher-predatory"], "Yes")
        self.assertEqual(f["x-blacklists"], "TestList")

    def test_found_by_search_when_no_doi(self):
        es, _ = bibtex.parse("@article{k, author={Doe, J.}, title={Graph Theory Today}, journal={J}, year={2020}}")
        rec = Record("Crossref", title="Graph theory today", year=2020, doi="10.5/x", authors=[("Doe", "Jane", "")], container="J")
        v = self.verifier(FakeSrc(docs={"10.5/x": rec}, found=[rec]))
        v.process(es[0])
        self.assertEqual(es[0].fields["doi"], "10.5/x")
        self.assertEqual(es[0].fields["author"], "Doe, Jane")
        self.assertEqual(es[0].fields["x-original-author"], "Doe, J.")

    def test_parsers_of_api_payloads(self):
        m = {"DOI": "10.1/A", "title": ["T"], "author": [{"family": "Doe", "given": "J", "ORCID": "http://orcid.org/0000-0000-0000-0001"}],
             "issued": {"date-parts": [[2020, 3]]}, "container-title": ["Jour"], "issn-type": [{"type": "print", "value": "12345678"}],
             "updated-by": [{"type": "retraction", "label": "Retraction", "DOI": "10.1/N", "updated": {"date-parts": [[2021, 4, 2]]}, "source": "retraction-watch"}]}
        r = Crossref.parse(m)
        self.assertTrue(r.is_retracted); self.assertEqual(r.retractions[0]["date"], "2021-04-02")
        self.assertEqual(r.authors[0][2], "0000-0000-0000-0001"); self.assertEqual(r.issn_print, "1234-5678")
        w = {"id": "https://openalex.org/W1", "title": "T", "publication_year": 2020, "doi": "https://doi.org/10.1/a", "is_retracted": True,
             "authorships": [{"author": {"display_name": "Jane Q Doe", "orcid": "https://orcid.org/0000-0000-0000-0002"}}],
             "primary_location": {"source": {"display_name": "Jour", "issn": ["1234-5678"], "is_in_doaj": True, "host_organization_name": "Pub"}},
             "biblio": {"volume": "3", "first_page": "1", "last_page": "9"}}
        o = OpenAlex.parse(w)
        self.assertEqual((o.authors[0][0], o.authors[0][1]), ("Doe", "Jane Q")); self.assertEqual(o.pages, "1--9"); self.assertTrue(o.in_doaj)


if __name__ == "__main__":
    unittest.main()
