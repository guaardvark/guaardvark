"""The query index: symbols, imports, calls and words of one map.

Built next to the SystemMap from the same corpus and stored beside its
snapshot (store.py), so the query layer (query.py) answers "where is X",
"what calls F" and "what depends on M" without reading the tree again.

Kept out of the snapshot the System Map page loads: on Guaardvark itself it
holds some 28,000 definitions and 200,000 call sites.

Call sites are recorded as written (``self.save``, ``os.path.join``) and
resolved when a question is asked (query.resolve_call), through the file's
imports. That resolution is best effort and labelled as such: a call on an
object whose type is not known statically resolves by name only.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

INDEX_VERSION = 3


def _locale_strings(root: Path) -> dict[str, str]:
    """{"imagesBatch.seed.label": "Seed", ...} from frontend/src/locales/en/*.json."""
    out: dict[str, str] = {}
    folder = root / "frontend" / "src" / "locales" / "en"
    if not folder.is_dir():
        return out

    def walk(prefix: str, node) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(f"{prefix}.{k}", v)
        elif isinstance(node, str):
            out[prefix] = node

    for path in sorted(folder.glob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            walk(path.stem, json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def build(corpus, smap) -> dict[str, Any]:
    """The index for one map (see the module docstring)."""
    from .corpus import is_test_path, node_id

    node_meta = smap.node_meta or {}
    files: dict[str, dict] = {}
    defs: dict[str, list] = {}
    calls: dict[str, list] = {}
    bindings: dict[str, dict] = {}
    tokens: dict[str, str] = {}
    i18n_uses: dict[str, list] = {}
    for rel in corpus.files:
        f = corpus.facts[rel]
        mid = node_id(rel)
        files[rel] = {
            "id": mid,
            "lang": f["lang"],
            "lines": f.get("lines", 0),
            "doc": f.get("doc", ""),
            "subsystem": (node_meta.get(mid) or {}).get("subsystem"),
            "test": is_test_path(rel),
        }
        if f.get("default_export"):
            files[rel]["default_export"] = f["default_export"]
        if f.get("defs"):
            defs[rel] = [[d["qualname"], d["kind"], d["line"], d["end"], d.get("doc", "")] for d in f["defs"]]
        if f.get("calls"):
            calls[rel] = f["calls"]
        b = corpus.bindings(rel)
        if b:
            bindings[rel] = {k: list(v) for k, v in b.items()}
        if f.get("tokens"):
            tokens[rel] = " ".join(sorted(f["tokens"]))
        for key, line in f.get("i18n", []):
            i18n_uses.setdefault(key, []).append([rel, line])

    imports: dict[str, list] = {}
    for e in corpus.edges():
        imports.setdefault(e.src, []).append([e.dst, e.kind, e.line])

    return {
        "version": INDEX_VERSION,
        "root": str(corpus.root),
        "files": files,
        "defs": defs,
        "imports": imports,
        "bindings": bindings,
        "calls": calls,
        "tokens": tokens,
        "i18n": {"en": _locale_strings(corpus.root), "uses": i18n_uses},
        "all_files": corpus.all_files,
    }
