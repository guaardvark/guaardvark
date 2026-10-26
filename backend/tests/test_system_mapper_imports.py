"""How the System Mapper resolves imports, and what it builds from them.

Small trees on disk (tmp_path is outside any git checkout, so the mapper walks
it). No backend, database, GPU or network.
"""
from pathlib import Path

from backend.services.system_mapper import codebase_map, dependency_graph
from backend.services.system_mapper.core import FindingKind
from backend.services.system_mapper.corpus import Corpus, is_test_path


def _write(root: Path, rel: str, text: str = "") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _edges(root: Path) -> set[tuple[str, str, str]]:
    return {(e.src, e.dst, e.kind) for e in Corpus.build(root).edges()}


# ---- Python ------------------------------------------------------------------

def test_relative_imports_resolve(tmp_path):
    _write(tmp_path, "backend/__init__.py")
    _write(tmp_path, "backend/no_netrc.py", "X = 1\n")
    _write(tmp_path, "backend/services/__init__.py")
    _write(tmp_path, "backend/services/a.py", "from . import b\nfrom .. import no_netrc\nfrom .b import thing\n")
    _write(tmp_path, "backend/services/b.py", "thing = 1\n")
    edges = _edges(tmp_path)
    assert ("backend/services/a.py", "backend/services/b.py", "top") in edges
    assert ("backend/services/a.py", "backend/no_netrc.py", "top") in edges


def test_importing_a_submodule_does_not_point_at_the_package(tmp_path):
    _write(tmp_path, "backend/__init__.py")
    _write(tmp_path, "backend/services/__init__.py", "")
    _write(tmp_path, "backend/services/x.py", "")
    _write(tmp_path, "backend/app.py", "from backend.services import x\nfrom backend.services import helper\n")
    edges = _edges(tmp_path)
    assert ("backend/app.py", "backend/services/x.py", "top") in edges
    # A name that is not a submodule comes from the package itself.
    assert ("backend/app.py", "backend/services/__init__.py", "top") in edges


def test_imports_inside_functions_are_lazy_and_type_checking_is_its_own_kind(tmp_path):
    _write(tmp_path, "backend/a.py",
           "from typing import TYPE_CHECKING\n"
           "if TYPE_CHECKING:\n    from backend import c\n"
           "def f():\n    from backend import b\n    return b\n")
    _write(tmp_path, "backend/b.py", "from backend import a\n")
    _write(tmp_path, "backend/c.py", "")
    edges = _edges(tmp_path)
    assert ("backend/a.py", "backend/b.py", "lazy") in edges
    assert ("backend/b.py", "backend/a.py", "top") in edges
    assert ("backend/a.py", "backend/c.py", "type") in edges


def test_cycles_count_load_time_imports_only(tmp_path):
    _write(tmp_path, "backend/a.py", "def f():\n    from backend import b\n")
    _write(tmp_path, "backend/b.py", "from backend import a\n")
    _write(tmp_path, "backend/c.py", "from backend import d\n")
    _write(tmp_path, "backend/d.py", "from backend import c\n")
    result = dependency_graph.analyze(tmp_path)
    cycles = [f.evidence["cycle"] for f in result["findings"] if f.kind == FindingKind.IMPORT_CYCLE]
    assert cycles == [["backend.c", "backend.d"]]
    assert result["lazy_graph"] == {"backend.a": ["backend.b"]}


def test_a_hub_on_many_short_cycles_is_reported(tmp_path):
    # backend.hub imports five modules that each import it back.
    spokes = [f"s{i}" for i in range(5)]
    _write(tmp_path, "backend/hub.py", "".join(f"from backend import {s}\n" for s in spokes))
    for s in spokes:
        _write(tmp_path, f"backend/{s}.py", "from backend import hub\n")
    result = dependency_graph.analyze(tmp_path)
    hubs = [f for f in result["findings"] if f.kind == FindingKind.OVER_COUPLED]
    assert [f.paths for f in hubs] == [["backend/hub.py"]]
    assert result["stats"]["over_coupled_hubs"] == ["backend.hub"]


def test_plugin_folders_are_their_own_import_root(tmp_path):
    _write(tmp_path, "plugins/swarm/service/__init__.py")
    _write(tmp_path, "plugins/swarm/service/models.py", "class SwarmStatus: pass\n")
    _write(tmp_path, "plugins/swarm/service/orchestrator.py", "from service.models import SwarmStatus\n")
    _write(tmp_path, "plugins/discord/core/api_client.py", "")
    _write(tmp_path, "plugins/discord/bot.py", "from core.api_client import x\n")
    edges = _edges(tmp_path)
    assert ("plugins/swarm/service/orchestrator.py", "plugins/swarm/service/models.py", "top") in edges
    assert ("plugins/discord/bot.py", "plugins/discord/core/api_client.py", "top") in edges


def test_a_folder_with_its_own_packaging_is_an_import_root(tmp_path):
    _write(tmp_path, "cli/pyproject.toml", "[project]\nname='llx'\n")
    _write(tmp_path, "cli/llx/__init__.py")
    _write(tmp_path, "cli/llx/client.py", "")
    _write(tmp_path, "cli/llx/commands/chat.py", "from llx.client import Client\n")
    assert ("cli/llx/commands/chat.py", "cli/llx/client.py", "top") in _edges(tmp_path)


def test_import_module_calls_and_loader_lists_are_imports(tmp_path):
    _write(tmp_path, "backend/__init__.py")
    _write(tmp_path, "backend/providers/__init__.py")
    _write(tmp_path, "backend/providers/linkedin.py", "")
    _write(tmp_path, "backend/providers/mastodon.py", "")
    _write(tmp_path, "backend/registry.py",
           "import importlib\n"
           "def load(name):\n    return importlib.import_module(f'backend.providers.{name}')\n")
    _write(tmp_path, "plugins/discord/commands/chat.py", "")
    _write(tmp_path, "plugins/discord/bot.py", "EXTENSIONS = ['commands.chat']\n")
    edges = _edges(tmp_path)
    assert ("backend/registry.py", "backend/providers/linkedin.py", "lazy") in edges
    assert ("backend/registry.py", "backend/providers/mastodon.py", "lazy") in edges
    assert ("plugins/discord/bot.py", "plugins/discord/commands/chat.py", "lazy") in edges


def test_modules_a_script_starts_are_not_dormant(tmp_path):
    _write(tmp_path, "plugins/upscaling/service/__init__.py")
    _write(tmp_path, "plugins/upscaling/service/app.py", "app = object()\n")
    _write(tmp_path, "plugins/upscaling/scripts/start.sh",
           "python -m uvicorn service.app:app --port 8202\n")
    result = dependency_graph.analyze(tmp_path)
    dormant = {f.paths[0] for f in result["findings"] if f.kind == FindingKind.DORMANT_MODULE}
    assert "plugins/upscaling/service/app.py" not in dormant
    meta = result["node_meta"]["plugins.upscaling.service.app"]
    assert meta["lifecycle"] == "auto-loaded"
    assert meta["started_by"] == ["plugins/upscaling/scripts/start.sh"]


# ---- Tests are not importers -----------------------------------------------------

def test_a_module_only_tests_import_is_dormant(tmp_path):
    _write(tmp_path, "backend/__init__.py")
    _write(tmp_path, "backend/services/lonely.py", "def f():\n    return 1\n")
    _write(tmp_path, "backend/tests/test_lonely.py", "from backend.services.lonely import f\n")
    result = dependency_graph.analyze(tmp_path)
    dormant = [f for f in result["findings"] if f.kind == FindingKind.DORMANT_MODULE]
    assert [f.paths for f in dormant] == [["backend/services/lonely.py"]]
    assert dormant[0].evidence["test_importers"] == ["backend.tests.test_lonely"]
    meta = result["node_meta"]["backend.services.lonely"]
    assert meta["lifecycle"] == "dormant"
    assert meta["importers"] == 0 and meta["test_importers"] == 1


def test_test_paths_in_both_languages():
    assert is_test_path("backend/tests/test_x.py")
    assert is_test_path("frontend/src/api/__tests__/apiAuth.test.js")
    assert is_test_path("frontend/src/hooks/useX.test.js")
    assert is_test_path("backend/x_test.py")
    assert not is_test_path("backend/services/testing_helpers.py")
    assert not is_test_path("frontend/src/pages/ChatPage.jsx")


# ---- JavaScript ------------------------------------------------------------------

def test_js_imports_multiline_lazy_alias_and_index(tmp_path):
    _write(tmp_path, "frontend/src/App.jsx",
           "import {\n  a,\n  b,\n} from './api/service';\n"
           "import Thing from '@/components/Thing';\n"
           "const Page = lazy(() => import('./pages/Page'));\n"
           "import { x } from './utils';\n")
    _write(tmp_path, "frontend/src/api/service.js", "export const a = 1;\nexport const b = 2;\n")
    _write(tmp_path, "frontend/src/components/Thing.jsx", "export default function Thing() { return null; }\n")
    _write(tmp_path, "frontend/src/pages/Page.jsx", "export default function Page() { return null; }\n")
    _write(tmp_path, "frontend/src/utils/index.js", "export const x = 1;\n")
    edges = _edges(tmp_path)
    assert ("frontend/src/App.jsx", "frontend/src/api/service.js", "top") in edges
    assert ("frontend/src/App.jsx", "frontend/src/components/Thing.jsx", "top") in edges
    assert ("frontend/src/App.jsx", "frontend/src/pages/Page.jsx", "lazy") in edges
    assert ("frontend/src/App.jsx", "frontend/src/utils/index.js", "top") in edges


def test_js_modules_are_nodes_with_paths_as_ids(tmp_path):
    _write(tmp_path, "frontend/src/main.jsx", "import App from './App';\n")
    _write(tmp_path, "frontend/src/App.jsx", "export default function App() { return null; }\n")
    _write(tmp_path, "frontend/src/Unused.jsx", "export default function Unused() { return null; }\n")
    result = dependency_graph.analyze(tmp_path)
    assert result["node_meta"]["frontend/src/App.jsx"]["lifecycle"] == "active"
    assert result["node_meta"]["frontend/src/main.jsx"]["lifecycle"] == "auto-loaded"
    dormant = {f.paths[0] for f in result["findings"] if f.kind == FindingKind.DORMANT_MODULE}
    assert dormant == {"frontend/src/Unused.jsx"}


def test_every_module_gets_node_meta_even_with_no_imports(tmp_path):
    _write(tmp_path, "backend/alone.py", "x = 1\n")
    smap = codebase_map(tmp_path)
    assert "backend.alone" in smap.node_meta
    assert smap.node_meta["backend.alone"]["path"] == "backend/alone.py"
    assert "backend.alone" not in smap.dependency_graph


def test_repository_analysis_uses_the_same_resolver(tmp_path):
    from backend.services.repository_analysis_service import RepositoryAnalysisService as R
    from backend.utils import import_resolution

    lookup = {"backend.services.b": "backend/services/b.py", "backend.services": "backend/services/__init__.py"}
    for level, module in ((1, "b"), (0, "backend.services.b.thing")):
        assert R._resolve_python_import("backend/services/a.py", level, module, lookup) == \
            import_resolution.resolve_python_import("backend/services/a.py", level, module, lookup) == \
            "backend/services/b.py"
