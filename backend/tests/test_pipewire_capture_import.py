"""PipeWire capture must import on a machine without the portal stack.

dbus and gi are system packages a virtualenv usually cannot see, so the module
guards their import — but an unquoted `Image.Image` annotation still resolved at
class-definition time and raised NameError, taking preflight down with it.
"""
from __future__ import annotations

import builtins
import importlib

import pytest


@pytest.mark.parametrize("missing", ["dbus", "gi", "PIL"])
def test_module_imports_without_the_portal_stack(monkeypatch, missing):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == missing or name.startswith(f"{missing}."):
            raise ImportError(f"No module named {missing!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    module = importlib.import_module("backend.services.pipewire_capture")
    module = importlib.reload(module)

    assert module.PIPEWIRE_AVAILABLE is False
    # The class body must still be constructible — that is where the annotation lives.
    assert module.PipeWireCapture is not None


def test_available_flag_is_a_bool_when_the_stack_is_present():
    module = importlib.reload(importlib.import_module("backend.services.pipewire_capture"))
    assert isinstance(module.PIPEWIRE_AVAILABLE, bool)
