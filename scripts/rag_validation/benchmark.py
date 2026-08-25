#!/usr/bin/env python3
"""Measure what ingest costs, phase by phase.

Two numbers decide whether a corpus is tractable: what each document costs before
any of its content is looked at, and what each chunk costs after that. A set of
large files measures only the second and hides the first, which is the term that
decides whether a hundred thousand small files is an afternoon or a fortnight. The
five fixtures deliberately span three orders of magnitude of size so both can be
fitted rather than guessed.

The run reports how much of the wall clock its own phases account for. An earlier
attempt at this decomposition explained 51% of a 93-minute run and was used to
rank optimisations anyway; the reconciliation line exists so that cannot happen
quietly again.
"""

import argparse
import json
import os
import statistics
import sys
import time
import warnings

warnings.filterwarnings("ignore")

PHASES = ("index_init_ms", "parse_ms", "chunk_ms", "purge_ms", "embed_ms",
          "vstore_ms", "persist_ms")


def say(msg=""):
    # Root logging is reset to WARNING once backend.app is imported, so progress
    # goes to stdout directly rather than through the logging module.
    print(msg, flush=True)


def fit_linear(xs, ys):
    """Least-squares fit of ys = intercept + slope*xs. Returns (intercept, slope)."""
    n = len(xs)
    if n < 2:
        return (ys[0] if ys else 0.0), 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return my, 0.0
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
    return my - slope * mx, slope


def chunk_stats(db, table, doc_ids):
    """Body-length distribution, with the contextual prefix removed.

    The prefix is prepended after chunking, so counting it makes chunks look
    healthier than they are -- roughly 120 characters of it per chunk.
    """
    from sqlalchemy import text as sqltext
    rows = db.session.execute(sqltext(
        f'SELECT length(coalesce(metadata_->>\'original_text\', text)) AS body '
        f'FROM "{table}" '
        f'WHERE split_part(metadata_->>\'document_id\', \'_\', 2) = ANY(:ids)'
    ), {"ids": [str(i) for i in doc_ids]}).fetchall()
    bodies = sorted(r[0] or 0 for r in rows)
    if not bodies:
        return {"chunks": 0}
    return {
        "chunks": len(bodies),
        "median_body": bodies[len(bodies) // 2],
        "mean_body": round(sum(bodies) / len(bodies)),
        "under_120": sum(1 for b in bodies if b < 120),
        "over_8000": sum(1 for b in bodies if b > 8000),
        "max_body": bodies[-1],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prefix", default="RagValidation/", help="Document path prefix")
    ap.add_argument("--repeats", type=int, default=3,
                    help="Runs to median over; GPU contention makes a single run noisy")
    ap.add_argument("--json-out", default=None, help="Write the full record here")
    ap.add_argument("--no-warmup", action="store_true",
                    help="Skip the discarded warmup pass (it exists to keep one-time "
                         "model-load cost out of the per-document fit)")
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
            say("index unavailable - aborting")
            return 1

        docs = (DBDocument.query
                .filter(DBDocument.path.like(f"{args.prefix}%"))
                .order_by(DBDocument.size).all())
        if not docs:
            say(f"no documents under {args.prefix}")
            return 2
        say(f"{len(docs)} fixture(s), {args.repeats} repeat(s)\n")

        per_doc = {d.id: [] for d in docs}
        meta = {d.id: {"name": d.filename, "size": d.size or 0} for d in docs}

        if not args.no_warmup:
            # A cold process is slow for more than one document, and for more than
            # one reason: the embedding model has to reach VRAM, the chunker builds
            # its clients, and the GPU itself ramps. Measured on the same file ten
            # times in a row: 23.9s, 4.9s, then 3.4s +/- 0.05 for the rest. A single
            # discarded document is not enough -- it leaves the second run 40% high,
            # and charging that to "fixed cost per document" is how a startup
            # transient gets mistaken for a per-document cost.
            #
            # So warm until two consecutive runs agree within 20%, or give up after a
            # few tries and say so rather than pretending the numbers are settled.
            warm = docs[-1]  # the largest, so the ramp is actually exercised
            wp = warm.path if os.path.isabs(warm.path) else os.path.join(UPLOAD_DIR, warm.path)
            if os.path.exists(wp):
                say("warmup (discarded) ...")
                prev = None
                for attempt in range(1, 6):
                    t0 = time.perf_counter()
                    isvc.add_file_to_index(wp, warm)
                    db.session.commit()
                    took = time.perf_counter() - t0
                    say(f"  warmup {attempt}: {took:5.2f}s")
                    if prev is not None and abs(took - prev) <= 0.20 * min(took, prev):
                        break
                    prev = took
                else:
                    say("  ^ still not settled; treat the numbers below as noisy")
                say("")

        for rep in range(1, args.repeats + 1):
            say(f"--- run {rep}/{args.repeats} ---")
            for d in docs:
                rel = getattr(d, "file_path", None) or d.path
                path = rel if os.path.isabs(rel) else os.path.join(UPLOAD_DIR, rel)
                if not os.path.exists(path):
                    say(f"  {d.filename}: file missing, skipping")
                    continue
                t0 = time.perf_counter()
                ok = isvc.add_file_to_index(path, d)
                wall_ms = (time.perf_counter() - t0) * 1000.0
                if not ok:
                    say(f"  {d.filename}: add_file_to_index returned falsy")
                    continue
                rec = isvc.get_last_phase_timings()
                rec["wall_ms"] = round(wall_ms, 1)
                accounted = sum(rec.get(p, 0.0) for p in PHASES)
                rec["accounted_pct"] = round(100.0 * accounted / wall_ms, 1) if wall_ms else 0.0
                per_doc[d.id].append(rec)
                say(f"  {d.filename:<28} {rec['nodes']:>5} nodes  "
                    f"{wall_ms/1000:6.2f}s  accounted {rec['accounted_pct']:5.1f}%")
                db.session.commit()
            say()

        # ---- report ----
        say("=" * 78)
        say(f"  {'document':<28} {'nodes':>6} {'wall':>8} {'parse':>8} {'chunk':>8} "
            f"{'embed':>8} {'vstore':>8} {'persist':>8} {'unacct':>8}")
        say("  " + "-" * 76)
        xs, ys, summary = [], [], []
        for d in docs:
            recs = per_doc[d.id]
            if not recs:
                continue
            med = lambda k: statistics.median([r.get(k, 0.0) for r in recs])
            nodes = int(med("nodes"))
            wall = med("wall_ms")
            xs.append(nodes)
            ys.append(wall)
            row = {"name": meta[d.id]["name"], "size": meta[d.id]["size"], "nodes": nodes,
                   "wall_ms": round(wall, 1),
                   **{p: round(med(p), 1) for p in PHASES},
                   "accounted_pct": round(med("accounted_pct"), 1),
                   "embed_calls": int(med("embed_calls"))}
            summary.append(row)
            unacct = wall - sum(row.get(p, 0.0) for p in PHASES)
            row["unaccounted_ms"] = round(unacct, 1)
            say(f"  {row['name']:<28} {nodes:>6} {wall/1000:7.2f}s "
                f"{row['parse_ms']/1000:7.2f}s {row['chunk_ms']/1000:7.2f}s "
                f"{row['embed_ms']/1000:7.2f}s {row['vstore_ms']/1000:7.2f}s "
                f"{row['persist_ms']/1000:7.2f}s {unacct/1000:7.2f}s")

        worst = min((r["accounted_pct"] for r in summary), default=0.0)
        say()
        say(f"  phase timings account for {worst:.1f}%-"
            f"{max((r['accounted_pct'] for r in summary), default=0):.1f}% of wall clock")
        if worst < 90.0:
            say("  ^ BELOW THE 90% EXIT CRITERION - do not rank optimisations on this yet")

        intercept, slope = fit_linear(xs, ys)
        say()
        say(f"  fixed cost per document : {intercept/1000:6.2f} s")
        say(f"  marginal cost per chunk : {slope:6.1f} ms")
        for n, label in ((10, "10-chunk doc"), (60, "60-chunk doc")):
            secs = (intercept + slope * n) / 1000.0
            say(f"  => {label:<14} {secs:6.2f} s  =>  {3600.0/secs:8.0f} docs/hour")

        table = "data_" + (isvc.resolve_existing_vector_table(None) or "")
        stats = chunk_stats(db, table, [d.id for d in docs])
        say()
        say(f"  chunks={stats.get('chunks')}  median_body={stats.get('median_body')}ch  "
            f"mean={stats.get('mean_body')}ch  under_120={stats.get('under_120')}  "
            f"over_8000={stats.get('over_8000')}  max={stats.get('max_body')}ch")

        if args.json_out:
            with open(args.json_out, "w") as f:
                json.dump({"documents": summary, "fixed_ms": intercept,
                           "per_chunk_ms": slope, "chunks": stats}, f, indent=2)
            say(f"\n  record -> {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
