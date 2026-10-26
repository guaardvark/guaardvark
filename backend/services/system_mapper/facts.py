"""What each source file says about itself, read once per change.

Every analyzer of the mapper used to parse every file on its own; the
analyzers now read the facts here instead. A Python file is parsed once with
``ast`` and walked once; a JavaScript file is scanned once with regular
expressions. The result is kept in memory per file, keyed by the file's
modification time and size, so a rebuild after a one-file edit re-reads one
file.

Python facts (all line numbers 1-based):
    doc          first line of the module docstring
    lines        number of lines
    imports      [{level, module, names, line, scope}]; names is [[name, asname]]
                 for ``from`` imports and None for ``import x``; asname for the
                 latter is in "alias"; scope is "top" (runs at import time),
                 "lazy" (inside a function) or "type" (under TYPE_CHECKING).
                 importlib.import_module / __import__ calls with a resolvable
                 name are imports too ("dynamic"); an f-string ending in a dot
                 ("pkg.providers.{name}") names every module under it ("prefix")
    defs         [{name, qualname, kind, line, end, decorated, doc}] for
                 module-level functions and classes and the methods of those
                 classes; closures and classes inside functions are left out
    calls        [[line, caller, callee]]: caller is the qualname of the
                 enclosing definition ("" at module level), callee the dotted
                 text of what is called ("os.path.join", "self.save",
                 "?.items" when the receiver is an expression)
    refs         identifiers the file loads, attribute names it reads,
                 decorator names and getattr() names
    strings      string constants that look like identifiers
    dotted       {"commands.chat": line} for string constants shaped like a
                 dotted module name (a module some loader imports by name)
    exports      names listed in __all__
    routes       Flask routes [{path, methods, function, line, bp_var}]
    blueprint    whether the file constructs a Blueprint
    tasks        Celery tasks [{function, name, line}] (name None when implicit)
    tool_classes classes with a string ``name`` attribute
                 [{class, name, line, end, bases, attrs, description}]

JavaScript facts:
    imports      [{spec, line, kind, names}]: kind is "static", "reexport",
                 "require", "lazy" (import()) or "glob" (import.meta.glob);
                 names maps local binding -> exported name ("default", "*")
    defs         [{name, qualname, kind, line, end}] from code_symbol_extractor,
                 with the end line found by matching braces (best effort)
    consts       {NAME: value} for module-level string or template constants
    local_consts [[name, value, line]] for the same declared inside a block
    api_calls    [{raw, line, via}] HTTP calls (fetch, axios, apiClient), and
                 any call whose first argument is a URL starting at /api or
                 with a substituted constant (a wrapper such as post(url))
    calls        [[line, caller, callee]] for calls of imported or local names
                 and JSX elements of imported components
    i18n         [[key, line]] translation keys the file uses
    exports      exported names
    default_export  the name exported as default, or ""

Both carry ``tokens``: the lower-case words (three letters or more) of the
file's identifiers, strings and comments, for the query layer's ``where``.
"""
from __future__ import annotations

import ast
import bisect
import os
import re
import threading
from pathlib import Path
from typing import Any

# Bumped when the shape of the facts changes, so a stored cache is not reused.
FACTS_VERSION = 9

PY_SUFFIXES = (".py",)
JS_SUFFIXES = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")

_IDENT_STRING = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,79}$")
_DOTTED_STRING = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$")
_WORD = re.compile(r"[A-Za-z][a-z]+|[A-Z]+(?![a-z])|[a-z]+")
_TOKEN_SOURCE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_MAX_TOKENS = 4000


def tokens_of(text: str) -> set[str]:
    """Lower-case words in ``text``: identifiers split at underscores and case
    changes ("parseImageSeed" gives parse, image, seed), three letters or more."""
    out: set[str] = set()
    for ident in _TOKEN_SOURCE.findall(text):
        for word in _WORD.findall(ident):
            if len(word) >= 3:
                out.add(word.lower())
        if len(out) >= _MAX_TOKENS:
            break
    return out


def _doc_line(node) -> str:
    try:
        doc = ast.get_docstring(node, clean=True)
    except Exception:
        return ""
    if not doc:
        return ""
    return doc.strip().splitlines()[0][:160]


def _dotted(node) -> str | None:
    """'a.b.c' for a Name/Attribute chain; '?.c' when the base is an expression."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    else:
        parts.append("?")
    return ".".join(reversed(parts))


_ROUTE_DECORATORS = {"route", "get", "post", "put", "delete", "patch"}
_TOOL_ATTRS = ("read_only", "plan_research", "requires_approval", "is_dangerous",
               "observation_chars", "destructive", "idempotent")


def _is_task_decorator(dec: ast.expr) -> bool:
    """@shared_task, @celery_app.task, @app.task, @task, @x.periodic_task."""
    target = dec.func if isinstance(dec, ast.Call) else dec
    if isinstance(target, ast.Name):
        return target.id in ("shared_task", "task")
    if isinstance(target, ast.Attribute):
        return target.attr in ("task", "shared_task", "periodic_task")
    return False


def _task_name_kw(dec: ast.expr) -> str | None:
    if isinstance(dec, ast.Call):
        for kw in dec.keywords:
            if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                return kw.value.value
    return None


def _is_type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def _str_value(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


class _PyCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.imports: list[dict] = []
        self.defs: list[dict] = []
        self.calls: list[list] = []
        self.refs: set[str] = set()
        self.strings: set[str] = set()
        self.dotted: dict[str, int] = {}     # "commands.chat" -> first line
        self.exports: set[str] = set()
        self.raw_routes: list[dict] = []
        self.bp_prefixes: dict[str, str] = {}
        self.blueprint = False
        self.tasks: list[dict] = []
        self.tool_classes: list[dict] = []
        # (kind, qualname) of the enclosing definitions; kind is "function",
        # "class" or "hidden" (a closure or a class inside a function).
        self._stack: list[tuple[str, str]] = []
        self._type_checking = 0
        self._str_consts: dict[str, str] = {}   # module-level NAME = "literal"

    # -- scope helpers -------------------------------------------------------
    def _in_function(self) -> bool:
        return any(kind in ("function", "hidden") for kind, _ in self._stack)

    def _caller(self) -> str:
        for kind, qual in reversed(self._stack):
            if kind != "hidden":
                return qual
        return ""

    def _qual(self, name: str) -> str:
        if not self._stack:
            return name
        return f"{self._stack[-1][1]}.{name}"

    # -- definitions ---------------------------------------------------------
    def _visit_function(self, node) -> None:
        for dec in node.decorator_list:
            self._decorator(dec, node)
        hidden = self._in_function()
        in_class = bool(self._stack) and self._stack[-1][0] == "class"
        qual = self._qual(node.name)
        if not hidden:
            self.defs.append({
                "name": node.name,
                "qualname": qual,
                "kind": "method" if in_class else "function",
                "line": node.lineno,
                "end": getattr(node, "end_lineno", node.lineno),
                "decorated": bool(node.decorator_list),
                "doc": _doc_line(node),
            })
        # Decorators and default values run where the def statement runs.
        for child in node.decorator_list:
            self.visit(child)
        for child in node.args.defaults + node.args.kw_defaults:
            if child is not None:
                self.visit(child)
        a = node.args
        for arg in a.posonlyargs + a.args + a.kwonlyargs + [x for x in (a.vararg, a.kwarg) if x]:
            if arg.annotation is not None:
                self.visit(arg.annotation)
        if node.returns is not None:
            self.visit(node.returns)
        self._stack.append(("hidden" if hidden else "function", qual))
        for stmt in node.body:
            self.visit(stmt)
        self._stack.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for dec in node.decorator_list:
            self.refs.update(_names_in(dec))
        hidden = self._in_function()
        qual = self._qual(node.name)
        if not hidden:
            self.defs.append({
                "name": node.name,
                "qualname": qual,
                "kind": "class",
                "line": node.lineno,
                "end": getattr(node, "end_lineno", node.lineno),
                "decorated": bool(node.decorator_list),
                "doc": _doc_line(node),
            })
            self._tool_class(node, qual)
        for child in node.decorator_list + node.bases + [kw.value for kw in node.keywords]:
            self.visit(child)
        self._stack.append(("hidden" if hidden else "class", qual))
        for stmt in node.body:
            self.visit(stmt)
        self._stack.pop()

    def _tool_class(self, node: ast.ClassDef, qual: str) -> None:
        name = None
        attrs: dict[str, Any] = {}
        description = ""
        for stmt in node.body:
            targets: list = []
            value = None
            if isinstance(stmt, ast.Assign):
                targets, value = stmt.targets, stmt.value
            elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                targets, value = [stmt.target], stmt.value
            for tgt in targets:
                if not isinstance(tgt, ast.Name):
                    continue
                if tgt.id == "name" and _str_value(value) is not None:
                    name = _str_value(value)
                elif tgt.id in _TOOL_ATTRS and isinstance(value, ast.Constant):
                    attrs[tgt.id] = value.value
                elif tgt.id in ("chat_summary", "description"):
                    text = _joined_str(value)
                    if text and (tgt.id == "chat_summary" or not description):
                        description = text[:300]
        if name is None:
            return
        self.tool_classes.append({
            "class": qual,
            "name": name,
            "line": node.lineno,
            "end": getattr(node, "end_lineno", node.lineno),
            "bases": [_dotted(b) or "?" for b in node.bases],
            "attrs": attrs,
            "description": description,
        })

    def _decorator(self, dec: ast.expr, fn) -> None:
        self.refs.update(_names_in(dec))
        if _is_task_decorator(dec):
            self.tasks.append({"function": fn.name, "name": _task_name_kw(dec), "line": fn.lineno})
            return
        if not isinstance(dec, ast.Call):
            return
        func = dec.func
        if not (isinstance(func, ast.Attribute) and func.attr in _ROUTE_DECORATORS
                and isinstance(func.value, ast.Name)):
            return
        path_arg = _str_value(dec.args[0]) if dec.args else None
        methods: list[str] = []
        if func.attr != "route":
            methods = [func.attr.upper()]
        else:
            for kw in dec.keywords:
                if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                    methods.extend(e.value for e in kw.value.elts if isinstance(e, ast.Constant))
        self.raw_routes.append({
            "path": path_arg or "",
            "methods": sorted(set(methods)) or ["GET"],
            "function": fn.name,
            "line": fn.lineno,
            "bp_var": func.value.id,
        })

    # -- imports -------------------------------------------------------------
    def _scope(self) -> str:
        if self._type_checking:
            return "type"
        return "lazy" if self._in_function() else "top"

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append({"level": 0, "module": alias.name, "names": None,
                                 "alias": alias.asname, "line": node.lineno, "scope": self._scope()})

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.imports.append({
            "level": node.level or 0,
            "module": node.module or "",
            "names": [[a.name, a.asname] for a in node.names],
            "alias": None,
            "line": node.lineno,
            "scope": self._scope(),
        })

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking(node.test):
            self._type_checking += 1
            for stmt in node.body:
                self.visit(stmt)
            self._type_checking -= 1
            for stmt in node.orelse:
                self.visit(stmt)
            return
        self.generic_visit(node)

    # -- references, calls, assignments --------------------------------------
    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.refs.add(node.id)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self.refs.add(node.attr)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            if _IDENT_STRING.match(node.value):
                self.strings.add(node.value)
            elif len(node.value) <= 120 and _DOTTED_STRING.match(node.value):
                self.dotted.setdefault(node.value, getattr(node, "lineno", 0))

    def visit_Call(self, node: ast.Call) -> None:
        callee = _dotted(node.func)
        if callee and callee != "?":
            self.calls.append([node.lineno, self._caller(), callee])
        func = node.func
        is_getattr = ((isinstance(func, ast.Name) and func.id == "getattr")
                      or (isinstance(func, ast.Attribute) and func.attr == "getattr"))
        if is_getattr and len(node.args) >= 2:
            name = _str_value(node.args[1])
            if name:
                self.refs.add(name)
        fname = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        if fname == "Blueprint":
            self.blueprint = True
        if fname in ("import_module", "__import__") and node.args:
            self._dynamic_import(node.args[0], node.lineno)
        # Factory registration: celery_app.task(name="x")(fn)
        if isinstance(func, ast.Call) and _is_task_decorator(func):
            target = node.args[0] if node.args else None
            fn_name = target.id if isinstance(target, ast.Name) else None
            if _task_name_kw(func) or fn_name:
                self.tasks.append({"function": fn_name, "name": _task_name_kw(func), "line": node.lineno})
        self.generic_visit(node)

    def _dynamic_import(self, arg, line: int) -> None:
        """importlib.import_module("a.b"), (f"a.b.{name}") or (CONST): an
        import that runs when the call does. An f-string whose literal part
        ends in a dot names a whole package's modules (``prefix``)."""
        module, prefix = None, False
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            module = arg.value
        elif isinstance(arg, ast.Name) and arg.id in self._str_consts:
            module = self._str_consts[arg.id]
        elif isinstance(arg, ast.JoinedStr):
            lead = []
            for part in arg.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    lead.append(part.value)
                else:
                    break
            text = "".join(lead)
            if text.endswith(".") and len(text) > 1:
                module, prefix = text, True
        if not module or not re.fullmatch(r"[A-Za-z_][\w.]*", module):
            return
        self.imports.append({"level": 0, "module": module, "names": None, "alias": None, "line": line,
                             "scope": "lazy" if self._in_function() else "top",
                             "dynamic": True, "prefix": prefix})

    def visit_Assign(self, node: ast.Assign) -> None:
        if not self._stack and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    self._str_consts[tgt.id] = node.value.value
        value = node.value
        if isinstance(value, ast.Call):
            func = value.func
            fname = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if fname == "Blueprint":
                prefix = ""
                for kw in value.keywords:
                    if kw.arg == "url_prefix":
                        prefix = _str_value(kw.value) or ""
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        self.bp_prefixes[tgt.id] = prefix
        for tgt in node.targets:
            if isinstance(tgt, ast.Name) and tgt.id == "__all__":
                self.exports |= _string_seq(value)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if isinstance(node.target, ast.Name) and node.target.id == "__all__":
            self.exports |= _string_seq(node.value)
        self.generic_visit(node)


def _names_in(expr: ast.expr) -> set[str]:
    out: set[str] = set()
    for sub in ast.walk(expr):
        if isinstance(sub, ast.Name):
            out.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            out.add(sub.attr)
    return out


def _string_seq(value) -> set[str]:
    out: set[str] = set()
    if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
        for elt in value.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                out.add(elt.value)
    return out


def _joined_str(node) -> str:
    """A string constant, or the literal parts of implicit concatenation."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return " ".join(node.value.split())
    if isinstance(node, ast.JoinedStr):
        return " ".join("".join(v.value for v in node.values
                                if isinstance(v, ast.Constant) and isinstance(v.value, str)).split())
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return (_joined_str(node.left) + " " + _joined_str(node.right)).strip()
    return ""


def _route_path(prefix: str, path_arg: str) -> str:
    full = (prefix.rstrip("/") + "/" + path_arg.lstrip("/")) if prefix else path_arg
    full = "/" + full.lstrip("/")
    return re.sub(r"/+", "/", full)


def python_facts_from_text(text: str) -> dict:
    """Facts for Python source ``text`` (see the module docstring)."""
    lines = text.count("\n") + 1
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError) as exc:
        return {"lang": "py", "error": f"{type(exc).__name__}: {exc}", "lines": lines,
                "doc": "", "imports": [], "defs": [], "calls": [], "refs": set(),
                "strings": set(), "dotted": {}, "exports": set(), "routes": [], "blueprint": False,
                "tasks": [], "tool_classes": [], "tokens": tokens_of(text)}
    collector = _PyCollector()
    collector.visit(tree)
    routes = []
    for r in collector.raw_routes:
        prefix = collector.bp_prefixes.get(r["bp_var"], "")
        routes.append({**r, "path": _route_path(prefix, r["path"])})
    return {
        "lang": "py",
        "error": None,
        "lines": lines,
        "doc": _doc_line(tree),
        "imports": collector.imports,
        "defs": collector.defs,
        "calls": collector.calls,
        "refs": collector.refs,
        "strings": collector.strings,
        "dotted": collector.dotted,
        "exports": collector.exports,
        "routes": routes,
        "blueprint": collector.blueprint,
        "tasks": collector.tasks,
        "tool_classes": collector.tool_classes,
        "tokens": tokens_of(text),
    }


# ── JavaScript ──────────────────────────────────────────────────────────────

_JS_IMPORT = re.compile(
    r"""(?:^|[;\n])\s*import\s+(?P<clause>[^'"`;]*?)\s+from\s+['"](?P<spec>[^'"\n]+)['"]""", re.S)
_JS_SIDE_IMPORT = re.compile(r"""(?:^|[;\n])\s*import\s+['"](?P<spec>[^'"\n]+)['"]""")
_JS_REEXPORT = re.compile(
    r"""(?:^|[;\n])\s*export\s+(?P<clause>\*(?:\s+as\s+\w+)?|\{[^}]*\})\s+from\s+['"](?P<spec>[^'"\n]+)['"]""", re.S)
_JS_DYNAMIC = re.compile(r"""\bimport\(\s*['"](?P<spec>[^'"\n]+)['"]\s*\)""")
_JS_REQUIRE = re.compile(r"""\brequire\(\s*['"](?P<spec>[^'"\n]+)['"]\s*\)""")
_JS_GLOB = re.compile(r"""import\.meta\.glob(?:Eager)?\(\s*(?P<arg>\[[^\]]*\]|['"][^'"\n]+['"])""")
_JS_EXPORT_NAME = re.compile(
    r"""\bexport\s+(?:default\s+)?(?:async\s+)?(?:function\*?|class|const|let|var)\s+([A-Za-z_$][\w$]*)""")
_JS_DEFAULT_EXPORT = re.compile(
    r"""\bexport\s+default\s+(?:async\s+)?(?:function\*?\s+|class\s+)?([A-Za-z_$][\w$]*)""")
_JS_CONST = re.compile(r"""^(?P<indent>[ \t]*)(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*""", re.M)
_JS_HTTP_CALL = re.compile(
    r"""\b(?P<via>fetch|axios\.(?:get|post|put|delete|patch|head)|axios|apiClient\.(?:get|post|put|delete|patch))\s*\(\s*""")
# Any other call whose first argument is a URL that begins at /api or with a
# substituted constant: wrappers such as post(`${BASE_URL}/agent-control/kill`)
# or request("/api/x"). Reachability keeps only the ones that resolve to /api.
_JS_URL_ARG_CALL = re.compile(r"""(?<![\w$.])(?P<via>[A-Za-z_$][\w$.]*)\s*\(\s*(?=`\$\{|['"`]/api/|['"`]/socket\.io/)""")
_JS_I18N = re.compile(r"""(?:\bt|\btranslate|\bi18n\.t)\(\s*['"`]([A-Za-z0-9_.-]+)['"`]|i18nKey=\s*['"{]\s*['"]?([A-Za-z0-9_.-]+)['"]""")
_JS_CALL = re.compile(r"""(?<![\w$.])([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\(""")
_JSX_ELEMENT = re.compile(r"""<([A-Z][\w$]*(?:\.[A-Z][\w$]*)*)[\s/>]""")
_JS_KEYWORDS = frozenset({
    "if", "for", "while", "switch", "catch", "function", "return", "typeof", "new", "await",
    "import", "super", "this", "do", "else", "try", "with", "yield", "void", "delete", "in", "of",
})


class _LineIndex:
    def __init__(self, text: str) -> None:
        self._starts = [0] + [m.end() for m in re.finditer("\n", text)]

    def line(self, offset: int) -> int:
        return bisect.bisect_right(self._starts, offset)


def _scan_js_string(text: str, i: int) -> tuple[str, int] | None:
    """The string literal starting at text[i] ('...', "..." or `...`), as a
    template with each ${...} kept verbatim, and the offset after it."""
    quote = text[i]
    if quote not in "'\"`":
        return None
    out = []
    j = i + 1
    n = len(text)
    while j < n:
        c = text[j]
        if c == "\\":
            out.append(text[j:j + 2])
            j += 2
            continue
        if c == quote:
            return "".join(out), j + 1
        if quote != "`" and c == "\n":
            return None
        if quote == "`" and c == "$" and j + 1 < n and text[j + 1] == "{":
            depth = 0
            k = j + 1
            while k < n:
                ch = text[k]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        break
                elif ch in "'\"`":
                    inner = _scan_js_string(text, k)
                    if inner is None:
                        return None
                    k = inner[1]
                    continue
                k += 1
            out.append(text[j:k + 1])
            j = k + 1
            continue
        out.append(c)
        j += 1
    return None


_URLISH_NAME = re.compile(r"(?:URL|Url|BASE|Base|API|Api|PATH|Path|PREFIX|Prefix|ROOT|Root)")


def _js_url_expr(text: str, i: int, want_end: bool = False):
    """A URL-ish expression starting at text[i] as a template: a string or
    template literal, an identifier (``${NAME}``), ``(env || "lit")``, or a
    chain of those joined with ``+``. None for anything else."""
    parts: list[str] = []
    n = len(text)
    while i < n:
        while i < n and text[i] in " \t\r\n":
            i += 1
        if i >= n:
            break
        c = text[i]
        if c in "'\"`":
            scanned = _scan_js_string(text, i)
            if scanned is None:
                return None
            parts.append(scanned[0])
            i = scanned[1]
        elif c == "(":
            # (import.meta.env.VITE_X || "/api") — the fallback literal.
            m = re.match(r"""\(\s*import\.meta\.env\.\w+\s*\|\|\s*(['"`])([^'"`\n]*)\1\s*\)""", text[i:])
            if not m:
                return None
            parts.append(m.group(2))
            i += m.end()
        elif text.startswith("import.meta.env.", i):
            m = re.match(r"""import\.meta\.env\.\w+\s*\|\|\s*(['"`])([^'"`\n]*)\1""", text[i:])
            if not m:
                return None
            parts.append(m.group(2))
            i += m.end()
        else:
            m = re.match(r"[A-Za-z_$][\w$]*", text[i:])
            if not m:
                return None
            parts.append("${" + m.group(0) + "}")
            i += m.end()
        # Skip a trailing .replace(...)/.trim() on a base constant.
        m = re.match(r"\s*\.(?:replace|trim)\([^)]*\)(?:\))?", text[i:])
        if m:
            i += m.end()
        m = re.match(r"\s*\+\s*", text[i:])
        if not m:
            break
        i += m.end()
    if not parts:
        return None
    return ("".join(parts), i) if want_end else "".join(parts)


def _import_names(clause: str) -> dict[str, str]:
    """{local: exported} for an import clause: X, { a as b }, * as ns."""
    names: dict[str, str] = {}
    clause = clause.strip()
    if clause.startswith("type "):
        clause = clause[5:]
    brace = re.search(r"\{([^}]*)\}", clause, re.S)
    if brace:
        for item in brace.group(1).split(","):
            item = item.strip()
            if not item:
                continue
            item = re.sub(r"^type\s+", "", item)
            bits = re.split(r"\s+as\s+", item)
            exported, local = bits[0].strip(), bits[-1].strip()
            if re.match(r"^[A-Za-z_$][\w$]*$", local):
                names[local] = exported
        clause = clause[:brace.start()] + clause[brace.end():]
    ns = re.search(r"\*\s+as\s+([A-Za-z_$][\w$]*)", clause)
    if ns:
        names[ns.group(1)] = "*"
        clause = clause[:ns.start()] + clause[ns.end():]
    default = re.match(r"\s*([A-Za-z_$][\w$]*)", clause)
    if default:
        names[default.group(1)] = "default"
    return names


def _skip_js_string(text: str, i: int) -> int:
    scanned = _scan_js_string(text, i)
    return scanned[1] if scanned else i + 1


def _matching(text: str, i: int, open_ch: str, close_ch: str) -> int | None:
    """Offset of the bracket closing the one at text[i], skipping strings and
    comments; None when it never closes."""
    n = len(text)
    depth = 0
    while i < n:
        c = text[i]
        if c in "'\"`":
            i = _skip_js_string(text, i)
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            nl = text.find("\n", i)
            i = n if nl < 0 else nl
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            close = text.find("*/", i + 2)
            i = n if close < 0 else close + 2
            continue
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _js_def_end(text: str, start: int, kind: str) -> int | None:
    """Offset where the definition starting on the line at ``start`` ends:
    the class body, or the function body after its parameter list (a block,
    a parenthesised expression, or the rest of the statement). Best effort."""
    n = len(text)
    line_end = text.find("\n", start)
    line_end = n if line_end < 0 else line_end
    if kind == "class":
        brace = text.find("{", start)
        return _matching(text, brace, "{", "}") if brace >= 0 else None
    paren = text.find("(", start, line_end)
    arrow = text.find("=>", start, line_end)
    if paren >= 0 and (arrow < 0 or paren < arrow):
        close = _matching(text, paren, "(", ")")
        if close is None:
            return None
        i = close + 1
    elif arrow >= 0:
        i = arrow
    else:
        return None
    while i < n and text[i] in " \t\r\n":
        i += 1
    if text.startswith("=>", i):
        i += 2
        while i < n and text[i] in " \t\r\n":
            i += 1
    elif i < n and text[i] == ":":
        # A TypeScript return type: the body is the next block.
        brace = text.find("{", i)
        return _matching(text, brace, "{", "}") if brace >= 0 else None
    if i >= n:
        return None
    if text[i] == "{":
        return _matching(text, i, "{", "}")
    if text[i] == "(":
        return _matching(text, i, "(", ")")
    if text[i] == "[":
        return _matching(text, i, "[", "]")
    semi = text.find(";", i)
    nl = text.find("\n", i)
    ends = [e for e in (semi, nl) if e >= 0]
    return min(ends) if ends else n


def js_facts_from_text(text: str, rel_path: str = "") -> dict:
    """Facts for JavaScript/JSX/TypeScript source ``text``."""
    from backend.utils.code_symbol_extractor import extract_symbols

    index = _LineIndex(text)
    imports: list[dict] = []
    for m in _JS_IMPORT.finditer(text):
        imports.append({"spec": m.group("spec"), "line": index.line(m.start("spec")),
                        "kind": "static", "names": _import_names(m.group("clause"))})
    for m in _JS_SIDE_IMPORT.finditer(text):
        imports.append({"spec": m.group("spec"), "line": index.line(m.start("spec")),
                        "kind": "static", "names": {}})
    for m in _JS_REEXPORT.finditer(text):
        imports.append({"spec": m.group("spec"), "line": index.line(m.start("spec")),
                        "kind": "reexport", "names": {}})
    for m in _JS_DYNAMIC.finditer(text):
        imports.append({"spec": m.group("spec"), "line": index.line(m.start("spec")),
                        "kind": "lazy", "names": {}})
    for m in _JS_REQUIRE.finditer(text):
        imports.append({"spec": m.group("spec"), "line": index.line(m.start("spec")),
                        "kind": "require", "names": {}})
    for m in _JS_GLOB.finditer(text):
        for pat in re.findall(r"""['"]([^'"\n]+)['"]""", m.group("arg")):
            imports.append({"spec": pat, "line": index.line(m.start()), "kind": "glob", "names": {}})

    defs: list[dict] = []
    seen: set[tuple[str, int]] = set()
    language = "typescript" if rel_path.endswith((".ts", ".tsx")) else "javascript"
    for sym in extract_symbols(text, language):
        if sym.get("type") not in ("function", "class"):
            continue
        key = (sym["name"], sym["line"])
        if key in seen:
            continue
        seen.add(key)
        line_start = index._starts[sym["line"] - 1] if sym["line"] - 1 < len(index._starts) else 0
        end_off = _js_def_end(text, line_start, sym["type"])
        end = index.line(end_off) if end_off is not None else sym["line"]
        defs.append({"name": sym["name"], "qualname": sym["name"], "kind": sym["type"],
                     "line": sym["line"], "end": max(end, sym["line"]), "decorated": False, "doc": ""})
    defs.sort(key=lambda d: d["line"])

    consts: dict[str, str] = {}
    local_consts: list[list] = []
    for m in _JS_CONST.finditer(text):
        parsed = _js_url_expr(text, m.end(), want_end=True)
        if parsed is None:
            continue
        value, end = parsed
        rest = text[end:end + 40].lstrip(" \t")
        if rest and rest[0] not in ";\n,)":
            continue  # a call, an arrow function or an object: not a URL constant
        literal = re.sub(r"\$\{[^}]*\}", "", value)
        names = re.findall(r"\$\{([^}]*)\}", value)
        if not literal and not all(_URLISH_NAME.search(n) for n in names):
            continue
        if len(value) > 300:
            continue
        if m.group("indent"):
            # Only a block-level const: a let is reassigned further down.
            if text.startswith("const", m.start() + len(m.group("indent"))) and literal:
                local_consts.append([m.group(2), value, index.line(m.start(2))])
        else:
            consts.setdefault(m.group(2), value)

    api_calls: list[dict] = []
    for m in _JS_HTTP_CALL.finditer(text):
        start = m.end()
        raw = None
        if m.group("via") == "axios" and text.startswith("{", start):
            um = re.match(r"\{\s*url\s*:\s*", text[start:])
            if um:
                raw = _js_url_expr(text, start + um.end())
        else:
            raw = _js_url_expr(text, start)
        if raw:
            api_calls.append({"raw": raw, "line": index.line(m.start()), "via": m.group("via")})

    seen_calls = {c["line"] for c in api_calls}
    for m in _JS_URL_ARG_CALL.finditer(text):
        via = m.group("via")
        if via in _JS_KEYWORDS or via.split(".")[-1] in ("log", "warn", "error", "info", "debug", "t", "translate"):
            continue
        line = index.line(m.start())
        if line in seen_calls:
            continue
        raw = _js_url_expr(text, m.end())
        if raw:
            api_calls.append({"raw": raw, "line": line, "via": via})

    i18n: list[list] = []
    for m in _JS_I18N.finditer(text):
        key = m.group(1) or m.group(2)
        if key and "." in key:
            i18n.append([key, index.line(m.start())])

    bound: set[str] = {local for imp in imports for local in imp["names"]}
    local_defs = {d["name"] for d in defs}
    known = bound | local_defs
    calls: list[list] = []

    def _caller_at(line: int) -> str:
        best = ""
        for d in defs:
            if d["line"] <= line <= d["end"]:
                best = d["qualname"]
            elif d["line"] > line:
                break
        return best

    for m in _JS_CALL.finditer(text):
        name = m.group(1)
        head = name.split(".", 1)[0]
        if head in _JS_KEYWORDS or head not in known:
            continue
        line = index.line(m.start())
        if any(d["line"] == line and d["name"] == head for d in defs):
            continue  # the definition's own signature
        calls.append([line, _caller_at(line), name])
    for m in _JSX_ELEMENT.finditer(text):
        name = m.group(1)
        if name.split(".", 1)[0] in known:
            line = index.line(m.start())
            calls.append([line, _caller_at(line), name])

    return {
        "lang": "js",
        "error": None,
        "lines": text.count("\n") + 1,
        "doc": "",
        "imports": imports,
        "defs": defs,
        "consts": consts,
        "local_consts": local_consts,
        "api_calls": api_calls,
        "calls": calls,
        "i18n": i18n,
        "exports": set(_JS_EXPORT_NAME.findall(text)),
        "default_export": _default_export(text),
        "tokens": tokens_of(text),
    }


def _default_export(text: str) -> str:
    """The name a module exports as default ("ChatPage" for ``export default
    ChatPage`` or ``export default function ChatPage``), or ""."""
    m = _JS_DEFAULT_EXPORT.search(text)
    if not m or m.group(1) in ("function", "class", "async", "new"):
        return ""
    return m.group(1)


# ── Cache ───────────────────────────────────────────────────────────────────

_cache: dict[str, tuple[int, int, dict]] = {}
_cache_lock = threading.Lock()
_stats = {"hits": 0, "misses": 0}


def file_facts(path: Path, rel_path: str = "") -> dict | None:
    """Facts for one file, from the cache when the file has not changed since
    it was last read. None for a file of another language or one that cannot
    be read."""
    suffix = path.suffix.lower()
    if suffix not in PY_SUFFIXES and suffix not in JS_SUFFIXES:
        return None
    key = str(path)
    try:
        st = os.stat(key)
    except OSError:
        return None
    with _cache_lock:
        cached = _cache.get(key)
        if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
            _stats["hits"] += 1
            return cached[2]
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    if suffix in PY_SUFFIXES:
        facts = python_facts_from_text(text)
    else:
        facts = js_facts_from_text(text, rel_path or path.name)
    with _cache_lock:
        _cache[key] = (st.st_mtime_ns, st.st_size, facts)
        _stats["misses"] += 1
    return facts


def cache_stats() -> dict:
    with _cache_lock:
        return {"files": len(_cache), **_stats}


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
        _stats["hits"] = _stats["misses"] = 0
