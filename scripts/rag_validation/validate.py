#!/usr/bin/env python3
"""Score retrieval against known answers.

Each question targets a fact planted by make_test_docs.py in exactly one place
(or, for one question, two). That makes the result checkable rather than
impressionistic: the needle either came back in the retrieved passages or it did
not, and the source document either ranked or it did not.

Every result records the provenance trace alongside the score. A miss on a
contended GPU, where retrieval fell back to a single leg, is a different fact
from a miss with every leg healthy -- and without the trace the two are
indistinguishable, which is how a resource problem gets filed as a relevance
regression.
"""

import argparse
import json
import sys

QUESTIONS = [
    {
        "id": "flat-needle",
        "q": "What date was the Meridian Protocol ratified, and how many member stations sent delegates?",
        "needle": "flat",
        "expect_file": "flat_prose_no_headings.md",
        "why": "Large document with no headings — the sectioning path's degenerate case.",
    },
    {
        "id": "structured-deep",
        "q": "How many times must the emergency purge valve be turned before venting?",
        "needle": "structured",
        "expect_file": "structured_manual.md",
        "why": "Fact buried four heading levels deep; needs the heading path to survive.",
    },
    {
        "id": "prose-in-mixed",
        "q": "What happens to throughput when the retry budget grows too large?",
        "needle": "code_prose",
        "expect_file": "mixed_prose_and_code.md",
        "why": "Prose in a code-heavy file — must not be chunked at code scale.",
    },
    {
        "id": "code-in-mixed",
        "q": "What is the maximum retry budget set in the request handler?",
        "needle": "code_body",
        "expect_file": "mixed_prose_and_code.md",
        "why": "The answer is in a code block, not prose.",
    },
    {
        "id": "docx-late-page",
        "q": "Which coolant sample recorded the highest drift in the survey?",
        "needle": "docx",
        "expect_file": "field_report.docx",
        "why": "Binary parsed by Docling; the fact sits on a later page.",
    },
    {
        "id": "tiny-note",
        "q": "Who holds relay bypass authority for Module 7?",
        "needle": "tiny",
        "expect_file": "tiny_note.md",
        "why": "A very short document -- must not be lost among larger neighbours.",
    },
    {
        "id": "shared-fact",
        "q": "What ledger anomaly level mandates an immediate quarantine review?",
        "needle": "shared",
        "expect_file": None,  # present in two documents; either is correct
        "why": "Appears in two documents — checks ranking, not just recall.",
    },
]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", default="data/uploads/RagValidation",
                    help="Directory holding needles.json")
    ap.add_argument("--top-k", type=int, default=8)
    ap.add_argument("--json", action="store_true", help="Emit machine-readable results")
    args = ap.parse_args()

    import pathlib
    needles = json.loads((pathlib.Path(args.corpus) / "needles.json").read_text())

    from flask import Flask
    from backend.models import db
    from backend.config import DATABASE_URL
    app = Flask(__name__)
    app.config.update({"SQLALCHEMY_DATABASE_URI": DATABASE_URL,
                       "SQLALCHEMY_TRACK_MODIFICATIONS": False})
    db.init_app(app)

    rows = []
    with app.app_context():
        import backend.services.indexing_service as isvc
        isvc.get_or_create_index()
        for spec in QUESTIONS:
            needle = needles[spec["needle"]]
            out = isvc.search_with_llamaindex(spec["q"], max_chunks=args.top_k,
                                              with_trace=True)
            results = out.get("results", []) if isinstance(out, dict) else (out or [])
            trace = out.get("trace", {}) if isinstance(out, dict) else {}

            # Normalise whitespace before matching: chunking may re-wrap, and a
            # line break inside the sentence is not a retrieval failure.
            def norm(s):
                return " ".join((s or "").split())
            n_needle = norm(needle)
            hit_rank = None
            for i, r in enumerate(results, 1):
                if n_needle in norm(r.get("text", "")):
                    hit_rank = i
                    break

            # The loaders write `source_filename`; `file_path` is the absolute path
            # and `file_name` is a LlamaIndex convention this pipeline does not use.
            files = [((r.get("metadata") or {}).get("source_filename")
                      or (r.get("metadata") or {}).get("file_path")
                      or (r.get("metadata") or {}).get("file_name") or "")
                     for r in results]
            file_rank = None
            if spec["expect_file"]:
                for i, f in enumerate(files, 1):
                    if spec["expect_file"] in (f or ""):
                        file_rank = i
                        break

            rows.append({
                "id": spec["id"],
                "needle_rank": hit_rank,
                "file_rank": file_rank,
                "expect_file": spec["expect_file"],
                "results": len(results),
                "legs": trace.get("legs"),
                "degraded": trace.get("degraded"),
                "degraded_reason": trace.get("degraded_reason"),
                "rerank": trace.get("rerank_applied", trace.get("rerank")),
                "why": spec["why"],
            })

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    passed = sum(1 for r in rows if r["needle_rank"])
    print(f"\n  {'question':<18} {'needle':>8} {'file':>7} {'hits':>5}  legs / notes")
    print("  " + "-" * 74)
    for r in rows:
        nr = f"@{r['needle_rank']}" if r["needle_rank"] else "MISS"
        fr = f"@{r['file_rank']}" if r["file_rank"] else ("-" if not r["expect_file"] else "MISS")
        legs = ",".join(r["legs"] or []) or "?"
        note = legs + ("  DEGRADED: " + str(r["degraded_reason"]) if r["degraded"] else "")
        print(f"  {r['id']:<18} {nr:>8} {fr:>7} {r['results']:>5}  {note}")
    print(f"\n  {passed}/{len(rows)} needles retrieved in the top {args.top_k}")
    for r in rows:
        if not r["needle_rank"]:
            print(f"    MISS {r['id']}: {r['why']}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
