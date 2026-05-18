from pathlib import Path

import pytest

from scripts.dep_reconciler.base import Reconciler, ReconcileResult


class _FakeReconciler(Reconciler):
    id = "fake"
    name = "Fake"

    def manifests(self) -> list[Path]:
        return [Path("/dev/null")]

    def is_active(self) -> bool:
        return True

    def compute_hash(self) -> str:
        return "sha256:fake"

    def install(self, log_path: Path) -> int:
        return 0


def test_subclass_is_instantiable():
    r = _FakeReconciler()
    assert r.id == "fake"
    assert r.is_active()


def test_extra_state_default_is_empty_dict():
    assert _FakeReconciler().extra_state() == {}


def test_abstract_methods_required():
    """Subclassing without overriding required hooks must error."""
    with pytest.raises(TypeError):
        class Incomplete(Reconciler):
            id = "x"
            name = "X"
        Incomplete()


def test_reconcile_result_dataclass():
    r = ReconcileResult(reconciler_id="fake", status="ok", message=None)
    assert r.status == "ok"
