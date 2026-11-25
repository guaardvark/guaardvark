"""Shared natural-language control plane: classify → normalize → dispatch.

Guaardvark grew the same NL pattern in ~6 places (outreach intent, the media /
video / music-video directors, the orchestrator): freeform text → a local Ollama
JSON classification → a validated structured intent → an intent→handler dispatch.
Until now each feature copy-pasted the primitive. This module is the single
validated implementation new NL-controlled features build on.

The canonical exemplar it generalizes is
`backend/services/social_outreach/intent.py`. Three primitives:

  json_chat(system, user)         → one-shot Ollama call, parsed JSON (tolerant)
  normalize(raw, valid_intents=…) → whitelist intent, clamp confidence, coerce ints
  dispatch(classification, …)     → intent→handler table with a default fallback

Feature-specific validation (platform whitelists, topic denylists, regex
fallbacks) stays in the feature module and layers on top of `normalize`.

``json_chat_result`` is the same call reporting why it failed (timeout, call
error, a reply that is not JSON) instead of folding every failure into
``on_error``; ``decide()`` needs the difference.

``decide(decision, state, fallback=...)`` asks one question declared in
``decision_data.DECISIONS`` and returns a ``DecisionResult``; see its
docstring for the modes and the rule for when the model is asked.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JsonChatResult:
    """One json_chat call: the parsed reply, or why there is none.

    ``failure`` is None on success, else ``"timeout"`` (no reply within the
    timeout), ``"error"`` (the call failed: Ollama down, model missing) or
    ``"parse_fail"`` (a reply came back with no JSON in it). ``raw`` is the
    reply text as received, empty when the call failed.
    """

    data: Any = None
    failure: Optional[str] = None
    error: str = ""
    raw: str = ""


def parse_json_reply(raw: str) -> tuple[bool, Any]:
    """(ok, value) for a model's JSON reply text.

    format="json" usually yields clean JSON, but models occasionally wrap or
    truncate it, so the first-to-last brace span is tried after the whole text.
    """
    raw = (raw or "").strip()
    try:
        return True, json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return True, json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return False, None


# One client per (host, timeout): building an httpx client per call costs more
# than the decisions it carries.
_clients: dict = {}
_clients_lock = threading.Lock()


def _client_for(timeout: float):
    import ollama
    from backend.utils.ollama_resource_manager import get_ollama_base_url

    key = (get_ollama_base_url(), float(timeout))
    with _clients_lock:
        client = _clients.get(key)
        if client is None:
            client = ollama.Client(host=key[0], timeout=key[1])
            _clients[key] = client
    return client


def _is_timeout(exc: BaseException) -> bool:
    try:
        import httpx
        if isinstance(exc, httpx.TimeoutException):
            return True
    except ImportError:  # pragma: no cover - httpx ships with the ollama client
        pass
    return isinstance(exc, TimeoutError)


def json_chat_result(
    system: str,
    user: str,
    *,
    model: Optional[str] = None,
    temperature: float = 0.6,
    timeout: Optional[float] = None,
    num_ctx: Optional[int] = None,
) -> JsonChatResult:
    """One-shot Ollama chat with format="json". Never raises.

    ``timeout`` (seconds) bounds the call through a client built with that
    timeout; None keeps the module-level ``ollama.chat``, which waits as long
    as Ollama takes. ``num_ctx`` overrides the sized context; pass the
    context length the model is already loaded with, since a request asking
    for another size makes Ollama reload the runner.
    """
    import ollama
    from backend.utils.ollama_resource_manager import think_payload, request_options

    if model is None:
        from backend.config import get_default_llm
        model = get_default_llm()

    try:
        chat = _client_for(timeout).chat if timeout is not None else ollama.chat
        response = chat(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            format="json",
            options=request_options(model, temperature=temperature, num_ctx=num_ctx),
            **think_payload(model),
        )
    except Exception as e:  # noqa: BLE001 — degrade, don't crash the caller
        failure = "timeout" if _is_timeout(e) else "error"
        logger.error("nl_control_plane.json_chat: ollama.chat failed (%s): %s", failure, e)
        return JsonChatResult(failure=failure, error=str(e))

    msg = getattr(response, "message", None)
    if msg is None and isinstance(response, dict):
        msg = response.get("message")
    content = getattr(msg, "content", None)
    if content is None and isinstance(msg, dict):
        content = msg.get("content", "")
    raw = (content or "").strip()

    ok, value = parse_json_reply(raw)
    if ok:
        return JsonChatResult(data=value, raw=raw)
    logger.warning("nl_control_plane.json_chat: non-JSON response: %s", raw[:200])
    return JsonChatResult(failure="parse_fail", error="reply is not JSON", raw=raw)


def json_chat(
    system: str,
    user: str,
    *,
    model: Optional[str] = None,
    temperature: float = 0.6,
    on_error: Optional[dict[str, Any]] = None,
    timeout: Optional[float] = None,
    num_ctx: Optional[int] = None,
) -> dict[str, Any]:
    """One-shot Ollama chat that returns parsed JSON. Best-effort, never raises.

    Forces format="json" so the model emits valid JSON; still guards the parse
    because models occasionally wrap or truncate — falls back to the first
    brace-balanced span. Returns `on_error` (default {}) on any failure.
    ``timeout`` and ``num_ctx`` are as in :func:`json_chat_result`; leaving
    them out keeps the call exactly as it always was.
    """
    result = json_chat_result(
        system, user, model=model, temperature=temperature, timeout=timeout, num_ctx=num_ctx,
    )
    if result.failure is None:
        return result.data
    return dict(on_error) if on_error else {}


def normalize(
    raw: Mapping[str, Any],
    *,
    valid_intents: Iterable[str],
    default_intent: str = "refuse",
    int_fields: Iterable[str] = (),
    confidence_bounds: tuple[float, float] = (0.0, 1.0),
) -> dict[str, Any]:
    """Defensive normalization of a classifier's raw dict.

    Generic, feature-agnostic: whitelist `intent` (unknown → default_intent),
    clamp `confidence` into bounds, coerce named `int_fields` to int-or-None.
    Feature-specific fields (platform, topics, …) pass through untouched for the
    caller to validate. Returns a shallow copy — never mutates `raw`.
    """
    valid = frozenset(valid_intents)
    out: dict[str, Any] = dict(raw) if isinstance(raw, Mapping) else {}

    intent = str(out.get("intent") or default_intent).strip().lower()
    out["intent"] = intent if intent in valid else default_intent

    lo, hi = confidence_bounds
    try:
        c = float(out.get("confidence")) if out.get("confidence") is not None else 0.0
    except (TypeError, ValueError):
        c = 0.0
    out["confidence"] = max(lo, min(hi, c))

    for f in int_fields:
        v = out.get(f)
        if v is None:
            continue
        try:
            out[f] = int(v)
        except (TypeError, ValueError):
            out[f] = None

    return out


def dispatch(
    classification: Mapping[str, Any],
    handlers: Mapping[str, Callable[[Mapping[str, Any]], Any]],
    *,
    default_handler: Callable[[Mapping[str, Any]], Any],
    intent_key: str = "intent",
) -> Any:
    """Route a normalized classification to its handler.

    handlers maps intent → callable(classification). Anything not in the table
    (including a refused/unknown intent) goes to default_handler — so a missing
    branch degrades to the safe default rather than raising.
    """
    intent = classification.get(intent_key)
    return handlers.get(intent, default_handler)(classification)


# ---------------------------------------------------------------------------
# decide(): one declared closed question, answered by the chat model that is
# already loaded, or by today's code whenever the model may not or cannot.

REASONS = (
    "ok",                # the model answered and its answer is used
    "off",               # the decision is off: the fallback, nothing asked
    "shadow",            # shadow mode: the fallback is used
    "unknown",           # no such decision is declared
    "guard_caller",      # called from a guard (decision_data.GUARD_MODULES)
    "batch_only",        # the layer's BACKEND_POLICY does not let this run kind ask
    "author_unknown",    # this run kind must name its authors (exclude_models) and did not
    "bad_state",         # state fields missing or undeclared, or a value that is not text, a number or a bool
    "too_long",          # a state field is longer than its declared max_chars
    "gpu_busy",          # a GPU job holds the job gate or the exclusive GPU lock
    "not_resident",      # the chat model is not loaded on the GPU
    "author_is_judge",   # the loaded model wrote, or shares a build or family with, what it would judge
    "below_floor",       # no measured row for the loaded build qualifies; on acts as shadow
    "timeout",           # the model did not answer within timeout_s
    "parse_fail",        # the reply held no usable answer
    "error",             # the call failed, or something failed inside decide()
    "third_party_unlock",  # the answer would unlock, and some of the state is third-party text
    "shadow_skipped",    # a shadow question not asked: chat stayed busy for SHADOW_WAIT_S
)

# With the decision on, these mean no model answer could be used. A layer
# whose BACKEND_POLICY says when_unanswered "safe_side" (L4, the checks) then
# returns the entry's safe side, never the fallback: a check that did not
# answer is not verified. shadow, below_floor (on acting as shadow) and
# third_party_unlock (the fallback stands) are not in it.
NO_ANSWER_REASONS = frozenset({
    "batch_only", "author_unknown", "bad_state", "too_long", "gpu_busy", "not_resident",
    "author_is_judge", "timeout", "parse_fail", "error",
})

DEFAULT_RUN = "interactive"


@dataclass(frozen=True)
class DecisionResult:
    """What decide() answered and why.

    ``answer`` is what the caller acts on. ``source`` says where it came
    from: ``"llm"`` (the model), ``"fallback"`` (the caller's own answer,
    today's code) or ``"safe_side"`` (the entry's declared safe side: a check
    that is on and got no usable answer). ``reason`` is one of REASONS.
    ``model`` and ``digest`` name the build that was asked, when one was.
    """

    decision: str
    answer: Any
    source: str
    reason: str
    model: Optional[str] = None
    confidence: Optional[float] = None
    latency_ms: int = 0
    digest: Optional[str] = None


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_THIS_FILE = os.path.abspath(__file__)
# decide() keeps its answers for a request in the request's ctx dict under this key.
_MEMO_KEY = "_decide_memo"

# Called with (event, state) for every decision that is not off. None means
# decision_telemetry.record_decision, which writes a question row only while
# GUAARDVARK_DECISION_TELEMETRY is on. The event never carries the state's text,
# only its hash and size; the writer decides whether to keep the text.
_recorder: Optional[Callable[[dict, Mapping[str, Any]], None]] = None


def set_decision_recorder(recorder: Optional[Callable[[dict, Mapping[str, Any]], None]]) -> None:
    """Install the function that records each decision made in shadow or on;
    None restores the decision telemetry writer."""
    global _recorder
    _recorder = recorder


def _record(event: dict, state: Mapping[str, Any]) -> None:
    recorder = _recorder
    if recorder is None:
        from backend.services.decision_telemetry import record_decision as recorder
    try:
        recorder(event, state)
    except Exception as e:  # noqa: BLE001 - a recorder failure never changes an answer
        logger.warning("decide: recording %s failed: %s", event.get("decision"), e)


def guard_for(filename: str, function: str) -> Optional[str]:
    """The GUARD_MODULES entry code in ``filename``/``function`` belongs to, or None."""
    from backend.services.decision_data import GUARD_MODULES

    try:
        rel = os.path.relpath(os.path.abspath(filename), _REPO_ROOT)
    except ValueError:  # another drive on Windows: not in this repository
        return None
    if rel.startswith(".."):
        return None
    rel = rel.replace(os.sep, "/")
    for guard, functions in GUARD_MODULES.items():
        hit = rel.startswith(guard) if guard.endswith("/") else rel == guard
        if hit and (functions is None or function in functions):
            return f"{rel}:{function}" if functions else rel
    return None


def _guard_caller() -> Optional[str]:
    """The guard that called decide(), skipping frames in this module, or None."""
    frame = sys._getframe(1)
    while frame is not None and os.path.abspath(frame.f_code.co_filename) == _THIS_FILE:
        frame = frame.f_back
    if frame is None:
        return None
    return guard_for(frame.f_code.co_filename, frame.f_code.co_name)


def _ms(started: float) -> int:
    return int(round((time.perf_counter() - started) * 1000))


def _state_digest(state: Any) -> tuple[str, int]:
    """(sha256 prefix, characters) of the state as sent, for records without its text."""
    try:
        text = json.dumps(state, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        text = repr(state)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], len(text)


def check_state(entry: Mapping[str, Any], state: Any) -> Optional[str]:
    """None when ``state`` has the entry's declared shape, else "bad_state" or "too_long".

    Exactly the declared fields; each value text, a number or a bool, within
    its declared max_chars (a bool or number by its JSON spelling, so "false"
    is 5). An oversized field is refused, never cut: a cut field is a prompt
    the measured rows say nothing about, and the part cut may be the part
    that mattered.
    """
    declared = entry["state"]
    if not isinstance(state, Mapping) or set(state) != set(declared):
        return "bad_state"
    for field, (_source, max_chars) in declared.items():
        value = state[field]
        if isinstance(value, str):
            size = len(value)
        elif isinstance(value, (bool, int, float)):
            size = len(json.dumps(value))
        else:
            return "bad_state"
        if size > max_chars:
            return "too_long"
    return None


def gpu_busy() -> Optional[str]:
    """Why the GPU is not free for a question, or None.

    GPU work registered with this process's job gate, or anything holding the
    cross-process exclusive GPU lock (renders, training, image generation).
    """
    from backend.services.job_operation_gate import get_gate

    snapshot = get_gate().snapshot()
    if snapshot.get("gpu_busy"):
        holder = snapshot.get("gpu_holder") or {}
        return f"{holder.get('kind', 'a GPU job')} is running"
    from backend.services.gpu_resource_coordinator import get_gpu_coordinator

    if get_gpu_coordinator().lock_held():
        return "the exclusive GPU lock is held"
    return None


def _chat_model(ctx: Optional[Mapping[str, Any]]) -> Optional[str]:
    """The chat model this turn uses: ctx["model"] when the caller knows it, else the saved choice."""
    if ctx and ctx.get("model"):
        return str(ctx["model"])
    from backend.config import _read_saved_model_name, get_default_llm

    return _read_saved_model_name() or get_default_llm()


def _resident(model: str) -> Optional[dict]:
    """The model's /api/ps entry when it is loaded on the GPU and staying loaded."""
    from backend.utils.ollama_resource_manager import resident_entry

    return resident_entry(model)


def _chat(system: str, user: str, *, model: str, timeout: float, num_ctx: Optional[int]) -> JsonChatResult:
    """The model call: the measured settings (temperature 0), bounded by ``timeout``."""
    return json_chat_result(system, user, model=model, temperature=0.0, timeout=timeout, num_ctx=num_ctx)


@dataclass(frozen=True)
class Asked:
    """One question put to a model: ``reason`` "ok" with its answer, or why there is none."""

    reason: str
    answer: Any = None
    confidence: Optional[float] = None
    raw: str = ""


def ask_question(entry: Mapping[str, Any], state: Mapping[str, Any], *, model: str, timeout_s: float,
                 num_ctx: Optional[int]) -> Asked:
    """Ask ``model`` the entry's question about ``state`` and read the reply.

    The measured prompt (decision_prompt), temperature 0, ``num_ctx`` and
    ``timeout_s``; the reply read with decision_prompt.parse_answer. No gate:
    decide() checks first, and scripts/eval_decisions.py measures through
    this same function so its rows describe what decide() would get.
    """
    from backend.services.decision_prompt import NO_ANSWER, parse_answer, system_prompt, user_prompt

    reply = _chat(system_prompt(entry), user_prompt(entry, state), model=model, timeout=timeout_s, num_ctx=num_ctx)
    if reply.failure:
        return Asked(reply.failure, raw=reply.raw)
    answer, confidence = parse_answer(entry, reply.data)
    if answer is NO_ANSWER:
        return Asked("parse_fail", confidence=confidence, raw=reply.raw)
    return Asked("ok", answer, confidence, reply.raw)


def _memo_for(ctx: Any) -> Optional[dict]:
    if not isinstance(ctx, dict):
        return None
    memo = ctx.setdefault(_MEMO_KEY, {})
    return memo if isinstance(memo, dict) else None


def decide(
    decision: str,
    state: Mapping[str, Any],
    *,
    fallback: Any,
    timeout_s: float = 2.0,
    ctx: Optional[dict] = None,
    run: str = DEFAULT_RUN,
    exclude_models: Optional[Iterable[str]] = None,
) -> DecisionResult:
    """Answer the declared closed question ``decision`` about ``state``.

    ``fallback`` is the answer today's code gives; the caller computes it
    first and acts on ``result.answer``. decide() never raises and never
    loads a model: whenever the model may not or cannot answer, the result
    is the fallback with the reason (REASONS).

    Modes (decision_settings): off, the default for every decision, returns
    the fallback and does nothing else (no model, no network, no record).
    shadow returns the fallback. on asks the model only when all of these
    hold, in this order, and otherwise returns the fallback:

    1. the layer's BACKEND_POLICY lets ``run`` ("interactive", "unattended"
       or "batch") ask, and a run that must name its authors passed
       ``exclude_models`` (an empty tuple when no model wrote what is judged);
    2. ``state`` has exactly the declared fields, each within its max_chars;
       an oversized field is refused ("too_long"), never truncated;
    3. no GPU job is running and the exclusive GPU lock is free;
    4. the turn's chat model is loaded on the GPU and staying loaded;
    5. it is not one of ``exclude_models`` and shares no build or family
       with them (decision_data.INDEPENDENCE);
    6. a MEASURED_ACCURACY row for that build's digest, with the current
       wording and labelled set, reaches the decision's floor at its lower
       bound (decision_evidence.qualifies); without one, on acts as shadow
       ("below_floor").

    The model then gets the measured prompt at temperature 0, with the
    context length it is loaded with and ``timeout_s`` as the limit.

    What the answer may do is limited in data, whatever the model says. An
    answer in the entry's ``unlocks`` is refused ("third_party_unlock", the
    fallback stands) when any state field comes from a third party, so
    retrieved pages, posts and documents can only lead to a veto. Text the
    user typed may unlock, which is why callers filter candidates through the
    user's own policy (web access, MCP policy, tool confirmation) before
    asking. When an L4 check is on and no model answer can be used
    (NO_ANSWER_REASONS), the result is its safe side, "not verified", never
    the fallback. decide() only answers; it never runs a tool or writes.

    ``ctx`` is a dict for the request: ``request_id`` and ``model`` (the
    turn's chat model) when the caller knows them. Pass the same dict for the
    whole turn: decide() keeps its answers in it, so a question asked twice
    with the same state in one turn is asked once.
    """
    started = time.perf_counter()
    entry, mode = None, None
    try:
        from backend.services.decision_data import DECISIONS
        from backend.services.decision_settings import get_decision_mode

        entry = DECISIONS.get(decision)
        if entry is None:
            logger.warning("decide: no decision named %r is declared", decision)
            return DecisionResult(decision, fallback, "fallback", "unknown", latency_ms=_ms(started))
        guard = _guard_caller()
        if guard:
            logger.error("decide(%s) was called from the guard %s; a guard never asks a model", decision, guard)
            return DecisionResult(decision, fallback, "fallback", "guard_caller", latency_ms=_ms(started))
        mode = get_decision_mode(decision)
        if mode == "off":
            return DecisionResult(decision, fallback, "fallback", "off", latency_ms=_ms(started))

        if isinstance(exclude_models, str):
            exclude_models = (exclude_models,)
        authors = None if exclude_models is None else tuple(sorted({str(m) for m in exclude_models if m}))
        sha, chars = _state_digest(state)
        memo = _memo_for(ctx)
        key = (decision, sha, run, authors)
        if memo is not None and key in memo:
            return memo[key]

        result, asked = _decide_live(entry, decision, state, fallback, mode, timeout_s, ctx, run, authors, started)
        if memo is not None:
            memo[key] = result
        if result.reason == "shadow":
            # Asked later, off the person's wait; recorded when it is asked.
            _queue_shadow(entry, decision, state, fallback, timeout_s, ctx, run, authors, sha, chars)
            return result
        _record({
            "ts": time.time(), "decision": decision, "layer": entry["layer"], "mode": mode, "run": run,
            "backend": "chat", "model": result.model, "digest": result.digest,
            "state_sha256": sha, "state_chars": chars,
            "llm_answer": asked.answer if asked and asked.reason == "ok" else None,
            "llm_confidence": asked.confidence if asked else None,
            "fallback_answer": fallback, "used_answer": result.answer, "source": result.source,
            "reason": result.reason, "latency_ms": result.latency_ms,
            "request_id": (ctx or {}).get("request_id") if isinstance(ctx, Mapping) else None,
        }, state)
        return result
    except Exception as e:  # noqa: BLE001 - decide() never raises
        logger.error("decide(%s) failed: %s", decision, e, exc_info=True)
        if mode == "on" and entry is not None and _fails_closed(entry):
            return DecisionResult(decision, entry["safe_side"], "safe_side", "error", latency_ms=_ms(started))
        return DecisionResult(decision, fallback, "fallback", "error", latency_ms=_ms(started))


def _live_model(ctx: Optional[Mapping[str, Any]], authors: Optional[tuple]):
    """(problem, model, digest, num_ctx) for asking now: problem is None when
    the GPU is free, the turn's chat model is loaded on it and staying loaded,
    and that model did not write what is judged; else the reason it may not."""
    from backend.services.decision_evidence import check_counts_as_independent
    from backend.utils.ollama_resource_manager import _short_digest

    try:
        busy = gpu_busy()
    except Exception as e:  # noqa: BLE001 - an unreadable GPU state counts as busy
        busy = f"the GPU state could not be read: {e}"
    if busy:
        return "gpu_busy", None, None, None
    model = _chat_model(ctx)
    resident = _resident(model) if model else None
    if not resident:
        return "not_resident", model, None, None
    digest = _short_digest(resident.get("digest")) or None
    if authors:
        if model in authors or check_counts_as_independent("model", judge=model, authors=authors) is not True:
            return "author_is_judge", model, digest, None
    num_ctx = resident.get("context_length")
    return None, model, digest, (int(num_ctx) if isinstance(num_ctx, int) and num_ctx > 0 else None)


# Shadow questions are asked after the person has the reply: a caller that
# keeps a list under ctx[SHADOW_KEY] (the chat engine does, for its turn)
# collects them there and hands them over with run_shadow() when the turn
# ends; any other shadow question goes to the worker at once. One worker asks
# them one at a time, at the measured settings, with the same checks as on
# (GPU free, the turn's model loaded, not the author) except the floor:
# collecting evidence below the floor is what shadow is for.
SHADOW_KEY = "_decide_shadow"
_shadow_pool = None
_shadow_pool_lock = threading.Lock()
# Nobody waits on a shadow answer, so it gets longer than a person would, and
# it waits for the model to be free: with OLLAMA_NUM_PARALLEL=1 a question sent
# while a chat reply streams queues behind it, and one sent between a turn's
# model calls delays the next. ``busy_check`` is set by the chat
# (chat_decisions): True while a chat turn is running.
SHADOW_TIMEOUT_S = 30.0
SHADOW_WAIT_S = 120.0
busy_check: Optional[Callable[[], bool]] = None


def _wait_until_idle() -> bool:
    """False when chat stayed busy for SHADOW_WAIT_S."""
    check = busy_check
    deadline = time.monotonic() + SHADOW_WAIT_S
    while check is not None and check():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.5)
    return True


def _submit_shadow(job: Callable[[], None]) -> None:
    """Start one shadow question on the shadow worker (tests replace this)."""
    global _shadow_pool
    with _shadow_pool_lock:
        if _shadow_pool is None:
            from concurrent.futures import ThreadPoolExecutor
            _shadow_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="decide-shadow")
        pool = _shadow_pool
    pool.submit(job)


def _queue_shadow(entry, decision, state, fallback, timeout_s, ctx, run, authors, sha, chars) -> None:
    turn = {"model": ctx.get("model")} if isinstance(ctx, Mapping) and ctx.get("model") else None
    request_id = ctx.get("request_id") if isinstance(ctx, Mapping) else None
    state = dict(state)

    def job():
        started = time.perf_counter()
        asked, model, digest = None, None, None
        try:
            reason = None if _wait_until_idle() else "shadow_skipped"
            if not reason:
                reason, model, digest, num_ctx = _live_model(turn, authors)
            if not reason:
                asked = ask_question(entry, state, model=model,
                                     timeout_s=max(float(timeout_s), SHADOW_TIMEOUT_S), num_ctx=num_ctx)
                reason = "shadow" if asked.reason == "ok" else asked.reason
        except Exception as e:  # noqa: BLE001 - a shadow question never reaches the turn
            logger.warning("decide(%s) shadow question failed: %s", decision, e)
            reason = "error"
        _record({
            "ts": time.time(), "decision": decision, "layer": entry["layer"], "mode": "shadow", "run": run,
            "backend": "chat", "model": model, "digest": digest,
            "state_sha256": sha, "state_chars": chars,
            "llm_answer": asked.answer if asked and asked.reason == "ok" else None,
            "llm_confidence": asked.confidence if asked else None,
            "fallback_answer": fallback, "used_answer": fallback, "source": "fallback",
            "reason": reason, "latency_ms": _ms(started), "request_id": request_id,
        }, state)

    pending = ctx.get(SHADOW_KEY) if isinstance(ctx, dict) else None
    if isinstance(pending, list):
        pending.append(job)
    else:
        _submit_shadow(job)


def run_shadow(ctx: Any) -> int:
    """Hand the shadow questions collected in ``ctx`` to the shadow worker;
    returns how many. Call once the reply is out."""
    jobs = ctx.pop(SHADOW_KEY, None) if isinstance(ctx, dict) else None
    if not jobs:
        return 0
    for job in jobs:
        _submit_shadow(job)
    return len(jobs)


def _fails_closed(entry: Mapping[str, Any]) -> bool:
    from backend.services.decision_data import BACKEND_POLICY

    return BACKEND_POLICY[entry["layer"]]["when_unanswered"] == "safe_side"


def _decide_live(entry, decision, state, fallback, mode, timeout_s, ctx, run, authors, started):
    """(result, what the model said) for a decision in shadow or on; see decide()."""
    from backend.services.decision_data import BACKEND_POLICY, RUN_KINDS
    from backend.services.decision_evidence import qualifies

    policy = BACKEND_POLICY[entry["layer"]]
    asked: Optional[Asked] = None

    def refuse(reason, model=None, digest=None):
        answer, source = fallback, "fallback"
        if mode == "on" and reason in NO_ANSWER_REASONS and policy["when_unanswered"] == "safe_side":
            answer, source = entry["safe_side"], "safe_side"
        return DecisionResult(decision, answer, source, reason, model,
                              asked.confidence if asked else None, _ms(started), digest), asked

    if run not in RUN_KINDS:
        logger.error("decide(%s): unknown run kind %r", decision, run)
        return refuse("error")
    if run not in policy["runs"] or "chat" not in policy["backends"]:
        return refuse("batch_only")
    if run in policy["author_must_differ"] and authors is None:
        return refuse("author_unknown")
    problem = check_state(entry, state)
    if problem:
        return refuse(problem)
    if mode != "on":
        return refuse("shadow")

    problem, model, digest, num_ctx = _live_model(ctx, authors)
    if problem:
        return refuse(problem, model, digest)
    if not qualifies(decision, digest):
        return refuse("below_floor", model, digest)

    asked = ask_question(entry, state, model=model, timeout_s=max(0.1, float(timeout_s)), num_ctx=num_ctx)
    if asked.reason != "ok":
        return refuse(asked.reason, model, digest)
    if asked.answer in entry["unlocks"] and any(src == "third_party" for src, _ in entry["state"].values()):
        logger.info("decide(%s): answer %r would unlock on third-party text; the fallback stands",
                    decision, asked.answer)
        return refuse("third_party_unlock", model, digest)
    return DecisionResult(decision, asked.answer, "llm", "ok", model, asked.confidence, _ms(started), digest), asked
