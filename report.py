"""Markdown + JSON reports."""
from __future__ import annotations
import json
from collections import Counter

ICON = {"verified": "✅", "corrected": "🛠️", "retracted": "⛔", "not_found": "❓", "mismatch": "⚠️", "error": "🔌"}


def to_markdown(entries, results, source_name=""):
    c = Counter(r.status for r in results)
    out = [f"# BibTeX verification report{' – ' + source_name if source_name else ''}", "",
           f"Entries: **{len(results)}** · " + " · ".join(f"{ICON.get(k, '')} {k}: {v}" for k, v in sorted(c.items())), "",
           "| Key | Status | Exists | DOI ok | Retracted | Changes |", "|---|---|---|---|---|---|"]
    by_key = {e.key: e for e in entries}
    for r in results:
        f = by_key[r.key].fields
        out.append(f"| {r.key} | {ICON.get(r.status, '')} {r.status} | {f.get('x-publication-exists', '')} | "
                   f"{f.get('x-doi-points-to-the-correct-publication', '')} | {f.get('x-publication-retracted', '')} | {len(r.changes)} |")
    out += ["", "## Details", ""]
    for r in results:
        if r.status == "verified" and not r.notes:
            continue
        out.append(f"### {r.key} – {r.status}")
        for n in r.notes:
            out.append(f"- {n}")
        for name, old, new in r.changes:
            out.append(f"- **{name}**: `{old or '∅'}` → `{new}`")
        for e in r.errors:
            out.append(f"- source problem: {e}")
        out.append("")
    return "\n".join(out)


def to_json(entries, results):
    by_key = {e.key: e for e in entries}
    return json.dumps([{"key": r.key, "status": r.status, "changes": [{"field": n, "old": o, "new": w} for n, o, w in r.changes],
                        "notes": r.notes, "errors": r.errors,
                        "x": {k: v for k, v in by_key[r.key].fields.items() if k.startswith("x-")}} for r in results],
                      ensure_ascii=False, indent=2)
