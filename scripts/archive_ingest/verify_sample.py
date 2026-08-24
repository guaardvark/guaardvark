#!/usr/bin/env python3
"""Index a small representative sample and report what each parser path produced.

Indexing the whole archive to discover a defect costs hours; a dozen documents
chosen to span every parser path costs minutes and finds the same defects. The
sample is deliberately stratified by file type rather than taken off the top of
the list, because the failure modes are per-parser: markdown sectioning, the
SimpleDirectoryReader fallback, Docling with page provenance, and Docling on a
scanned document that yields no text at all.

Reports what a caller would actually receive: which parser ran, whether chunks
kept a source and a section, whether hierarchical chunking held or fell back to
a simpler splitter, and whether the text arrived damaged.
"""

import argparse
import collections
import logging
import os
import warnings

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
for _noisy in ("backend", "llama_index", "docling", "httpx", "urllib3", "sentence_transformers"):
    logging.getLogger(_noisy).setLevel(logging.ERROR)
log = logging.getLogger("verify_sample")

FALLBACK_CHUNK_TYPES = {"standard_text", "semantic_fallback", "raw", "complete_file"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path-prefix", default="ArchiveHistory/")
    ap.add_argument("--per-type", type=int, default=3, help="Documents per file extension")
    ap.add_argument("--max-kb", type=int, default=96,
                    help="Skip documents larger than this. The point of this loop is speed, and a "
                         "single 500KB file can produce thousands of chunks and take ten minutes to "
                         "embed while exercising exactly the same code paths as a 20KB one. 0 = no cap.")
    ap.add_argument("--reindex", action="store_true",
                    help="Include already-INDEXED documents (re-runs them)")
    args = ap.parse_args()

    from flask import Flask
    from backend.models import db, Document as DBDocument
    from backend.config import DATABASE_URL, UPLOAD_DIR

    app = Flask(__name__)
    app.config.update({"SQLALCHEMY_DATABASE_URI": DATABASE_URL,
                       "SQLALCHEMY_TRACK_MODIFICATIONS": False})
    db.init_app(app)

    with app.app_context():
        import backend.services.indexing_service as isvc
        isvc.get_or_create_index()
        if isvc.index is None:
            log.error("index unavailable")
            return 1

        q = DBDocument.query.filter(DBDocument.path.like(f"{args.path_prefix}%"))
        if not args.reindex:
            q = q.filter(DBDocument.index_status != "INDEXED")
        candidates = q.order_by(DBDocument.id).all()

        # Stratify: the parsers differ by extension, so sampling off the top of the
        # list would exercise one path repeatedly and miss the rest.
        by_ext = collections.defaultdict(list)
        oversized = 0
        for d in candidates:
            _rel = getattr(d, "file_path", None) or d.path
            _fp = _rel if os.path.isabs(_rel) else os.path.join(UPLOAD_DIR, _rel)
            if args.max_kb:
                try:
                    if os.path.getsize(_fp) > args.max_kb * 1024:
                        oversized += 1
                        continue
                except OSError:
                    continue
            by_ext[os.path.splitext(d.filename or "")[1].lower()].append(d)
        if oversized:
            log.info("skipped %d document(s) over %dKB (--max-kb 0 to include them)",
                     oversized, args.max_kb)
        sample = []
        for ext in sorted(by_ext):
            sample.extend(by_ext[ext][: args.per_type])

        log.info("sample: %d documents across %s",
                 len(sample), {e: min(len(v), args.per_type) for e, v in sorted(by_ext.items())})

        rows, problems = [], []
        import time as _time
        for _i, doc in enumerate(sample, 1):
            _t0 = _time.time()
            # Report per document, not only at the end: the binary parsers take
            # tens of seconds each, and a harness meant for a fast iteration loop
            # that shows nothing until it finishes is unusable for that loop.
            log.info("[%d/%d] %s", _i, len(sample), (doc.filename or "")[:60])
            # Watermark so the measurement below counts only rows this run added.
            _mid = isvc._pg_connect()
            try:
                with _mid.cursor() as _c:
                    _c.execute(f'SELECT coalesce(max(id), 0) FROM "data_{isvc._pg_table_name(None)}"')
                    _max_id_before = _c.fetchone()[0]
            finally:
                _mid.close()
            rel = getattr(doc, "file_path", None) or doc.path
            path = rel if os.path.isabs(rel) else os.path.join(UPLOAD_DIR, rel)
            ext = os.path.splitext(doc.filename or "")[1].lower()
            if not os.path.exists(path):
                problems.append(f"{doc.filename}: file missing at {path}")
                continue

            # Deliberately NOT pre-parsing to inspect: add_file_to_index parses the
            # file itself, so doing it here too ran every Docling document through
            # the expensive path twice. Everything wanted is carried onto the chunks
            # (parsed_by, page_label, heading_path, text_quality), so read it back
            # from what actually landed -- which is the more honest measurement anyway.
            try:
                ok = isvc.add_file_to_index(path, doc)
            except Exception as exc:
                problems.append(f"{doc.filename}: indexing raised {exc.__class__.__name__}: {exc}")
                doc.index_status = "ERROR"
                db.session.commit()
                continue
            doc.index_status = "INDEXED" if ok else "ERROR"
            db.session.commit()

            # Inspect what actually landed for this file.
            table = isvc._pg_table_name(None)
            got = isvc._pg_connect()
            try:
                with got.cursor() as cur:
                    # Only THIS run's rows. Matching on source_filename alone counted
                    # every historical chunk for the file, so re-running the harness
                    # inflated its own numbers -- one document appeared to produce
                    # 20,480 chunks.
                    cur.execute(
                        f"""SELECT metadata_->>'chunk_type',
                                   metadata_->>'source_filename',
                                   metadata_->>'heading_path',
                                   metadata_->>'page_label',
                                   metadata_->>'parsed_by',
                                   metadata_->>'text_quality'
                            FROM "data_{table}"
                            WHERE metadata_->>'source_filename' = %s
                              AND id > %s""",
                        (doc.filename, _max_id_before),
                    )
                    chunk_rows = cur.fetchall()
            finally:
                got.close()

            parser = next((c[4] for c in chunk_rows if c[4]), "SimpleDirectoryReader")
            damaged = sum(1 for c in chunk_rows if c[5] == "degraded")
            fallback = sum(1 for c in chunk_rows if c[0] in FALLBACK_CHUNK_TYPES)
            if not ok or not chunk_rows:
                problems.append(f"{doc.filename}: produced NO chunks"
                                + (" (scanned PDF, OCR not installed)" if ext == ".pdf" else ""))
            log.info("      %s: %d parsed doc(s), %d chunk(s), %.1fs",
                     parser, 1, len(chunk_rows), _time.time() - _t0)
            rows.append({
                "file": doc.filename, "ext": ext, "parser": parser, "docs": 1,
                "chunks": len(chunk_rows),
                "src": sum(1 for c in chunk_rows if c[1]),
                "section": sum(1 for c in chunk_rows if c[2]),
                "page": sum(1 for c in chunk_rows if c[3]),
                "fallback": fallback, "damaged": damaged,
            })
            if chunk_rows and not any(c[1] for c in chunk_rows):
                problems.append(f"{doc.filename}: chunks carry NO source_filename — not citable")
            if fallback:
                problems.append(f"{doc.filename}: {fallback} chunk(s) used a fallback splitter")

        print("\n" + "=" * 104)
        print(f"{'file':<46}{'parser':<20}{'docs':>5}{'chunks':>7}{'src':>5}{'sect':>6}{'page':>6}{'fb':>4}{'dmg':>5}")
        print("-" * 104)
        for r in rows:
            print(f"{r['file'][:45]:<46}{r['parser'][:19]:<20}{r['docs']:>5}{r['chunks']:>7}"
                  f"{r['src']:>5}{r['section']:>6}{r['page']:>6}{r['fallback']:>4}{r['damaged']:>5}")
        print("=" * 104)

        by_parser = collections.Counter(r["parser"] for r in rows)
        print("parsers exercised:", dict(by_parser))
        print(f"chunks: {sum(r['chunks'] for r in rows)} | "
              f"with source: {sum(r['src'] for r in rows)} | "
              f"with section: {sum(r['section'] for r in rows)} | "
              f"with page: {sum(r['page'] for r in rows)}")

        if problems:
            print(f"\nPROBLEMS ({len(problems)}):")
            for p in problems:
                print("  -", p)
        else:
            print("\nno problems detected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
