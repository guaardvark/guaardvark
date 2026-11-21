import json

import pytest
from flask import Flask

from backend.models import Document, Folder, db


@pytest.fixture
def app(tmp_path):
    app = Flask(__name__)
    app.config.update(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            "UPLOAD_FOLDER": str(tmp_path),
        }
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_run_sandbox_command_is_not_registered_by_test_execution_tools(monkeypatch):
    from backend.services import agent_tools
    from backend.tools import tool_registry_init

    monkeypatch.setattr(agent_tools, "_global_tool_registry", agent_tools.ToolRegistry())
    monkeypatch.setattr(tool_registry_init, "_tool_categories", {})

    registered = tool_registry_init.register_test_execution_tools()
    tool_names = agent_tools.get_tool_registry().list_tools()

    assert "run_sandbox_command" not in registered
    assert "run_sandbox_command" not in tool_names


def test_repo_folder_light_dict_includes_metadata_only_for_repositories():
    repo = Folder(
        name="Repo",
        path="Repo",
        is_repository=True,
        description="Architecture summary",
        repo_metadata=json.dumps({"languages": {"py": 2}, "frameworks": ["Python"]}),
    )
    plain = Folder(name="Plain", path="Plain", is_repository=False)

    repo_payload = repo.to_dict_light()
    plain_payload = plain.to_dict_light()

    assert repo_payload["description"] == "Architecture summary"
    assert repo_payload["repo_metadata"]["languages"] == {"py": 2}
    assert "repo_metadata" not in plain_payload
    assert "description" not in plain_payload


def test_read_ast_node_rejects_paths_outside_repo(app):
    with app.app_context():
        repo_root = app.config["UPLOAD_FOLDER"] + "/Repo"
        folder = Folder(name="Repo", path="Repo", is_repository=True)
        db.session.add(folder)
        db.session.commit()

        from pathlib import Path

        Path(repo_root).mkdir(parents=True)
        outside = Path(app.config["UPLOAD_FOLDER"]) / "outside.py"
        outside.write_text("def escape():\n    return True\n", encoding="utf-8")

        from backend.tools.agent_tools.code_manipulation_tools import ReadASTNodeTool

        result = ReadASTNodeTool().execute(
            folder_id=folder.id,
            filepath="../outside.py",
            node_name="escape",
        )

        assert result.success is False
        assert "outside" in result.error


def test_read_ast_node_refuses_credential_file_names(app):
    with app.app_context():
        folder = Folder(name="Repo", path="Repo", is_repository=True)
        db.session.add(folder)
        db.session.commit()

        from pathlib import Path

        repo_root = Path(app.config["UPLOAD_FOLDER"]) / "Repo"
        repo_root.mkdir(parents=True)
        (repo_root / "credentials.py").write_text("TOKEN = 'x'\n", encoding="utf-8")

        from backend.tools.agent_tools.code_manipulation_tools import ReadASTNodeTool

        result = ReadASTNodeTool().execute(
            folder_id=folder.id, filepath="credentials.py", node_name="TOKEN",
        )

        assert result.success is False
        assert "Credential" in result.error

def test_dependency_graph_resolves_python_and_javascript_import_targets(app):
    with app.app_context():
        folder = Folder(name="Repo", path="Repo", is_repository=True)
        db.session.add(folder)
        db.session.flush()

        docs = [
            Document(
                filename="main.py",
                path="Repo/app/main.py",
                folder_id=folder.id,
                content="from app import util\nfrom app.services.worker import Worker\n",
                is_code_file=True,
            ),
            Document(
                filename="util.py",
                path="Repo/app/util.py",
                folder_id=folder.id,
                content="def helper():\n    return 1\n",
                is_code_file=True,
            ),
            Document(
                filename="worker.py",
                path="Repo/app/services/worker.py",
                folder_id=folder.id,
                content="class Worker:\n    pass\n",
                is_code_file=True,
            ),
            Document(
                filename="App.jsx",
                path="Repo/src/App.jsx",
                folder_id=folder.id,
                content="import helper from './utils/helper';\nconst widget = require('./widgets');\n",
                is_code_file=True,
            ),
            Document(
                filename="helper.js",
                path="Repo/src/utils/helper.js",
                folder_id=folder.id,
                content="export default function helper() { return true; }\n",
                is_code_file=True,
            ),
            Document(
                filename="index.ts",
                path="Repo/src/widgets/index.ts",
                folder_id=folder.id,
                content="export const widget = true;\n",
                is_code_file=True,
            ),
        ]
        db.session.add_all(docs)
        db.session.commit()

        from backend.services.repository_analysis_service import RepositoryAnalysisService

        graph = RepositoryAnalysisService.build_dependency_graph(folder.id)

        assert graph["Repo/app/main.py"] == [
            "Repo/app/services/worker.py",
            "Repo/app/util.py",
        ]
        assert graph["Repo/src/App.jsx"] == [
            "Repo/src/utils/helper.js",
            "Repo/src/widgets/index.ts",
        ]


def test_analyze_repository_task_registered_and_routes_to_default():
    """The toggle-repo async path dispatches analyze_repository_task; the worker
    must have it registered. Regression: the module used @celery.task with a
    top-level `from backend.celery_app import celery`, which hit a circular
    import during create_celery_app() and left the task UNregistered — the
    worker then rejected dispatches with 'Received unregistered task'. Fixed by
    switching to @shared_task. This pins both registration and 'default' routing.
    """
    from backend.celery_app import celery
    from backend.tasks.repo_analysis_tasks import analyze_repository_task  # noqa: F401

    name = "backend.tasks.repo_analysis_tasks.analyze_repository_task"
    assert name in celery.tasks, f"{name} not registered with the Celery app"

    route = celery.amqp.router.route({}, name, args=[1], kwargs={})
    queue = route.get("queue")
    queue_name = queue.name if hasattr(queue, "name") else str(queue)
    assert queue_name == "default", f"analyze_repository_task routed to {queue_name!r}, worker consumes 'default'"


class TestRepoIntelToolPinning:
    """The semantic tool selector under-ranks get_dependency_graph / read_ast_node
    for natural repo queries (only get_repository_map ranks in), so chat couldn't
    reach 2 of the 3 tools. _pin_repo_intel_tools force-includes the trio on
    repo-intent messages. These pin that behavior."""

    def _pin(self, message):
        from backend.services.unified_chat_engine import _pin_repo_intel_tools, REPO_INTEL_TOOLS
        # selected starts empty to prove the pin alone surfaces them
        return set(_pin_repo_intel_tools(message, [], list(REPO_INTEL_TOOLS) + ["read_code", "system_command"]))

    @pytest.mark.parametrize("message", [
        "give me an architectural map of my code repository",
        "in repo folder 746, what does main.py import or depend on?",
        "show me the source code of the Worker class in folder 746",
        "build the dependency graph of the repo",
    ])
    def test_repo_queries_pin_all_three_tools(self, message):
        from backend.services.unified_chat_engine import REPO_INTEL_TOOLS
        pinned = self._pin(message)
        for tool in REPO_INTEL_TOOLS:
            assert tool in pinned, f"{tool} not pinned for {message!r}"

    @pytest.mark.parametrize("message", [
        "play some music and turn up the volume",
        "what's the weather like today",
        "draft a reddit post about our launch",
    ])
    def test_non_repo_queries_pin_nothing(self, message):
        assert self._pin(message) == set()
