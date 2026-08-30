# Swarm Plan: Autoresearch Code-Tuning Run

Karpathy-style overnight experimentation on Guaardvark itself. Each arm is a
coding agent in an ISOLATED git worktree off the run branch
`autoresearch/run-{RUN_TAG}`. An arm makes ONE focused change, proves it with
the RAG eval harness (not its own opinion), and reports to the experiment
ledger. Code never merges to main automatically — the human reviews PendingFixes
and the run branch in the morning.

Director diagnosis (why this night is looking at code):
```
{DIAGNOSIS}
```

Ground rules for every arm:
- ONE experiment per arm: a single, describable change with a hypothesis that
  names the RAG metric it intends to move (hit-rate, MRR, or composite).
- Fitness is the eval harness. NEVER self-score. NEVER edit tests to make them
  pass; never edit `backend/services/rag_eval_harness.py`.
- Simplicity criterion: a tiny gain that adds ugly complexity is a discard.
  Deleting code and staying equal is a keep.
- Preserve-and-extend: pytest must be green AND RAG composite must not fall
  below the director baseline AND (composite delta ≥ 0.05 OR MRR/hit-rate up).
  A faster eval that drops retrieval is a discard.
- Report EVERY outcome to the ledger (the API will coerce an illegal keep to
  discard):
  `curl -s -X POST http://127.0.0.1:5000/api/autoresearch/experiments -H 'Content-Type: application/json' -d '{"run_tag": "{RUN_TAG}", "parameter": "<short-change-name>", "new_value": "<one-line description>", "status": "<keep|discard|crash>", "composite_score": <harness-composite>, "baseline_score": <director-baseline>, "delta": <composite-minus-baseline>, "pytest_passed": true, "retrieval_metrics": {"layer": "code", "mrr": 0, "hit_rate_at_k": 0, "baseline_mrr": 0, "baseline_hit_rate_at_k": 0}, "hypothesis": "<metric you aimed at>", "source": "code_arm"}'`
- Redirect noisy command output to files; read back only the lines you need.

## How to measure (do this, do not invent a score)

From the worktree, with `PYTHONPATH` set to the worktree root:

1. `python3 -m pytest backend/tests/test_autoresearch_integration.py backend/tests/test_rag_eval_harness.py -q`
   If this fails, status is `crash` or `discard` — do not keep.
2. Measure retrieval + judge subset by calling the harness the same way the
   director does (F0 then F1). Record composite, MRR, hit-rate. Compare to the
   diagnosis baseline.

## Task: Propose experiment slate
- files: docs/local-workspace-only/autoresearch-slate-{RUN_TAG}.md

Read `data/rag_research_program.md`, the diagnosis block above, the last run's
ledger (`GET /api/autoresearch/metrics?limit=50`), and the current RAG retrieval
implementation (`backend/services/indexing_service.py` — read-only;
`backend/services/rag_eval_harness.py` — read-only). Write a slate of 2–3
SMALL, independent, testable code improvements aimed at the bottleneck the
director measured (e.g. hit-rate stuck after top_k was exhausted). For each:
hypothesis, files to touch, how the harness will prove it. Do NOT implement
anything in this task.

## Task: Implement experiment arm A
- depends_on: propose-experiment-slate

Implement slate item 1 in this worktree. Run the measurement steps. Report to
the ledger. Commit with message `experiment({RUN_TAG}): <change-name>`.

## Task: Implement experiment arm B
- depends_on: propose-experiment-slate

Implement slate item 2, same rules as arm A.

## Task: Judge and summarize
- depends_on: implement-experiment-arm-a, implement-experiment-arm-b

You are the independent judge — you did NOT write these changes. For each arm:
read its diff cold (`git diff`), verify its ledger claim matches the code and
the harness numbers, and keep/discard on simplicity + preserve-and-extend.
Write the verdict table to
`docs/local-workspace-only/autoresearch-verdict-{RUN_TAG}.md`. Flag any arm
whose ledger claim you could not reproduce.
