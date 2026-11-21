"""Minimal web front end (stage 2). Run:  python -m bibcheck.webapp   -> http://127.0.0.1:5000
It reuses exactly the same pipeline as the CLI, so command-line functionality is unchanged."""
from __future__ import annotations
import html
import io
import os

from flask import Flask, request, send_file

from . import bibtex
from .cli import build_verifier, run
from .report import to_markdown

app = Flask(__name__)
LAST = {}
PAGE = """<!doctype html><meta charset=utf-8><title>bibcheck</title>
<body style="font-family:system-ui;max-width:60rem;margin:2rem auto;padding:0 1rem">
<h1>bibcheck</h1><p>Verify, correct and enrich a BibTeX file using Crossref, DataCite, OpenAlex, DBLP and DOAJ (no generative AI).</p>
<form method=post enctype=multipart/form-data>
<p><input type=file name=bib accept=.bib required></p>
<p><label>Contact e-mail (API polite pool): <input name=email type=email></label></p>
<p><label><input type=checkbox name=noadopt> only fill missing fields (never overwrite)</label></p>
<button>Check</button></form>{body}</body>"""


@app.route("/", methods=["GET", "POST"])
def index():
    body = ""
    if request.method == "POST":
        text = request.files["bib"].read().decode("utf-8", "replace")
        v = build_verifier(request.form.get("email", ""), os.environ.get("BIBCHECK_LISTS"),
                           True, not request.form.get("noadopt"))
        entries, extras, results = run(text, v)
        LAST["bib"], LAST["md"] = bibtex.dump(entries, extras), to_markdown(entries, results)
        rows = "".join(f"<tr><td>{html.escape(r.key)}</td><td>{r.status}</td><td>{html.escape('; '.join(r.notes))}</td></tr>" for r in results)
        body = (f"<h2>Result</h2><p><a href=/download/bib>enriched .bib</a> · <a href=/download/md>report</a></p>"
                f"<table border=1 cellpadding=4><tr><th>key<th>status<th>notes</tr>{rows}</table>")
    return PAGE.replace("{body}", body)


@app.route("/download/<kind>")
def download(kind):
    data = LAST.get(kind)
    if not data:
        return "nothing to download yet", 404
    name, mime = ("verified.bib", "text/plain") if kind == "bib" else ("report.md", "text/markdown")
    return send_file(io.BytesIO(data.encode("utf-8")), as_attachment=True, download_name=name, mimetype=mime)


if __name__ == "__main__":
    app.run(debug=False)
