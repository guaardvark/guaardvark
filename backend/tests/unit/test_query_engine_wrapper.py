import pytest

try:
    from backend.utils.query_engine_wrapper import (
        RetrieverQueryEngine,
        _build_query_engine_from_retriever,
    )
except Exception:
    pytest.skip("Required modules not available", allow_module_level=True)


def test_duplicate_retriever_kwarg(monkeypatch, caplog):
    calls = {}

    def fake_from_args(ret, **kwargs):
        calls["ret"] = ret
        calls["kwargs"] = kwargs
        return "dummy"

    monkeypatch.setattr(RetrieverQueryEngine, "from_args", fake_from_args)

    r1 = object()
    r2 = object()
    caplog.set_level("WARNING")
    result = _build_query_engine_from_retriever(retriever=r1, llm="x")

    assert result == "dummy"
    assert calls["ret"] is r1
    assert calls["kwargs"]["llm"] == "x"
