"""Settings > Training libraries: Unsloth, TRL and Datasets install into the
backend's Python only from the modal's click, hold every other installed
package where it is, leave out what the project removes or must not import,
come out again on Remove, and training jobs refuse while they are missing or
incomplete (a requirement of theirs unmet).

Seams: subprocess.Popen (pip), read by _pip at call time; _spawn (the
background thread), installed_versions, _requires, hardware_fit,
_record_path, _pip_check and _trainer_check on the service module;
subprocess.run for the two checks' own tests; importlib.metadata.version
for the job refusal. Nothing runs pip, reads this machine's hardware or
touches the network.
"""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask

from backend.models import TrainingJob, db
from backend.services import training_libraries as tl

PRACTICAL = {"practical": True, "reason": "NVIDIA GPU with 16 GB of memory.",
             "gpu": "test card", "vram_mb": 16311, "ram_gb": 64}
NOT_PRACTICAL = {"practical": False, "reason": "No GPU was found. Guaardvark's trainer needs an NVIDIA GPU.",
                 "gpu": None, "vram_mb": None, "ram_gb": 8}

# A stock environment as far as these tests care.
STOCK = {"torch": "2.6.0+cu124", "torchvision": "0.21.0+cu124", "transformers": "5.16.1",
         "peft": "0.20.0", "numpy": "1.26.4", "accelerate": "1.2.1", "fsspec": "2026.7.0",
         "typeguard": "2.13.3", "pip": "25.0.1", "legacy-thing": "not-a-version"}

# What a successful install leaves, on top of STOCK.
INSTALLED = {"unsloth": "2026.10.1", "unsloth-zoo": "2026.10.1", "trl": "1.13.0",
             "datasets": "4.8.5", "bitsandbytes": "0.50.2", "pyarrow": "25.0.1",
             "accelerate": "1.15.0", "fsspec": "2026.2.0", "typeguard": "4.6.0"}


class FakePip:
    """subprocess.Popen for `python -m pip ...`: records each call, prints the
    given lines, and applies what the call would do to `env`."""

    def __init__(self, env, outcomes=None, lines=None):
        self.env = env
        self.calls = []
        self.outcomes = list(outcomes or [])
        self.lines = list(lines or [])

    def __call__(self, cmd, **kwargs):
        args = cmd[3:]
        call = {"cmd": cmd, "args": args, "env": kwargs.get("env") or {}}
        for i, arg in enumerate(args):
            if arg == "-c":
                call.setdefault("constraints", []).append(Path(args[i + 1]).read_text())
        self.calls.append(call)
        code = self.outcomes.pop(0) if self.outcomes else 0
        if code == 0:
            if args[0] == "install" and "--no-deps" not in args:
                self.env.update({k: v for k, v in INSTALLED.items() if k not in ("unsloth", "unsloth-zoo")})
            elif args[0] == "install" and any(a.startswith("unsloth==") for a in args):
                self.env.update({"unsloth": INSTALLED["unsloth"], "unsloth-zoo": INSTALLED["unsloth-zoo"]})
            elif args[0] == "install":
                for spec in (a for a in args if "==" in a):
                    name, version = spec.split("==")
                    self.env[name] = version
            elif args[0] == "uninstall":
                for name in args[2:]:
                    self.env.pop(name, None)
        lines = [line + "\n" for line in self.lines]
        return SimpleNamespace(stdout=iter(lines), wait=lambda: code, kill=lambda: None, pid=1)


@pytest.fixture
def env(monkeypatch, tmp_path):
    """A fresh service state over a fake environment; returns its versions."""
    versions = dict(STOCK)
    monkeypatch.setattr(tl, "installed_versions", lambda: dict(versions))
    monkeypatch.setattr(tl, "_requires", lambda name: [])
    monkeypatch.setattr(tl, "hardware_fit", lambda: dict(PRACTICAL))
    monkeypatch.setattr(tl, "_record_path", lambda: tmp_path / "record.json")
    monkeypatch.setattr(tl, "_spawn", lambda target, *args, name: None)
    monkeypatch.setattr(tl, "_tokens", {})
    monkeypatch.setattr(tl, "_run", tl._idle_run())
    monkeypatch.setattr(tl, "_pip_check", lambda: set())
    monkeypatch.setattr(tl, "_trainer_check", lambda: {"ok": True, "problems": [], "cuda_device": "test card"})
    return versions


@pytest.fixture
def pip(env, monkeypatch):
    fake = FakePip(env)
    monkeypatch.setattr(tl.subprocess, "Popen", fake)
    return fake


# ---- what the modal lists ------------------------------------------------------------

def test_a_stock_install_lists_the_three_libraries_missing_with_sizes(env):
    status = tl.status()

    assert [(lib["id"], lib["version"], lib["state"]) for lib in status["libraries"]] == [
        ("unsloth", "2026.10.1", "missing"), ("trl", "1.13.0", "missing"), ("datasets", "4.8.5", "missing")]
    assert status["ready"] is False
    assert status["download_mb"] == pytest.approx(78 + 1.4 + 51)
    assert {c["name"] for c in status["changes"]} == {"accelerate", "fsspec", "typeguard"}
    assert {l["name"] for l in status["left_out"]} == {"xformers", "torchao"}
    assert status["removal"] == {"remove": [], "restore": {}}
    assert "plan_token" not in status


def test_the_plan_token_comes_only_when_asked_for(env):
    assert tl.status(with_plan_token=True)["plan_token"]


def test_installed_libraries_read_ready_and_other_versions_are_named(env):
    env.update(INSTALLED)
    env["trl"] = "1.12.0"
    status = tl.status()

    states = {lib["id"]: (lib["state"], lib["installed_version"]) for lib in status["libraries"]}
    assert states == {"unsloth": ("installed", "2026.10.1"), "trl": ("other_version", "1.12.0"),
                      "datasets": ("installed", "4.8.5")}
    assert status["ready"] is True
    assert status["download_mb"] == 0
    assert status["changes"] == []


def test_unmet_requirements_name_the_package_and_skip_what_is_left_out(env, monkeypatch):
    env.update(INSTALLED)
    env["accelerate"] = "1.2.1"
    env.pop("bitsandbytes")
    declared = {
        "unsloth": ["xformers>=0.0.27.post2", "torchao>=0.13.0", "bitsandbytes>=0.45.5",
                    "pytest; extra == 'dev'", "mlx==0.32.3; sys_platform == 'darwin' and platform_machine == 'arm64'"],
        "trl": ["accelerate>=1.4.0", "datasets>=4.7.0"],
    }
    monkeypatch.setattr(tl, "_requires", lambda name: declared.get(name, []))

    unmet = tl.unmet_requirements(env)

    assert unmet == ["unsloth 2026.10.1 needs bitsandbytes, which is not installed",
                     "trl 1.13.0 needs accelerate>=1.4.0 (1.2.1 installed)"]


def _accelerate_put_back(env, monkeypatch):
    """Every library installed, with accelerate below what TRL declares: what
    a start-up `pip install -r` left when the requirements pinned 1.2.1."""
    env.update(INSTALLED)
    env["accelerate"] = "1.2.1"
    monkeypatch.setattr(tl, "_requires", lambda name: ["accelerate>=1.4.0"] if name == "trl" else [])


INCOMPLETE = ("The training libraries are installed, but incomplete: trl 1.13.0 needs "
              "accelerate>=1.4.0 (1.2.1 installed). Open Settings > Training libraries and "
              "click Repair.")


def test_an_unmet_requirement_reads_installed_but_not_ready(env, monkeypatch):
    _accelerate_put_back(env, monkeypatch)

    status = tl.status()

    assert {lib["state"] for lib in status["libraries"]} == {"installed"}
    assert status["ready"] is False
    assert status["unmet"] == ["trl 1.13.0 needs accelerate>=1.4.0 (1.2.1 installed)"]
    assert status["download_mb"] == 0


# ---- only the click installs -------------------------------------------------------------

def test_install_refuses_without_a_plan_token(env, pip):
    for token in (None, "", "made-up", 42):
        with pytest.raises(tl.Refused) as refused:
            tl.start_install(token)
        assert refused.value.status == 403
        assert refused.value.code == "NEEDS_CLICK"
    assert pip.calls == []
    assert tl._run["state"] == "idle"


def test_a_plan_token_starts_one_run_only(env, monkeypatch):
    started = []
    monkeypatch.setattr(tl, "_spawn", lambda target, *args, name: started.append(name))
    token = tl.issue_plan_token()

    tl.start_install(token)
    assert started == ["training-libraries-install"]
    assert tl._run["state"] == "running"

    tl._run["state"] = "completed"
    with pytest.raises(tl.Refused) as refused:
        tl.start_install(token)
    assert refused.value.code == "NEEDS_CLICK"
    assert started == ["training-libraries-install"]


def test_an_expired_plan_token_is_refused(env, monkeypatch):
    token = tl.issue_plan_token()
    tl._tokens[token] = 0
    with pytest.raises(tl.Refused):
        tl.start_install(token)


def test_a_second_run_is_refused_while_one_is_running(env):
    tl.start_install(tl.issue_plan_token())
    with pytest.raises(tl.Refused) as refused:
        tl.start_install(tl.issue_plan_token())
    assert refused.value.code == "BUSY"


def test_a_machine_that_is_not_practical_installs_only_anyway(env, monkeypatch):
    started = []
    monkeypatch.setattr(tl, "hardware_fit", lambda: dict(NOT_PRACTICAL))
    monkeypatch.setattr(tl, "_spawn", lambda target, *args, name: started.append(args))

    with pytest.raises(tl.Refused) as refused:
        tl.start_install(tl.issue_plan_token())
    assert refused.value.code == "NOT_PRACTICAL"
    assert "No GPU was found" in str(refused.value)
    assert started == []

    tl.start_install(tl.issue_plan_token(), anyway=True)
    assert started == [(True,)]


def test_nothing_but_the_routes_starts_a_run():
    """No task, schedule or startup step can install: the only code that
    calls start_install or start_remove is the route behind the modal. The
    base-model Download (training_base_models) and the GGUF converter
    (training_gguf_converter) define their own pairs, called from the same
    routes file. Other installs define a start_install of their own, each
    called from its own route: the search reranker (utils/reranker.py) and
    Docling's PDF models (utils/docling_loader.py) from api/index_mgmt_api.py,
    the identity encoder (services/identity_encoder.py) and the Cast training
    adapter (services/cast_train_adapter.py) from api/cast_library_api.py."""
    backend = Path(tl.__file__).resolve().parents[1]
    skip = {"tests", "venv", ".venv", "node_modules", "__pycache__"}
    callers = set()
    for root, dirs, files in os.walk(backend):
        dirs[:] = [d for d in dirs if d not in skip]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = Path(root) / name
            text = path.read_text(encoding="utf-8", errors="replace")
            if "start_install(" in text or "start_remove(" in text:
                callers.add(path.relative_to(backend).as_posix())
    assert callers == {"api/training/routes.py", "services/training_libraries.py",
                       "services/training_base_models.py", "services/training_gguf_converter.py",
                       "api/index_mgmt_api.py", "utils/reranker.py", "utils/docling_loader.py",
                       "api/cast_library_api.py", "services/identity_encoder.py",
                       "services/cast_train_adapter.py"}


def test_start_up_requirements_keep_what_an_install_moves():
    """start.sh, heal_backend_venv.sh and the dependency reconciler run
    `pip install -r` on these files, which reinstalls a package whose version
    falls outside its line. A package the install moves must stay inside it,
    or the next start-up undoes the install and fine-tuning fails at step 1.
    A line and the range the install needs agree when some version at an
    inclusive bound of either one satisfies both."""
    backend = Path(tl.__file__).resolve().parents[1]
    declared = []
    for name in ("requirements-base.txt", "requirements.txt"):
        for line in (backend / name).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line and not line.startswith("-"):
                declared.append((name, Requirement(line)))
    checked = 0
    for package, (spec, _why) in tl.MAY_CHANGE.items():
        needed = SpecifierSet(spec)
        for source, req in declared:
            if canonicalize_name(req.name) != package:
                continue
            checked += 1
            edges = [Version(s.version) for s in (*needed, *req.specifier)
                     if s.operator in (">=", "<=", "==") and not s.version.endswith(".*")]
            assert any(needed.contains(v) and req.specifier.contains(v) for v in edges), (
                f"{source} declares {req}, which start-up would hold outside {package}{spec}")
    assert checked, "no package the install moves is declared; this test checks nothing"


# ---- the install ---------------------------------------------------------------------

def test_install_holds_installed_packages_and_leaves_out_xformers_and_torchao(env, pip):
    tl._claim("install", 130)
    tl._install(False)

    assert tl._run["state"] == "completed", tl._run["error"]
    assert tl._run["restart_needed"] is True
    first, second = pip.calls
    assert first["cmd"][:3] == [sys.executable, "-m", "pip"]
    assert first["args"][0] == "install" and "--no-deps" not in first["args"]
    assert "trl==1.13.0" in first["args"] and "datasets==4.8.5" in first["args"]
    assert set(tl.UNSLOTH_REQUIREMENTS) <= set(first["args"])
    hold = first["constraints"][-1].splitlines()
    for held in ("torch==2.6.0+cu124", "transformers==5.16.1", "peft==0.20.0", "numpy==1.26.4", "pip==25.0.1"):
        assert held in hold
    for free in ("accelerate", "fsspec", "typeguard", "unsloth", "trl", "datasets", "legacy-thing"):
        assert not any(line.startswith(f"{free}==") for line in hold), free
    assert second["args"][:1] == ["install"] and "--no-deps" in second["args"]
    assert {"unsloth==2026.10.1", "unsloth-zoo==2026.10.1"} <= set(second["args"])
    for call in pip.calls:
        assert not any("xformers" in a or "torchao" in a for a in call["args"])
        assert "PIP_CONSTRAINT" not in call["env"]


def test_install_records_what_it_added_and_moved(env, pip, tmp_path):
    tl._claim("install", 130)
    tl._install(False)

    record = json.loads((tmp_path / "record.json").read_text())
    assert {"unsloth", "unsloth-zoo", "trl", "datasets", "bitsandbytes", "pyarrow"} <= set(record["added"])
    assert record["changed"]["accelerate"] == {"from": "1.2.1", "to": "1.15.0"}
    assert record["changed"]["fsspec"] == {"from": "2026.7.0", "to": "2026.2.0"}
    assert "torch" not in record["changed"]


def test_a_failed_pip_pass_fails_the_run_and_goes_no_further(env, monkeypatch):
    fake = FakePip(env, outcomes=[1], lines=["ERROR: ResolutionImpossible: torch==2.6.0+cu124 is held"])
    monkeypatch.setattr(tl.subprocess, "Popen", fake)
    tl._claim("install", 130)
    tl._install(False)

    assert len(fake.calls) == 1
    assert tl._run["state"] == "failed"
    assert "exit 1" in tl._run["error"]
    assert "ERROR: ResolutionImpossible: torch==2.6.0+cu124 is held" in tl._run["log"]


def test_an_install_that_leaves_requirements_unmet_is_failed(env, pip, monkeypatch):
    monkeypatch.setattr(tl, "_requires", lambda name: ["accelerate>=9"] if name == "trl" else [])
    tl._claim("install", 130)
    tl._install(False)

    assert tl._run["state"] == "failed"
    assert "trl 1.13.0 needs accelerate>=9" in tl._run["error"]


def test_install_ends_with_the_trainer_check(env, pip):
    tl._claim("install", 130)
    tl._install(False)

    assert tl._run["state"] == "completed", tl._run["error"]
    assert tl._run["check"]["ok"] is True
    assert tl.status()["run"]["check"]["cuda_device"] == "test card"


def test_a_trainer_that_cannot_load_the_libraries_fails_the_install(env, pip, monkeypatch):
    monkeypatch.setattr(tl, "_trainer_check", lambda: {
        "ok": False, "problems": ["unsloth: ImportError: libcuda.so.1 not found"]})
    tl._claim("install", 130)
    tl._install(False)

    assert tl._run["state"] == "failed"
    assert "the trainer cannot load them: unsloth: ImportError" in tl._run["error"]


def test_only_pip_check_complaints_new_since_the_install_are_shown(env, pip, monkeypatch):
    old = "legacy-thing 1.0 requires missing-dep, which is not installed."
    answers = iter([
        {old},
        {old, "unsloth 2026.10.1 requires xformers, which is not installed.",
         "trl 1.13.0 has requirement accelerate>=1.4.0, but you have accelerate 1.2.1."},
    ])
    monkeypatch.setattr(tl, "_pip_check", lambda: next(answers))
    tl._claim("install", 130)
    tl._install(False)

    assert tl._run["pip_check_new"] == [
        "trl 1.13.0 has requirement accelerate>=1.4.0, but you have accelerate 1.2.1."]
    assert any(line.startswith("pip check (new since the install): trl") for line in tl._run["log"])


def test_the_trainer_check_runs_offline_without_a_token_from_a_scratch_folder(monkeypatch):
    import subprocess

    monkeypatch.setenv("HF_TOKEN", "hf_secret")
    seen = {}

    def run(cmd, **kwargs):
        seen.update(cmd=cmd, **kwargs)
        report = {"ok": True, "problems": [], "libraries": {"trl": "1.13.0"}}
        return subprocess.CompletedProcess(cmd, 0, stdout="Unsloth banner\n" + json.dumps(report) + "\n", stderr="")

    monkeypatch.setattr(tl.subprocess, "run", run)

    report = tl._trainer_check()

    assert report["libraries"]["trl"] == "1.13.0"
    assert seen["cmd"][0] == sys.executable and seen["cmd"][-2:] == ["check", "--json"]
    assert seen["cmd"][1].endswith("finetune_model.py")
    assert "HF_TOKEN" not in seen["env"] and seen["env"]["HF_HUB_OFFLINE"] == "1"
    assert "guaardvark-trainer-check-" in seen["cwd"]
    assert not Path(seen["cwd"]).exists()


def test_a_trainer_check_without_a_report_says_so(monkeypatch):
    import subprocess

    monkeypatch.setattr(tl.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 1, stdout="", stderr="Traceback\nModuleNotFoundError: No module named 'unsloth'"))
    report = tl._trainer_check()
    assert report["ok"] is False
    assert "exit 1): ModuleNotFoundError: No module named 'unsloth'" in report["problems"][0]


def test_pip_check_reads_its_complaints(monkeypatch):
    import subprocess

    monkeypatch.setattr(tl.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 0, stdout="No broken requirements found.\n", stderr=""))
    assert tl._pip_check() == set()
    monkeypatch.setattr(tl.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 1, stdout="a 1.0 requires b, which is not installed.\n", stderr=""))
    assert tl._pip_check() == {"a 1.0 requires b, which is not installed."}


def test_progress_follows_the_megabytes_pip_fetches(env):
    tl._claim("install", 100)
    tl._on_pip_line("Downloading pyarrow-25.0.1-cp312-cp312-manylinux_2_28_x86_64.whl (50.1 MB)", fetching=True)
    assert tl._run["progress"] == 5 + int(80 * 0.501)
    tl._on_pip_line("Using cached trl-1.13.0-py3-none-any.whl (1010 kB)", fetching=True)
    assert tl._run["fetched_mb"] == pytest.approx(51.11)
    tl._on_pip_line("Installing collected packages: pyarrow, trl", fetching=True)
    assert tl._run["progress"] == 85
    assert tl._run["phase"] == "Installing collected packages"


# ---- remove --------------------------------------------------------------------------

def _record(tmp_path, added, changed):
    (tmp_path / "record.json").write_text(json.dumps({"added": added, "changed": changed}))


def test_remove_uninstalls_what_the_install_added_and_puts_back_moved_versions(env, pip, tmp_path):
    env.update(INSTALLED)
    _record(tmp_path, {"unsloth": "2026.10.1", "bitsandbytes": "0.50.2", "pyarrow": "25.0.1"},
            {"accelerate": {"from": "1.2.1", "to": "1.15.0"}, "fsspec": {"from": "2026.7.0", "to": "2026.2.0"}})
    tl._claim("remove", 1)
    tl._remove()

    assert tl._run["state"] == "completed", tl._run["error"]
    uninstall, restore = pip.calls
    assert uninstall["args"][:2] == ["uninstall", "-y"]
    assert set(uninstall["args"][2:]) == {"unsloth", "unsloth-zoo", "trl", "datasets", "bitsandbytes", "pyarrow"}
    assert "--no-deps" in restore["args"]
    assert {"accelerate==1.2.1", "fsspec==2026.7.0"} <= set(restore["args"])
    assert env["accelerate"] == "1.2.1"
    assert not (tmp_path / "record.json").exists()


def test_remove_leaves_alone_a_package_moved_again_since(env, tmp_path):
    env.update(INSTALLED)
    env["accelerate"] = "1.16.0"
    _record(tmp_path, {}, {"accelerate": {"from": "1.2.1", "to": "1.15.0"}})

    assert tl.removal_plan()["restore"] == {}


def test_remove_without_a_record_takes_out_only_the_libraries(env):
    env.update(INSTALLED)
    assert tl.removal_plan()["remove"] == ["datasets", "trl", "unsloth", "unsloth-zoo"]


def test_remove_is_refused_when_nothing_is_installed(env):
    with pytest.raises(tl.Refused) as refused:
        tl.start_remove(tl.issue_plan_token())
    assert refused.value.code == "NOTHING_TO_REMOVE"


# ---- jobs refuse while the libraries are missing or incomplete ------------------------

def _missing(*names):
    def version(name):
        if name in names:
            raise tl.metadata.PackageNotFoundError(name)
        return "1.0"
    return version


def test_the_job_refusal_names_what_is_missing_and_where_to_install_it(env, monkeypatch):
    monkeypatch.setattr(tl.metadata, "version", _missing("unsloth", "trl", "datasets"))
    reason = tl.unavailable_reason()
    assert reason == ("Training needs unsloth, trl and datasets, which are not installed. "
                      "Install them in Settings > Training libraries.")

    monkeypatch.setattr(tl.metadata, "version", _missing("trl"))
    assert tl.unavailable_reason() == ("Training needs trl, which is not installed. "
                                       "Install it in Settings > Training libraries.")

    monkeypatch.setattr(tl.metadata, "version", _missing())
    assert tl.unavailable_reason() is None


def test_the_job_refusal_names_an_unmet_requirement_and_points_to_repair(env, monkeypatch):
    monkeypatch.setattr(tl.metadata, "version", _missing())
    _accelerate_put_back(env, monkeypatch)

    assert tl.unavailable_reason() == INCOMPLETE

    env["accelerate"] = "1.15.0"
    assert tl.unavailable_reason() is None


def test_jobs_wait_while_an_install_runs(env, monkeypatch):
    monkeypatch.setattr(tl.metadata, "version", _missing())
    tl._claim("install", 130)
    assert "Settings > Training libraries" in tl.unavailable_reason()


# ---- the routes ----------------------------------------------------------------------

@pytest.fixture
def client(env):
    from backend.api.training.routes import training_bp

    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.register_blueprint(training_bp)
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def _error(response):
    return response.get_json()["error"]


def test_the_status_route_hands_out_a_token_only_with_plan(client):
    plain = client.get("/api/training/libraries").get_json()["data"]
    planned = client.get("/api/training/libraries?plan=1").get_json()["data"]
    assert "plan_token" not in plain
    assert planned["plan_token"] in tl._tokens
    assert [lib["state"] for lib in planned["libraries"]] == ["missing", "missing", "missing"]


def test_the_install_route_needs_the_confirm_and_the_token(client):
    no_confirm = client.post("/api/training/libraries/install", json={})
    assert no_confirm.status_code == 403 and _error(no_confirm)["code"] == "NEEDS_CLICK"

    no_token = client.post("/api/training/libraries/install", json={"confirm": "install"})
    assert no_token.status_code == 403 and _error(no_token)["code"] == "NEEDS_CLICK"
    assert tl._run["state"] == "idle"

    token = client.get("/api/training/libraries?plan=1").get_json()["data"]["plan_token"]
    started = client.post("/api/training/libraries/install", json={"confirm": "install", "plan_token": token})
    assert started.status_code == 202
    assert started.get_json()["data"]["run"]["state"] == "running"


def test_the_install_route_passes_anyway_only_when_true(client, monkeypatch):
    monkeypatch.setattr(tl, "hardware_fit", lambda: dict(NOT_PRACTICAL))
    token = client.get("/api/training/libraries?plan=1").get_json()["data"]["plan_token"]
    refused = client.post("/api/training/libraries/install",
                          json={"confirm": "install", "plan_token": token, "anyway": "yes"})
    assert refused.status_code == 409 and _error(refused)["code"] == "NOT_PRACTICAL"

    token = client.get("/api/training/libraries?plan=1").get_json()["data"]["plan_token"]
    started = client.post("/api/training/libraries/install",
                          json={"confirm": "install", "plan_token": token, "anyway": True})
    assert started.status_code == 202


def test_install_and_remove_wait_for_a_running_training_job(client):
    db.session.add(TrainingJob(job_id="busy", name="busy", base_model="org/base", status="running"))
    db.session.commit()
    token = client.get("/api/training/libraries?plan=1").get_json()["data"]["plan_token"]
    for path, confirm in (("install", "install"), ("remove", "remove")):
        response = client.post(f"/api/training/libraries/{path}", json={"confirm": confirm, "plan_token": token})
        assert response.status_code == 409 and _error(response)["code"] == "TRAINING_RUNNING"
    assert tl._run["state"] == "idle"
    assert token in tl._tokens


def test_job_creation_refuses_while_the_libraries_are_missing(client, monkeypatch):
    monkeypatch.setattr(tl.metadata, "version", _missing("unsloth", "trl", "datasets"))
    response = client.post("/api/training/jobs", json={
        "name": "job", "base_model": "org/base", "dataset_id": 1, "start_immediately": True})

    assert response.status_code == 409
    assert _error(response)["code"] == "TRAINING_LIBRARIES_MISSING"
    assert "Settings > Training libraries" in _error(response)["message"]
    assert db.session.query(TrainingJob).count() == 0


def test_resume_refuses_while_the_libraries_are_missing(client, monkeypatch):
    monkeypatch.setattr(tl.metadata, "version", _missing("unsloth"))
    job = TrainingJob(job_id="old", name="old", base_model="org/base", status="failed",
                      pipeline_stage="training", is_resumable=True, checkpoint_path="/ckpt/checkpoint-10")
    db.session.add(job)
    db.session.commit()

    response = client.post(f"/api/training/jobs/{job.id}/resume")

    assert response.status_code == 409
    assert "Settings > Training libraries" in _error(response)["message"]
    db.session.expire_all()
    assert db.session.get(TrainingJob, job.id).status == "failed"


def test_create_start_and_resume_refuse_while_a_requirement_is_unmet(client, env, monkeypatch):
    monkeypatch.setattr(tl.metadata, "version", _missing())
    _accelerate_put_back(env, monkeypatch)
    pending = TrainingJob(job_id="later", name="later", base_model="org/base", dataset_id=1,
                          status="pending", pipeline_stage="pending")
    failed = TrainingJob(job_id="old", name="old", base_model="org/base", status="failed",
                         pipeline_stage="training", is_resumable=True, checkpoint_path="/ckpt/checkpoint-10")
    db.session.add_all([pending, failed])
    db.session.commit()

    created = client.post("/api/training/jobs", json={
        "name": "job", "base_model": "org/base", "dataset_id": 1, "start_immediately": True})
    started = client.post(f"/api/training/jobs/{pending.id}/start")
    resumed = client.post(f"/api/training/jobs/{failed.id}/resume")

    for response in (created, started, resumed):
        assert response.status_code == 409
        assert _error(response)["code"] == "TRAINING_LIBRARIES_MISSING"
        assert _error(response)["message"] == INCOMPLETE
    db.session.expire_all()
    assert db.session.query(TrainingJob).count() == 2
    assert db.session.get(TrainingJob, pending.id).status == "pending"
    assert db.session.get(TrainingJob, failed.id).status == "failed"


def test_other_hosts_cannot_install_or_remove():
    from backend.utils.auth_guard import _is_protected

    app = Flask(__name__)
    for path in ("/api/training/libraries/install", "/api/training/libraries/remove"):
        with app.test_request_context(path, method="POST"):
            assert _is_protected() is True, path
    with app.test_request_context("/api/training/libraries", method="GET"):
        assert _is_protected() is False
