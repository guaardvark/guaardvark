"""The chat turn's questions to the loaded chat model (decision_data entries).

Each question here replaces or checks one of the chat engine's own rules, and
each is asked through nl_control_plane.decide(): off (the default) is today's
rule with nothing asked; shadow keeps today's rule and asks after the reply;
on uses the model's answer once that model's measured accuracy clears the
question's floor (decision_evidence).

Retrieval, per turn:

* ``skip_rag``: could the user's indexed documents help with this message?
  Asked beside the search, never before it, so a yes costs no wait and a no
  ends the wait for the search. A no on a turn the rule would not search
  stays a skip; a yes there searches.
* ``context_answers``: does this passage hold what the message asks for? A
  no leaves the passage out of the prompt, which keeps a document that only
  shares words with the message out of the answer. A passage the reranker is
  confident about is kept without asking (CONFIDENT_RERANK_SCORE). An
  unrelated passage costs more than prompt space: with one in the prompt
  gemma4:12b wrote 186 tokens of hidden reasoning for "What is the capital of
  Australia?" (3.9 s) against 39 without (1.1 s).
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

logger = logging.getLogger(__name__)

# The longest the turn waits for one answer; past it the rule's answer stands.
# A question on gemma4:12b at a 32K window answered in 0.3-0.5 s (2026-11-05).
ANSWER_TIMEOUT_S = 2.0
# The longest the turn waits for the search once the model said documents
# could help (the search alone took 0.7-2.0 s warm, 18.6 s once cold).
SEARCH_WAIT_S = 60.0
# Passages checked per turn; any after these are kept unchecked.
MAX_PASSAGE_CHECKS = 5
# A passage the reranker scores at least this high is kept without asking.
# Measured on this box's index with BAAI/bge-reranker-v2-m3 (2026-11-05,
# probe_rerank_scores in the decision-wiring notes): passages that answered
# scored 0.91-0.99 (Apollo 15 rover distance, Apollo 13 cause); an unrelated
# one that only named Australia scored 0.82 for "What is the capital of
# Australia?", and README passages listing Guaardvark's features 0.59-0.86.
CONFIDENT_RERANK_SCORE = 0.90
# The file-name list the skip_rag question reads (its state field: 900 chars),
# kept for this long before the index is read again.
LISTING_MAX_CHARS = 900
LISTING_TTL_S = 300.0

_search_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="chat-search")
_listing: Dict[str, Any] = {"at": 0.0, "text": ""}
_listing_lock = threading.Lock()


def mode(decision: str) -> str:
    """off, shadow or on; off whenever the mode cannot be read."""
    try:
        from backend.services.decision_settings import get_decision_mode
        return get_decision_mode(decision)
    except Exception:  # noqa: BLE001 - an unreadable mode is today's rule
        return "off"


def any_active(decisions: Iterable[str]) -> bool:
    return any(mode(d) != "off" for d in decisions)


_turns = {"running": 0}
_turns_lock = threading.Lock()


def turn_started() -> None:
    with _turns_lock:
        _turns["running"] += 1


def turn_ended() -> None:
    with _turns_lock:
        _turns["running"] = max(0, _turns["running"] - 1)


def turns_running() -> bool:
    """True while a chat turn is running in this process."""
    return _turns["running"] > 0


def _install_busy_check() -> None:
    try:
        from backend.services import nl_control_plane
        nl_control_plane.busy_check = turns_running
    except Exception:  # noqa: BLE001
        pass


_install_busy_check()


def turn_context(request_id: Optional[str], model: Optional[str]) -> dict:
    """The ctx dict decide() takes for one chat turn. Shadow questions wait in
    it until the turn ends (finish_turn)."""
    from backend.services.nl_control_plane import SHADOW_KEY
    return {"request_id": request_id, "model": model, SHADOW_KEY: []}


def finish_turn(ctx: Optional[dict]) -> None:
    """Ask the turn's shadow questions now that the reply is out."""
    if not ctx:
        return
    try:
        from backend.services.nl_control_plane import run_shadow
        run_shadow(ctx)
    except Exception:  # noqa: BLE001
        logger.debug("shadow questions not started", exc_info=True)


def ask(decision: str, state: Dict[str, Any], *, fallback: Any, ctx: Optional[dict]):
    from backend.services.nl_control_plane import decide
    return decide(decision, state, fallback=fallback, ctx=ctx, timeout_s=ANSWER_TIMEOUT_S)


def _note(result) -> Dict[str, Any]:
    return {"answer": result.answer, "source": result.source, "reason": result.reason,
            "ms": result.latency_ms}


# --- what the index holds ----------------------------------------------------

def knowledge_listing() -> str:
    """The indexed file names, largest first, as the skip_rag question reads
    them: "a.pdf, b.md and 12 more". Empty when nothing is indexed or the
    index cannot be read."""
    now = time.monotonic()
    with _listing_lock:
        if _listing["at"] and now - _listing["at"] < LISTING_TTL_S:
            return _listing["text"]
    text = _read_listing()
    with _listing_lock:
        _listing.update(at=now, text=text)
    return text


def _read_listing() -> str:
    try:
        from backend.tools import knowledge_tools as kt
        table, err = kt._table()
        if err or not table:
            return ""
        rows, qerr = kt._query(
            f"SELECT metadata_->>'source_filename' AS name, count(*) FROM \"{table}\" "
            "WHERE metadata_->>'source_filename' IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1", ())
        if qerr or not rows:
            return ""
    except Exception:  # noqa: BLE001 - no listing means the question is not asked
        logger.debug("knowledge listing unavailable", exc_info=True)
        return ""
    return format_listing([str(r[0]) for r in rows if r and r[0]])


def format_listing(names: list) -> str:
    """Names joined by ", ", cut to LISTING_MAX_CHARS with " and N more"."""
    shown: list = []
    for i, name in enumerate(names):
        rest = len(names) - i - 1
        tail = f" and {rest} more" if rest else ""
        candidate = ", ".join(shown + [name])
        if len(candidate) + len(tail) > LISTING_MAX_CHARS:
            more = len(names) - len(shown)
            return ", ".join(shown) + f" and {more} more" if shown else ""
        shown.append(name)
    return ", ".join(shown)


# --- retrieval ---------------------------------------------------------------

def _in_app(app, fn: Callable, *args):
    if app is None:
        return fn(*args)
    with app.app_context():
        return fn(*args)


def search(find: Callable[[str], dict], message: str, *, previous_message: str, by_rule: bool,
           ctx: Optional[dict], app=None) -> Tuple[Optional[dict], Optional[Dict[str, Any]]]:
    """(what ``find`` found, or None when the turn does not search; a note on
    the skip_rag answer, or None when it was not asked).

    ``find`` is the search itself, with no effect on the turn, since it may
    finish after the turn has moved on. ``by_rule`` is today's answer.
    """
    listing = knowledge_listing() if mode("skip_rag") != "off" else ""
    if not listing:
        return (find(message) if by_rule else None), None
    from backend.utils.text_cut import cut_on_whitespace
    state = {"message": message, "previous_message": cut_on_whitespace(previous_message or "", 300),
             "documents": listing}
    if not by_rule:
        result = ask("skip_rag", state, fallback=False, ctx=ctx)
        return (find(message) if result.answer is True else None), _note(result)
    pending = _search_pool.submit(_in_app, app, find, message)
    result = ask("skip_rag", state, fallback=True, ctx=ctx)
    if result.answer is False:
        return None, _note(result)
    return pending.result(timeout=SEARCH_WAIT_S), _note(result)


def check_passages(found: dict, message: str, *, ctx: Optional[dict]) -> Tuple[dict, list]:
    """``found`` without the passages the model judged not to answer the
    message, and the sources left out."""
    passages = list(found.get("passages") or [])
    if not passages or mode("context_answers") == "off":
        return found, []
    kept, left_out, asked = [], [], 0
    for passage in passages:
        score = passage.get("score")
        if asked >= MAX_PASSAGE_CHECKS or (isinstance(score, (int, float)) and score >= CONFIDENT_RERANK_SCORE):
            kept.append(passage)
            continue
        asked += 1
        result = ask("context_answers", {"question": message, "passage": passage["text"]},
                     fallback=True, ctx=ctx)
        if result.answer is False:
            left_out.append({"source": passage.get("source"), "score": round(score, 3)
                             if isinstance(score, (int, float)) else None})
        else:
            kept.append(passage)
    return {**found, "passages": kept}, left_out
