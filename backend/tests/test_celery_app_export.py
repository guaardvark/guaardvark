"""backend.celery_app exports `celery`; nothing may import a `celery_app` name from it.

Four call sites did, every one inside a try/except that swallowed the
ImportError — so System Map dispatch, agent failure reporting and the
autoresearch corpus-changed signal silently never reached Celery.
"""
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
BAD = re.compile(r"from\s+backend\.celery_app\s+import\s+celery_app\b(?!\s+as)|import\s+celery_app\s*$")


def test_no_module_imports_a_celery_app_name():
    offenders = []
    for py in BACKEND.rglob("*.py"):
        if "tests" in py.parts or "venv" in py.parts:
            continue
        for lineno, line in enumerate(py.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if "backend.celery_app" in line and BAD.search(line):
                offenders.append(f"{py.relative_to(BACKEND)}:{lineno}: {line.strip()}")
    assert not offenders, "backend.celery_app exports `celery`, not `celery_app`:\n" + "\n".join(offenders)


def test_celery_app_module_defines_celery():
    src = (BACKEND / "celery_app.py").read_text(encoding="utf-8")
    assert re.search(r"^celery\s*=\s*create_celery_app\(\)", src, re.M)
