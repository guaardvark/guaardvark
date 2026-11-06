"""How much a measured result proves, and which checks count as independent.

A decision may run on a model only when the model has earned it on the
decision's labelled set. The strict rule: the lower confidence bound of its
accuracy, not the plain score, must reach the decision's floor. A perfect
short run proves less than it looks: 35 of 35 right is 1.0 observed and 0.920
at the bound, which does not clear a 0.95 floor.

The constants live in ``decision_data.EVIDENCE`` and ``INDEPENDENCE``. ``lcb``
is also the bound the Cast identity methods are judged by
(``identity_method_data``).
"""
from __future__ import annotations

import functools
import math
from typing import Iterable, Optional

from backend.services.decision_data import EVIDENCE, INDEPENDENCE

# needed_to_qualify gives up past this many extra items: a floor that far away
# is not reachable by growing the set.
_MAX_EXTRA_ITEMS = 10_000_000


def lcb(correct: int, n: int, q: Optional[float] = None) -> float:
    """Lower bound on accuracy from ``correct`` of ``n``: the ``q`` quantile of
    the Beta(1 + correct, 1 + n - correct) posterior (a uniform prior).

    ``q`` defaults to EVIDENCE["lcb_q"]. Returns 0.0 for an impossible count
    or when scipy is unavailable, which withholds rather than grants.
    """
    q = EVIDENCE["lcb_q"] if q is None else q
    if n <= 0 or correct < 0 or correct > n or not 0.0 < q < 1.0:
        return 0.0
    try:
        from scipy.stats import beta
    except Exception:  # noqa: BLE001 - a missing scipy must not qualify anything
        return 0.0
    return float(beta.ppf(q, 1 + correct, 1 + n - correct))


def _bound_below(floor: float, correct: int, n: int, q: float) -> bool:
    """True while lcb(correct, n, q) is still below ``floor``.

    The posterior is Beta(correct + 1, misses + 1), whose CDF at ``floor`` is
    the chance of at most ``misses`` failures in n + 1 trials that each pass
    with probability ``floor``: a finite sum, so no quantile search is needed.
    The bound is below the floor exactly when that CDF is above ``q``.
    """
    misses, trials = n - correct, n + 1
    log_f, log_miss = math.log(floor), math.log1p(-floor)
    cdf = 0.0
    for j in range(misses + 1):
        log_comb = math.lgamma(trials + 1) - math.lgamma(j + 1) - math.lgamma(trials - j + 1)
        cdf += math.exp(log_comb + (trials - j) * log_f + j * log_miss)
    return cdf > q


def needed_to_qualify(correct: int, n: int, floor: float, q: Optional[float] = None) -> Optional[int]:
    """How many more labelled items, all answered right, before the bound
    reaches ``floor``; 0 when it already does, None when no number of items
    would (a floor of 1 or more, or an impossible count).

    With no misses the answer is closed form: the bound of c of c is
    q ** (1 / (c + 1)), so c + 1 must reach ln q / ln floor. With misses the
    count is the first k for which the finite sum in _bound_below drops to q.
    """
    q = EVIDENCE["lcb_q"] if q is None else q
    if n < 0 or correct < 0 or correct > n or not 0.0 < q < 1.0:
        return None
    if floor <= 0.0:
        return 0
    if floor >= 1.0:
        return None
    if n - correct == 0:
        k = max(0, math.ceil(math.log(q) / math.log(floor)) - (n + 1))
        # Step off a boundary that floating point rounded the wrong way.
        while k > 0 and not _bound_below(floor, correct + k - 1, n + k - 1, q):
            k -= 1
        while _bound_below(floor, correct + k, n + k, q):
            k += 1
        return k
    if not _bound_below(floor, correct, n, q):
        return 0
    low, high = 0, 1
    while _bound_below(floor, correct + high, n + high, q):
        low, high = high, high * 2
        if high > _MAX_EXTRA_ITEMS:
            return None
    while high - low > 1:
        mid = (low + high) // 2
        if _bound_below(floor, correct + mid, n + mid, q):
            low = mid
        else:
            high = mid
    return high


def _model_differs(judge: Optional[str], authors: Iterable[Optional[str]], tags: Optional[list]) -> Optional[bool]:
    """True when ``judge`` differs from every author in everything
    INDEPENDENCE["model_must_differ_in"] names, False when it shares one with
    any author, None when that cannot be told."""
    from backend.utils.ollama_resource_manager import _ollama_tags, model_families, same_model

    authors = [a for a in authors if a]
    if not judge:
        return None
    if not authors:
        return True
    if tags is None:
        tags = _ollama_tags()
    must_differ = INDEPENDENCE["model_must_differ_in"]
    verdict: Optional[bool] = True
    for author in authors:
        if "digest" in must_differ:
            same = same_model(judge, author, tags)
            if same:
                return False
            if same is None:
                verdict = None
        if "family" in must_differ:
            judge_families, author_families = model_families(judge, tags), model_families(author, tags)
            if judge_families & author_families:
                return False
            if not judge_families or not author_families:
                verdict = None
    return verdict


def check_counts_as_independent(
    kind: str,
    *,
    judge: Optional[str] = None,
    authors: Iterable[Optional[str]] = (),
    tags: Optional[list] = None,
) -> Optional[bool]:
    """Whether a check of ``kind`` counts as the independent check of what
    ``authors`` produced (decision_data.INDEPENDENCE).

    ``kind`` is one of INDEPENDENCE["check_kinds"]; an unknown kind never
    counts. For a model check, ``judge`` is the model that checked and
    ``authors`` the models that wrote what it checked: True when it differs
    from all of them in digest and family, False when it shares either with
    one, None when that cannot be told. A caller that needs a yes or no reads
    None as INDEPENDENCE["unknown_counts"]. ``tags`` is an /api/tags list the
    caller already has.
    """
    rule = INDEPENDENCE["check_kinds"].get(kind)
    if rule == "counts":
        return True
    if rule != "counts_if_unrelated":
        return False
    return _model_differs(judge, authors, tags)


@functools.lru_cache(maxsize=64)
def labelled_set_version(set_name: Optional[str]) -> Optional[int]:
    """``set_version`` of a labelled set under backend/tests/fixtures/decisions,
    or None when the decision has no set or it cannot be read."""
    if not set_name:
        return None
    try:
        from backend.tests.fixtures.decision_sets import load_set

        version = load_set(set_name).get("set_version")
    except Exception:  # noqa: BLE001 - an unreadable set qualifies nothing
        return None
    return version if isinstance(version, int) and not isinstance(version, bool) else None


def qualifying_row(decision: str, digest: Optional[str], *, backend: str = "chat") -> Optional[dict]:
    """The MEASURED_ACCURACY row that lets ``decision`` run on the build
    ``digest`` (an Ollama manifest digest, full or prefix), or None.

    A row counts when it was measured on ``backend`` and on that build, with
    the decision's current wording (wording_sha) and labelled-set version, and
    its lower bound reaches the decision's floor, or, for a decision in
    QUALIFY_BY_RULE, beats today's rule on that set by the rule margin. A gate must also have
    missed none of the set's injection items: a gate row without
    ``injection_misses`` 0 does not count. Rows for other builds never
    transfer, whatever their names: Ollama 0.40 lists one name under two
    builds and one build under several names.
    """
    from backend.services.decision_data import DECISIONS, MEASURED_ACCURACY
    from backend.services.decision_prompt import wording_sha
    from backend.utils.ollama_resource_manager import _short_digest

    entry = DECISIONS.get(decision)
    build = _short_digest(digest)
    if not entry or not build:
        return None
    wording = wording_sha(entry)
    set_version = labelled_set_version(entry.get("set"))
    for row in MEASURED_ACCURACY.get(decision, ()):
        if row.get("backend") != backend or _short_digest(row.get("digest")) != build:
            continue
        if row.get("wording_sha") != wording or set_version is None or row.get("set_version") != set_version:
            continue
        if entry["type"] == "gate" and row.get("injection_misses") != 0:
            continue
        try:
            correct, n = int(row["correct"]), int(row["n"])
        except (KeyError, TypeError, ValueError):
            continue
        if lcb(correct, n) >= entry["floor"] or _beats_rule(decision, set_version, correct, n):
            return row
    return None


def _beats_rule(decision: str, set_version: int, correct: int, n: int) -> bool:
    """True when ``decision`` may qualify by beating today's rule
    (decision_data.QUALIFY_BY_RULE) and the model's lower bound is at least
    EVIDENCE["rule_margin"] above the rule's score on the same set version."""
    from backend.services.decision_data import QUALIFY_BY_RULE, RULE_ACCURACY

    if decision not in QUALIFY_BY_RULE:
        return False
    for rule in RULE_ACCURACY.get(decision, ()):
        if rule.get("set_version") != set_version:
            continue
        try:
            rule_score = int(rule["correct"]) / int(rule["n"])
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
        return lcb(correct, n) >= rule_score + float(EVIDENCE.get("rule_margin", 1.0))
    return False


def qualifies(decision: str, digest: Optional[str], *, backend: str = "chat") -> bool:
    """Whether ``decision`` may run on the build ``digest`` (see qualifying_row)."""
    return qualifying_row(decision, digest, backend=backend) is not None
