# Changelog

## 3.0.0 — Knowledge index rebuilt

**This release requires a full re-index of your knowledge base.** Existing vectors were
built with different chunking and are not migrated. Nothing is lost — your documents are
the source of truth and are re-read from disk — but plan for the corpus to be
unavailable while it rebuilds. See *Upgrading* below.

The major version is for that: the storage layout, the retrieval path and the chunk
boundaries all changed. Everything else in this release is a fix or a speed-up.

### Indexing is roughly an order of magnitude faster

Measured on the same machine and the same model, ingesting the same corpus:

| | before | after |
|---|---|---|
| Fixed cost per document | ~3.5 s | **0.02–0.07 s** |
| Characters embedded per chunk | ~2,230 | **~800** |
| A 151 KB document | 19.3 s | **6.1 s** |
| A 2 KB note | 9.3 s | **0.1 s** |

Four things account for most of it:

- **Every chunk was being embedded twice.** The text kept for citations was stored in
  metadata, and metadata is concatenated ahead of the chunk before embedding — so each
  chunk was sent to the model as both its text and its own metadata. Roughly half of all
  embedding work was duplication.
- **Chunks are sized by what is embedded**, not by what they carry. Chunk size is
  computed as `size - len(metadata)`, and that metadata is now excluded before splitting
  rather than after, so a document with a long path and tags no longer loses most of its
  chunk to text it was never going to embed.
- **The index no longer rewrites itself on every document.** It kept a JSON copy of every
  node and rewrote the whole file each time a document was added, so adding one document
  got slower as the corpus grew. That file is gone.
- **Garbage collection is amortised** rather than run twice per document. In a process
  holding the ML stack a full collection costs ~250 ms, which on a small file exceeded
  parsing, chunking and embedding combined.

Ingest cost is now flat in corpus size: one constant pair of coefficients predicts it
across a corpus growing from 0 to 703 documents and 36,000 chunks.

### Keyword search moved into PostgreSQL

The keyword half of hybrid search now queries the full-text index PostgreSQL was already
maintaining, instead of an in-memory index rebuilt from a JSON file. Retrieval behaviour
is unchanged in shape — same fusion, same adaptive weighting, same reranking — but it no
longer depends on a file that had to be rewritten constantly, and it can filter by project
in SQL rather than after the fact.

Ranking is tuned for how people actually search. Rare words now decide a query: asking for
a specific name, identifier or error code puts the passage containing it first, instead of
letting common words in the rest of the question outvote it.

### Fixed

- **Client, project and job metadata was never indexed.** Both metadata indexers passed
  their metadata in a form the indexing layer rejects, so every attempt failed and logged a
  message that read like a transient problem. They now work.
- **Uploads went through a lesser pipeline than everything else.** Files uploaded through
  the UI were read as plain text — a PDF or DOCX arrived as mojibake, markdown was never
  sectioned, and re-indexing appended a second copy instead of replacing the first.
- **Re-indexing generated text left the old copy behind.** A repository summary, client
  profile or extracted relationship stayed in the index after being regenerated, competing
  with the current version at query time.
- Re-indexing a document is no longer slower on a large corpus than a small one.
- Documents with no headings no longer explode into tens of thousands of fragments.
- Audio and video files are no longer fed to a text reader. They are not yet indexed;
  they are simply left alone until transcription lands.

### Upgrading

1. Back up if you want a fallback: `pg_dump` your database, and keep `data/docstore.json`
   until you are satisfied.
2. Upgrade and restart.
3. Re-index. The knowledge base rebuilds from your documents; the background catch-up job
   will work through them on its own, or drive it directly for a bulk rebuild.
4. `data/docstore.json` and `data/index_store.json` are no longer used and can be deleted
   once the rebuild finishes.

If you run PostgreSQL with default memory settings, `scripts/tune_postgres_for_rag.sh`
raises the two that matter for a vector index of any size. It prints what it would change
with `--dry-run` and needs root only to apply.
